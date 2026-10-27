"""The paced metadata fill: bring every tracked or logged Item under the current metadata rules.

An Item is pending while its metadata_version is below CURRENT_METADATA_VERSION for its
type, its last temporary failure (metadata_attempted_at) is null or more than 7 days old,
it isn't manual, and some user tracks or logged it. Progress lives in the database, so a
deploy that kills the chain loses nothing: the hourly sweep starts it again.

The chain (app.tasks.fill_item_metadata) walks pending Items by id in chunks of about
30 s, music last, saving each Item in its own savepoint:

- TV is filled by show: one tv_with_seasons call covers 8 seasons and fills the show,
  its seasons, its episodes and the stored episode lists.
- Hardcover books are fetched about 50 at a time (hardcover.book_metadata_batch).
- MusicBrainz, Apple Music, Open Library, ComicVine and IGDB Items that already have
  what their provider gives (year, genres, a runtime where the type has one, themes for
  games) are only stamped, without a call.
- Apple Music albums never go to MusicBrainz: one call on Apple's background session
  (apple_music.album with its tracks) gives the album length, and the release date and
  Apple's broad genres only where the Item has none, so an album moved from MusicBrainz
  keeps its first-release date and detailed genres. Without the Apple Music key the
  provider is stopped for the run and nothing is marked.
- A 404 or 410, NotImplementedError, TypeError, ValueError or KeyError from the provider
  stamps the Item: asking again would give the same answer. The 10th such failure in a
  row (with nothing stored in between) stops the provider instead: that looks like our
  bug, not missing titles. Titles TMDB no longer lists and bad ids are stamped too.
- Any other HTTP error (400, 401, 403, 422, 429, 5xx ...), a network error, or a whole
  Hardcover batch failing sets metadata_attempted_at and stops that provider for the
  rest of the run; the others carry on. quota.Deferred (a spent budget, or a provider
  that asked us to wait) only stops the provider: the Items aren't at fault, so nothing
  is marked and the next run asks again.
- An error while storing, or any other error, sets metadata_attempted_at: retried in
  7 days.

The fill keeps its own per-provider counters in services.redis_db (the per-host limiters
are in memory and reset with every task), with the caps in CAPS. MusicBrainz shares its
IP with every environment on this Mac, so albums are spaced to one call every 3 s.

At most one chunk runs (the lock, held only while a chunk runs) and at most one waits
(the queued marker), both raw redis_db keys that survive deploys. The chain stops when a
pass makes no progress or only blocked providers remain.

The hourly sweep does nothing until the fill is enabled: `fill_item_metadata --apply
--queue`, or a full `--apply` with no --limit, --type or --user, sets the enabled key (a
raw redis_db key with no expiry). So a deploy never starts the backfill before the
ship's dry run and spot check; if Redis loses the key, the sweep just stops.
"""

import logging
import math
import time
import uuid
from collections import Counter
from datetime import timedelta
from functools import partial

import requests
from django.apps import apps
from django.conf import settings
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Q
from django.utils import timezone

from api.services import stats_cache
from api.services.item_metadata import CURRENT_METADATA_VERSION, store_item_metadata
from app import tv_listing
from app.mixins import collect_external_rating_item_ids
from app.models import (
    DiaryEntry,
    Episode,
    Item,
    ItemFilterFacet,
    MediaSeriesItem,
    MediaTypes,
    Sources,
)
from app.providers import apple_music, quota, services

logger = logging.getLogger(__name__)

# Pending Items whose last attempt failed for a temporary reason wait this long.
RETRY_AFTER = timedelta(days=7)
CHUNK_SECONDS = 30
PAGE_SIZE = 100
HARDCOVER_BATCH_SIZE = 50
# Daily budgets the fill may spend (quota.scope); featured syncs have their own.
DAILY_BUDGETS = {"hardcover": 500, "openlibrary": 1000}

# Calls per window: (calls, seconds). "overall" covers every provider but MusicBrainz,
# which is paced on its own below.
CAPS = {
    "overall": (2, 1),
    Sources.IGDB.value: (2, 1),
    Sources.MAL.value: (15, 60),
    Sources.OPENLIBRARY.value: (10, 60),
    Sources.HARDCOVER.value: (20, 60),
    Sources.COMICVINE.value: (60, 60 * 60),
    # Half of Apple's background bucket (2 per second), so its other tasks keep room.
    Sources.APPLE_MUSIC.value: (1, 1),
}
MUSICBRAINZ_SECONDS_PER_CALL = 3
# Rough calls per fetch: a TMDB film and its collection, Open Library's work, edition
# and authors, an album's release, recordings and covers, an Apple album with its
# tracks. Hardcover is per batch of 50, TV per 8 seasons of a show.
FETCH_CALLS = {
    Sources.TMDB.value: 2,
    Sources.MAL.value: 1,
    Sources.MANGAUPDATES.value: 1,
    Sources.IGDB.value: 1,
    Sources.HARDCOVER.value: 2,
    Sources.OPENLIBRARY.value: 4,
    Sources.COMICVINE.value: 1,
    Sources.MUSICBRAINZ.value: 5,
    Sources.APPLE_MUSIC.value: 1,
}

TV_TYPES = (MediaTypes.TV.value, MediaTypes.SEASON.value, MediaTypes.EPISODE.value)
# Types tracked through a Media row of the same name; episodes through Episode rows.
TRACKED_TYPES = (
    MediaTypes.TV.value,
    MediaTypes.SEASON.value,
    MediaTypes.MOVIE.value,
    MediaTypes.ANIME.value,
    MediaTypes.MANGA.value,
    MediaTypes.GAME.value,
    MediaTypes.BOOK.value,
    MediaTypes.COMIC.value,
    MediaTypes.MUSIC.value,
)
# Providers expensive enough that an Item with what they give is only stamped.
STAMP_ONLY_PROVIDERS = {
    Sources.MUSICBRAINZ.value,
    Sources.APPLE_MUSIC.value,
    Sources.OPENLIBRARY.value,
    Sources.COMICVINE.value,
    Sources.IGDB.value,
}
# Types whose provider gives a runtime (music: the album length).
RUNTIME_TYPES = {
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.SEASON.value,
    MediaTypes.EPISODE.value,
    MediaTypes.ANIME.value,
    MediaTypes.MUSIC.value,
}
# The dry run's rough guess at new series members per fetched Item without a series
# (about 30% are in a series of about 3).
SERIES_TYPES = (MediaTypes.MOVIE.value, MediaTypes.GAME.value, MediaTypes.BOOK.value)
NEW_SERIES_MEMBERS_PER_ITEM = 0.6

# A wait up to this long is slept through; a longer one skips the provider for now.
INLINE_WAIT_SECONDS = 2
INLINE_TRIES = 3
# A pass that only waited on paced providers starts again after at most this long;
# longer waits end the chain, and the hourly sweep starts it again.
MAX_COUNTDOWN = 120
MAX_IDLE_PASSES = 2
LOCK_TTL = 30 * 60
LOCK_RETRY_SECONDS = 60
QUEUED_TTL = 2 * 60 * 60
SWEEP_RECENT = timedelta(hours=48)
SWEEP_LIMIT = 100
SWEEP_SECONDS = 5 * 60

MAIN = "main"
MUSIC = "music"
STAMP = "stamp"
BLOCK = "block"
RETRY = "retry"
# HTTP statuses that mean the title is gone; every other status stops the provider.
GONE_STATUSES = {404, 410}
# Failures in a row (with nothing stored in between) whose last one stops the provider
# instead of stamping.
MAX_STAMP_STREAK = 10
# How long the command's --apply waits for a running chunk or sweep to let go of the lock.
LOCK_WAIT_SECONDS = SWEEP_SECONDS + 60
LOCK_POLL_SECONDS = 5


def _now():
    """Return the wall clock in seconds (patched in tests)."""
    return time.time()


def _sleep(seconds):
    """Sleep between provider calls (patched in tests)."""
    time.sleep(seconds)


def _redis():
    return services.redis_db


def _key(*parts):
    prefix = f"{settings.REDIS_PREFIX}:" if settings.REDIS_PREFIX else ""
    return prefix + "metadata_fill:" + ":".join(parts)


LOCK_KEY = "lock"
QUEUED_KEY = "queued"
ENABLED_KEY = "enabled"
MUSICBRAINZ_KEY = "musicbrainz_free_at"


def bump_stats_epoch():
    """Hook for the stats cache: called once when a fill chain ends having filled Items.

    Bumps the stats epoch, so every stats page is worked out again from the filled
    metadata (STAT-006).
    """
    stats_cache.bump_epoch()


# Selection


def _used_q(*, user=None, since=None):
    """Match Items some user (or `user`) tracks or logged; with `since`, only recently."""
    match = Q()
    for media_type in TRACKED_TYPES:
        rows = apps.get_model("app", media_type).objects.filter(item=OuterRef("pk"))
        if user is not None:
            rows = rows.filter(user=user)
        if since is not None:
            rows = rows.filter(created_at__gte=since)
        match |= Q(media_type=media_type) & Q(Exists(rows))
    viewings = Episode.objects.filter(item=OuterRef("pk"))
    if user is not None:
        viewings = viewings.filter(related_season__user=user)
    if since is not None:
        viewings = viewings.filter(created_at__gte=since)
    match |= Q(media_type=MediaTypes.EPISODE.value) & Q(Exists(viewings))
    logs = DiaryEntry.objects.filter(item=OuterRef("pk"))
    if user is not None:
        logs = logs.filter(user=user)
    if since is not None:
        logs = logs.filter(created_at__gte=since)
    return match | Q(Exists(logs))


def _outdated_q():
    match = Q(pk__in=[])
    for media_type, version in CURRENT_METADATA_VERSION.items():
        match |= Q(media_type=media_type, metadata_version__lt=version)
    return match


def pending_items(*, media_types=None, user=None, now=None):
    """Return the Items the fill still has to visit (subqueries only, no id lists)."""
    now = now or timezone.now()
    queryset = (
        Item.objects.exclude(source=Sources.MANUAL.value)
        .filter(_outdated_q())
        .filter(Q(metadata_attempted_at__isnull=True) | Q(metadata_attempted_at__lt=now - RETRY_AFTER))
        .filter(_used_q(user=user))
    )
    if media_types:
        queryset = queryset.filter(media_type__in=media_types)
    return queryset


def waiting_for_retry(*, media_types=None, user=None, now=None):
    """Return outdated Items whose last attempt failed less than 7 days ago."""
    now = now or timezone.now()
    queryset = (
        Item.objects.exclude(source=Sources.MANUAL.value)
        .filter(_outdated_q())
        .filter(metadata_attempted_at__gte=now - RETRY_AFTER)
        .filter(_used_q(user=user))
    )
    if media_types:
        queryset = queryset.filter(media_type__in=media_types)
    return queryset


def _with_facet_flags(queryset):
    facets = ItemFilterFacet.objects.filter(item=OuterRef("pk"))
    return queryset.annotate(
        has_genre=Exists(facets.filter(facet_type=ItemFilterFacet.FacetType.GENRE)),
        has_theme=Exists(facets.filter(facet_type=ItemFilterFacet.FacetType.THEME)),
    )


def _only_needs_stamp(item):
    """Return whether an expensive provider's Item already has what the provider gives."""
    if item.source not in STAMP_ONLY_PROVIDERS:
        return False
    if item.release_year is None or not item.has_genre:
        return False
    if item.media_type in RUNTIME_TYPES and item.runtime_minutes is None:
        return False
    # Themes are new for games, so a game without them still needs its fetch.
    return item.media_type != MediaTypes.GAME.value or item.has_theme


# Pacing


def _reserve(provider, calls):
    """Spend `calls` from the provider's and the overall windows.

    Return 0 when they were spent, or the seconds until a window has room. One fetch
    into an empty window always goes, even when it costs more than the cap.
    """
    client = _redis()
    now = _now()
    limits = [(provider, CAPS[provider])] if provider in CAPS else []
    limits.append(("overall", CAPS["overall"]))
    spent = []
    for name, (cap, seconds) in limits:
        window = int(now // seconds)
        key = _key("calls", name, str(seconds), str(window))
        count = client.incrby(key, calls)
        if count == calls:
            client.expire(key, seconds + 60)
        if count > cap and count != calls:
            for spent_key in [*spent, key]:
                client.decrby(spent_key, calls)
            return (window + 1) * seconds - now
        spent.append(key)
    return 0


def _musicbrainz_wait():
    value = _redis().get(_key(MUSICBRAINZ_KEY))
    return max(float(value) - _now(), 0) if value is not None else 0


def _musicbrainz_done(started):
    """Space the next album so the average stays at one call every 3 s.

    An album takes one second a call through MusicBrainz's own limiter, so waiting
    twice as long as it took (at least 3 s) keeps every environment's share low.
    """
    now = _now()
    pause = max(MUSICBRAINZ_SECONDS_PER_CALL, 2 * (now - started))
    _redis().set(_key(MUSICBRAINZ_KEY), now + pause, ex=int(pause) + 60)


# Errors


def classify(error):
    """Return STAMP (asking again won't help), BLOCK (stop the provider) or RETRY.

    Only a 404 or 410 stamps: a 400 or 422 for every title (a query we got wrong) must
    not stamp the whole backlog.
    """
    if isinstance(error, quota.Deferred):
        return BLOCK
    if isinstance(error, (services.ProviderAPIError, requests.RequestException)):
        status = getattr(error, "status_code", None)
        if status is None:
            status = getattr(getattr(error, "response", None), "status_code", None)
        return STAMP if status in GONE_STATUSES else BLOCK
    if isinstance(error, (NotImplementedError, TypeError, ValueError, KeyError)):
        return STAMP
    return RETRY


def _stamp(items):
    by_type = {}
    for item in items:
        by_type.setdefault(item.media_type, []).append(item.pk)
    for media_type, ids in by_type.items():
        Item.objects.filter(pk__in=ids).update(
            metadata_version=CURRENT_METADATA_VERSION.get(media_type, 0),
        )


def _mark_attempted(items):
    Item.objects.filter(pk__in=[item.pk for item in items]).update(
        metadata_attempted_at=timezone.now(),
    )


# One run of the walk


class _Run:
    """One chunk's work: what it did, which providers are blocked or waiting."""

    def __init__(self, *, blocked=None, media_types=None, user=None, limit=None, budget=CHUNK_SECONDS):
        self.blocked = dict(blocked or {})
        self.waiting = {}
        self.media_types = media_types
        self.user = user
        self.limit = limit
        self.deadline = _now() + budget
        self.counts = Counter()
        self.progress = 0
        self.after = 0
        self.handled_shows = set()
        self.hardcover = []
        self.stopped_for_musicbrainz = False
        self.stamp_streak = Counter()

    def pending(self):
        return pending_items(media_types=self.media_types, user=self.user)

    def out_of_time(self):
        return _now() >= self.deadline

    def limit_reached(self):
        return self.limit is not None and self.progress + len(self.hardcover) >= self.limit

    def turn(self, provider, calls):
        """Wait briefly for room under the caps; return False when the provider must wait longer."""
        wait = 0
        for _ in range(INLINE_TRIES):
            wait = _reserve(provider, calls)
            if wait <= 0:
                return True
            if wait > INLINE_WAIT_SECONDS or _now() + wait > self.deadline:
                break
            _sleep(wait)
        self.waiting[provider] = _now() + wait
        self.counts["skipped_waiting"] += 1
        return False

    def done(self, items, outcome):
        self.counts[outcome] += len(items)
        self.progress += len(items)

    def failed(self, provider, items, error, *, kind=None):
        """Handle a failed fetch or store: stamp, stop the provider, or retry in a week."""
        if isinstance(error, quota.Deferred):
            # A spent budget or a provider's wait: the Items aren't at fault, so nothing
            # is marked and the next run asks again.
            logger.warning("metadata fill: stopping %s for this run: %s", provider, error)
            self.blocked[provider] = str(error)[:200]
            self.counts["skipped_blocked"] += len(items)
            return
        kind = kind or classify(error)
        if kind == STAMP and self.stamp_streak[provider] + 1 >= MAX_STAMP_STREAK:
            # That many missing titles in a row looks like our bug: stop instead.
            logger.warning(
                "metadata fill: %s failed %s times in a row; stopping it instead of stamping",
                provider,
                MAX_STAMP_STREAK,
            )
            kind = BLOCK
        if kind == STAMP:
            self.stamp_streak[provider] += 1
            self.unfillable(items, error)
            return
        _mark_attempted(items)
        self.done(items, "retry_later")
        if kind == BLOCK:
            logger.warning("metadata fill: stopping %s for this run: %s", provider, error)
            self.blocked[provider] = str(error)[:200]
        else:
            logger.error(
                "metadata fill: could not fill %s: %s",
                ", ".join(str(item.pk) for item in items),
                error,
                exc_info=error,
            )

    def unfillable(self, items, reason):
        """Stamp Items the provider can't fill (a missing title, a bad id)."""
        logger.info("metadata fill: %s for %s; stamped", reason, ", ".join(str(item.pk) for item in items))
        _stamp(items)
        self.done(items, "not_found")

    def store(self, item, metadata):
        try:
            with transaction.atomic():
                stamped = store_item_metadata(item, metadata)
        except Exception as error:  # noqa: BLE001 - our bug, not the provider's: retry in a week
            self.failed(item.source, [item], error, kind=RETRY)
            return
        self.stamp_streak[item.source] = 0
        if stamped:
            self.done([item], "filled")
        else:
            # A failed fetch or an album without a length: try again in a week.
            _mark_attempted([item])
            self.done([item], "unstamped")


def _process(run, item):
    """Fill one Item (or its whole show); return False to stop the walk before it."""
    provider = item.source
    if provider in run.blocked:
        run.counts["skipped_blocked"] += 1
        return True
    if provider in run.waiting:
        run.counts["skipped_waiting"] += 1
        return True
    if item.media_type in TV_TYPES:
        _fill_show(run, item)
        return True
    if provider == Sources.HARDCOVER.value:
        run.hardcover.append(item)
        if len(run.hardcover) >= HARDCOVER_BATCH_SIZE:
            _flush_hardcover(run)
        return True
    if _only_needs_stamp(item):
        _stamp([item])
        run.done([item], "stamp_only")
        return True
    if provider == Sources.APPLE_MUSIC.value and not apple_music.is_configured():
        # Not the Items' fault: nothing is marked, and the next run asks again.
        run.blocked[provider] = "Apple Music isn't configured"
        run.counts["skipped_blocked"] += 1
        return True
    if provider == Sources.MUSICBRAINZ.value:
        wait = _musicbrainz_wait()
        if wait > 0:
            if _now() + wait > run.deadline:
                run.waiting[provider] = _now() + wait
                run.stopped_for_musicbrainz = True
                return False
            _sleep(wait)
    elif not run.turn(provider, FETCH_CALLS.get(provider, 1)):
        return True
    _fill_one(run, item)
    return True


def _fill_one(run, item):
    started = _now()
    try:
        if item.source == Sources.APPLE_MUSIC.value:
            metadata = _apple_music_metadata(item)
        else:
            metadata = services.get_media_metadata(item.media_type, item.media_id, item.source)
    except Exception as error:  # noqa: BLE001 - classified
        run.failed(item.source, [item], error)
    else:
        run.store(item, metadata)
    finally:
        if item.source == Sources.MUSICBRAINZ.value:
            _musicbrainz_done(started)


def _apple_music_metadata(item):
    """Return an Apple Music album's metadata from one background catalog call.

    The album length comes from its tracks (only when every track has a length). The
    release date and Apple's genres (minus the catch-all "Music") only fill what the
    Item lacks. Music genres stay outside the genre groups (genre_groups).
    """
    album = apple_music.album(item.media_id, background=True)
    attributes = album.get("attributes") or {}
    tracks = ((album.get("relationships") or {}).get("tracks") or {}).get("data") or []
    lengths = [(track.get("attributes") or {}).get("durationInMillis") for track in tracks]
    metadata = {}
    if lengths and all(isinstance(length, int) and length > 0 for length in lengths):
        metadata["runtime"] = max(1, round(sum(lengths) / 60000))
    if item.release_date is None and attributes.get("releaseDate"):
        metadata["release_date"] = attributes["releaseDate"]
    if not item.filter_facets.filter(facet_type=ItemFilterFacet.FacetType.GENRE).exists():
        genres = [name for name in attributes.get("genreNames") or [] if name != "Music"]
        if genres:
            metadata["genres"] = genres
    return metadata


def _flush_hardcover(run):
    items, run.hardcover = run.hardcover, []
    if not items:
        return
    provider = Sources.HARDCOVER.value
    if provider in run.blocked:
        run.counts["skipped_blocked"] += len(items)
        return
    bad = [item for item in items if not str(item.media_id).isdigit()]
    if bad:
        run.unfillable(bad, "not a Hardcover book id")
        items = [item for item in items if item not in bad]
    if not items or not run.turn(provider, FETCH_CALLS[provider]):
        return
    from app.providers import hardcover  # noqa: PLC0415

    try:
        results = hardcover.book_metadata_batch([item.media_id for item in items])
    except Exception as error:  # noqa: BLE001 - classified
        # One book can't fail a whole batch, so nothing here is stamped.
        kind = classify(error)
        run.failed(provider, items, error, kind=BLOCK if kind == STAMP else kind)
        return
    for item in items:
        metadata = results.get(str(item.media_id))
        if metadata is None:
            run.failed(provider, [item], KeyError(f"Hardcover has no book {item.media_id}"))
        else:
            run.store(item, metadata)


def _fill_show(run, item):
    """Fill every pending Item of the item's show from one fetch per 8 seasons."""
    key = (item.source, item.media_id)
    if key in run.handled_shows:
        return
    run.handled_shows.add(key)
    group = list(
        run.pending().filter(source=item.source, media_id=item.media_id, media_type__in=TV_TYPES).order_by("id"),
    )
    if not group:
        return
    provider = item.source
    numbers = sorted({
        member.season_number
        for member in group
        if member.media_type != MediaTypes.TV.value and member.season_number is not None
    })
    size = tv_listing.SEASONS_PER_REQUEST
    chunks = [numbers[start : start + size] for start in range(0, len(numbers), size)] or [None]
    if not run.turn(provider, len(chunks)):
        return
    metadata = {}
    try:
        for chunk in chunks:
            metadata.update(services.get_media_metadata("tv_with_seasons", item.media_id, item.source, chunk))
    except Exception as error:  # noqa: BLE001 - classified
        run.failed(provider, group, error)
        return

    show = Item.objects.filter(
        source=item.source,
        media_id=item.media_id,
        media_type=MediaTypes.TV.value,
        season_number__isnull=True,
        episode_number__isnull=True,
    ).first()
    if show is not None:
        try:
            with transaction.atomic():
                tv_listing.update_from_metadata(show, metadata)
        except Exception as error:  # noqa: BLE001 - a malformed listing never stops the chain
            run.failed(provider, group, error, kind=RETRY)
            return
    show_metadata = {name: value for name, value in metadata.items() if not name.startswith("season/")}
    for member in group:
        if member.media_type == MediaTypes.TV.value:
            run.store(member, show_metadata)
            continue
        try:
            season = metadata.get(f"season/{member.season_number}")
            if member.media_type == MediaTypes.SEASON.value:
                values = _season_metadata(season, show_metadata) if season else None
            else:
                values = _episode_metadata(season, member.episode_number) if season else None
        except Exception as error:  # noqa: BLE001 - a malformed season never stops the chain
            run.failed(provider, [member], error, kind=RETRY)
            continue
        if values is None:
            # Not a provider error: TMDB dropped the season or episode.
            run.unfillable([member], f"TMDB doesn't list {member}")
        else:
            run.store(member, values)


def _season_metadata(season, show_metadata):
    """A season's metadata, with the show's original language when it has none."""
    language = show_metadata.get("original_language")
    if language and not season.get("original_language"):
        return {**season, "original_language": language}
    return season


def _episode_metadata(season, episode_number):
    """An episode's air date and runtime from its season's list, or None when it isn't listed."""
    for episode in season.get("episodes") or []:
        if episode.get("episode_number") == episode_number:
            return {"release_date": episode.get("air_date"), "runtime": episode.get("runtime")}
    return None


def _walk(run, queryset):
    """Process the queryset by id from run.after; return True once it is exhausted."""
    while True:
        page = list(_with_facet_flags(queryset.filter(id__gt=run.after)).order_by("id")[:PAGE_SIZE])
        if not page:
            return True
        for item in page:
            if run.out_of_time() or run.limit_reached():
                return False
            try:
                carry_on = _process(run, item)
            except Exception as error:  # noqa: BLE001 - one Item never stalls the chain
                run.failed(item.source, [item], error, kind=RETRY)
                carry_on = True
            if not carry_on:
                return False
            run.after = item.id


def _walk_phases(run, queryset, phase):
    """Walk the main phase, then music; return the phase reached and whether it ended."""
    exhausted = False
    with quota.scope("metadata_fill", DAILY_BUDGETS), collect_external_rating_item_ids() as created:
        if phase == MAIN:
            exhausted = _walk(run, queryset.exclude(media_type=MediaTypes.MUSIC.value))
            _flush_hardcover(run)
            if exhausted:
                phase, run.after = MUSIC, 0
        if phase == MUSIC:
            exhausted = _walk(run, queryset.filter(media_type=MediaTypes.MUSIC.value))
        _flush_hardcover(run)
    if created:
        from app.tasks import enqueue_external_rating_batches  # noqa: PLC0415

        run.counts["new_series_members"] += len(created)
        transaction.on_commit(partial(enqueue_external_rating_batches, sorted(created)), robust=True)
    return phase, exhausted


# Chunks and the chain


def initial_state():
    return {
        "phase": MAIN,
        "after": 0,
        "pass": 1,
        "pass_progress": 0,
        "idle_passes": 0,
        "blocked": {},
        "waiting_until": {},
        "done": 0,
    }


def run_chunk(state, *, budget=CHUNK_SECONDS, media_types=None, user=None, limit=None):
    """Run one chunk from `state`; return (next state or None when the chain ends, countdown, run)."""
    state = {**initial_state(), **(state or {})}
    run = _Run(blocked=state["blocked"], media_types=media_types, user=user, limit=limit, budget=budget)
    run.after = state["after"]
    phase, exhausted = _walk_phases(run, run.pending(), state["phase"])

    waiting_until = {**state["waiting_until"], **run.waiting}
    state.update(
        phase=phase,
        after=run.after,
        blocked=run.blocked,
        waiting_until=waiting_until,
        pass_progress=state["pass_progress"] + run.progress,
        done=state["done"] + run.progress,
    )
    if not exhausted:
        if run.limit_reached():
            return None, 0, run
        countdown = 0
        if run.stopped_for_musicbrainz:
            countdown = max(math.ceil(waiting_until[Sources.MUSICBRAINZ.value] - _now()), 1)
        return state, countdown, run

    remaining = run.pending().exclude(source__in=list(run.blocked)).exists()
    if not remaining:
        return None, 0, run
    next_pass = {
        **state,
        "phase": MAIN,
        "after": 0,
        "pass": state["pass"] + 1,
        "pass_progress": 0,
        "waiting_until": {},
    }
    if state["pass_progress"] > 0:
        return {**next_pass, "idle_passes": 0}, 0, run
    # Nothing moved this pass: only paced providers are left (blocked ones are skipped).
    if waiting_until and state["idle_passes"] < MAX_IDLE_PASSES:
        wait = min(waiting_until.values()) - _now()
        if wait <= MAX_COUNTDOWN:
            return {**next_pass, "idle_passes": state["idle_passes"] + 1}, max(math.ceil(wait), 1), run
    return None, 0, run


def _acquire_lock():
    token = uuid.uuid4().hex
    if _redis().set(_key(LOCK_KEY), token, nx=True, ex=LOCK_TTL):
        return token
    return None


def _release_lock(token):
    client = _redis()
    held = client.get(_key(LOCK_KEY))
    if held is not None and held.decode() == token:
        client.delete(_key(LOCK_KEY))


def chain_status():
    """Return "running" (a chunk holds the lock), "queued" (one waits) or None."""
    client = _redis()
    if client.exists(_key(LOCK_KEY)):
        return "running"
    if client.exists(_key(QUEUED_KEY)):
        return "queued"
    return None


def schedule_next_chunk(state=None, countdown=0):
    """Queue the next chunk unless one is already queued; return whether it queued one."""
    from app.tasks import fill_item_metadata  # noqa: PLC0415

    state = state or initial_state()
    client = _redis()
    token = uuid.uuid4().hex
    if not client.set(_key(QUEUED_KEY), token, nx=True, ex=QUEUED_TTL):
        return False
    try:
        fill_item_metadata.apply_async(
            kwargs={"cursor": state["after"], "state": state, "token": token},
            countdown=countdown or None,
        )
    except Exception:
        client.delete(_key(QUEUED_KEY))
        logger.exception("metadata fill: could not queue the next chunk")
        return False
    return True


def start_chain():
    """Start the chain unless a chunk is running or queued; return whether it started."""
    if chain_status() == "running":
        return False
    return schedule_next_chunk()


def chain_chunk(cursor=0, state=None, token=None):
    """Run one chunk of the chain (the Celery task's body) and queue the next."""
    client = _redis()
    queued = client.get(_key(QUEUED_KEY))
    if queued is not None:
        if queued.decode() != (token or ""):
            logger.info("metadata fill: another chunk is queued; dropping this duplicate")
            return None
        client.delete(_key(QUEUED_KEY))
    state = {**initial_state(), **(state or {}), "after": cursor}
    lock = _acquire_lock()
    if lock is None:
        # The command's in-process fill or the sweep holds it: try again shortly.
        logger.info("metadata fill: another fill holds the lock; trying again in %s s", LOCK_RETRY_SECONDS)
        transaction.on_commit(partial(schedule_next_chunk, state, LOCK_RETRY_SECONDS))
        return None
    try:
        next_state, countdown, run = run_chunk(state)
    finally:
        _release_lock(lock)
    log_chunk(state, next_state, run, countdown)
    if next_state is not None:
        transaction.on_commit(partial(schedule_next_chunk, next_state, countdown))
    else:
        finish_chain(state["done"] + run.progress)
    return None


def finish_chain(done):
    if done:
        bump_stats_epoch()


def pending_counts(*, media_types=None, user=None):
    rows = (
        pending_items(media_types=media_types, user=user)
        .values("media_type")
        .annotate(count=Count("id"))
        .order_by("media_type")
    )
    return {row["media_type"]: row["count"] for row in rows}


def log_chunk(state, next_state, run, countdown, *, label=None, outcome=None):
    """Write the one progress line a chunk logs.

    What is still pending is counted only when the chain ends: the count scans every
    outdated Item, too much for every ~30 s chunk.
    """
    counts = run.counts
    pending_text = "counted when the chain ends"
    if next_state is None and outcome is None:
        pending = pending_counts(media_types=run.media_types, user=run.user)
        outcome = _outcome(next_state, run, countdown, pending)
        pending_text = "{} ({})".format(
            sum(pending.values()),
            ", ".join(f"{media_type} {count}" for media_type, count in pending.items()) or "none",
        )
    elif outcome is None:
        outcome = _outcome(next_state, run, countdown, {})
    logger.info(
        "metadata fill: %s %s items (filled %s, stamp-only %s, not found %s, unstamped %s, "
        "retry later %s, skipped %s), %s new series members; pending %s; blocked: %s; %s",
        label or f"pass {state.get('pass', 1)},",
        run.progress,
        counts["filled"],
        counts["stamp_only"],
        counts["not_found"],
        counts["unstamped"],
        counts["retry_later"],
        counts["skipped_blocked"] + counts["skipped_waiting"],
        counts["new_series_members"],
        pending_text,
        ", ".join(sorted(run.blocked)) or "none",
        outcome,
    )


def _outcome(next_state, run, countdown, pending):
    if next_state is None and run.limit_reached():
        return "stopped at the limit"
    if next_state is None:
        return "done" if not pending else "stopped (only blocked or waiting providers remain)"
    if countdown:
        return f"next chunk in {countdown} s"
    return "next chunk queued"


def fill_in_process(*, media_types=None, user=None, limit=None, write=None):
    """Run the fill in this process until it ends (the command's --apply); return its totals."""
    state = initial_state()
    totals = Counter()
    while state is not None:
        lock = _wait_for_lock()
        try:
            remaining = None if limit is None else limit - totals["items"]
            next_state, countdown, run = run_chunk(state, media_types=media_types, user=user, limit=remaining)
        finally:
            _release_lock(lock)
        log_chunk(state, next_state, run, countdown)
        totals.update(run.counts)
        totals["items"] += run.progress
        if write is not None:
            write(state, run)
        state = next_state
        if state is not None and countdown:
            _sleep(countdown)
    finish_chain(totals["items"])
    return totals


def _wait_for_lock():
    """Take the lock, waiting for a worker's chunk or sweep to let go of it."""
    deadline = _now() + LOCK_WAIT_SECONDS
    while True:
        lock = _acquire_lock()
        if lock is not None:
            return lock
        if _now() >= deadline:
            msg = f"A fill chunk or sweep has held the lock for over {LOCK_WAIT_SECONDS} s; try again later."
            raise RuntimeError(msg)
        _sleep(LOCK_POLL_SECONDS)


def enable():
    """Let the hourly sweep fill recent Items and resume the backlog from now on."""
    _redis().set(_key(ENABLED_KEY), "1")


def is_enabled():
    return bool(_redis().exists(_key(ENABLED_KEY)))


def sweep(now=None):
    """The hourly sweep: fill what was tracked or logged in the last 48 h, then resume the backlog.

    It does nothing until the fill is enabled (enable()).
    """
    if not is_enabled():
        logger.info("metadata fill: sweep skipped; start the fill with fill_item_metadata --apply --queue")
        return {"recent": 0, "chain_started": False, "enabled": False}
    now = now or timezone.now()
    recent = 0
    lock = _acquire_lock()
    if lock is not None:
        try:
            run = _Run(limit=SWEEP_LIMIT, budget=SWEEP_SECONDS)
            ids = list(
                pending_items(now=now)
                .filter(_used_q(since=now - SWEEP_RECENT))
                .order_by("-id")
                .values_list("id", flat=True)[:SWEEP_LIMIT],
            )
            if ids:
                _walk_phases(run, run.pending().filter(id__in=ids), MAIN)
                recent = run.progress
                log_chunk({}, None, run, 0, label="sweep: recent", outcome="sweep done")
        finally:
            _release_lock(lock)
    started = False
    if chain_status() is None and pending_items().exists():
        started = schedule_next_chunk()
    return {"recent": recent, "chain_started": started, "enabled": True}


# The dry run


def estimate(*, media_types=None, user=None):
    """Count what the fill would do, without any provider call.

    Returns {"rows": [...], "waiting_for_retry": n, "new_series_members": n,
    "minutes": n, "status": chain_status(), "enabled": is_enabled()}; each row has media_type, provider,
    pending, stamp_only, failed_before, calls and minutes.
    """
    rows = {}
    shows = {}
    fetched_without_series = 0
    queryset = _with_facet_flags(pending_items(media_types=media_types, user=user)).annotate(
        in_series=Exists(MediaSeriesItem.objects.filter(item=OuterRef("pk"))),
    )
    for item in queryset.iterator():
        row = rows.setdefault(
            (item.media_type, item.source),
            Counter(pending=0, stamp_only=0, failed_before=0, fetch=0),
        )
        row["pending"] += 1
        if item.metadata_attempted_at is not None:
            row["failed_before"] += 1
        if item.media_type in TV_TYPES:
            seasons = shows.setdefault((item.source, item.media_id), set())
            if item.media_type != MediaTypes.TV.value and item.season_number is not None:
                seasons.add(item.season_number)
        elif _only_needs_stamp(item):
            row["stamp_only"] += 1
        else:
            row["fetch"] += 1
            if item.media_type in SERIES_TYPES and not item.in_series:
                fetched_without_series += 1

    tv_calls = {}
    for (source, _media_id), seasons in shows.items():
        tv_calls[source] = tv_calls.get(source, 0) + max(
            math.ceil(len(seasons) / tv_listing.SEASONS_PER_REQUEST),
            1,
        )

    result_rows = []
    provider_calls = Counter()
    order = list(CURRENT_METADATA_VERSION)
    for (media_type, source), row in sorted(rows.items(), key=lambda pair: (order.index(pair[0][0]), pair[0][1])):
        if media_type in TV_TYPES:
            calls = tv_calls.pop(source, 0)
        elif source == Sources.HARDCOVER.value:
            calls = math.ceil(row["fetch"] / HARDCOVER_BATCH_SIZE) * FETCH_CALLS[source]
        else:
            calls = row["fetch"] * FETCH_CALLS.get(source, 1)
        provider_calls[source] += calls
        result_rows.append({
            "media_type": media_type,
            "provider": source,
            "pending": row["pending"],
            "stamp_only": row["stamp_only"],
            "failed_before": row["failed_before"],
            "calls": calls,
            "minutes": _minutes(source, calls),
        })

    musicbrainz = Sources.MUSICBRAINZ.value
    others = {source: calls for source, calls in provider_calls.items() if source != musicbrainz}
    overall_cap, overall_seconds = CAPS["overall"]
    minutes = max(
        [sum(others.values()) * overall_seconds / overall_cap / 60]
        + [_minutes(source, calls) for source, calls in others.items()],
    ) + _minutes(musicbrainz, provider_calls[musicbrainz])
    return {
        "rows": result_rows,
        "waiting_for_retry": waiting_for_retry(media_types=media_types, user=user).count(),
        "new_series_members": round(fetched_without_series * NEW_SERIES_MEMBERS_PER_ITEM),
        "minutes": round(minutes, 1),
        "status": chain_status(),
        "enabled": is_enabled(),
    }


def _minutes(provider, calls):
    if not calls:
        return 0
    if provider == Sources.MUSICBRAINZ.value:
        return round(calls * MUSICBRAINZ_SECONDS_PER_CALL / 60, 1)
    cap, seconds = CAPS.get(provider, CAPS["overall"])
    overall_cap, overall_seconds = CAPS["overall"]
    per_second = min(cap / seconds, overall_cap / overall_seconds)
    return round(calls / per_second / 60, 1)


def clear():
    """Delete the fill's counters, lock and marker (tests)."""
    client = _redis()
    for key in client.scan_iter(match=_key("*")):
        client.delete(key)
