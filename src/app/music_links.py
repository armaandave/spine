"""Background tasks that link Spine's two music catalogs.

See docs/music/apple-music-catalog.md.

- ``fill_bridges`` ("Fill Apple album bridges", every 5 minutes, default queue): the
  MusicBrainz bridge of the Apple albums whose pages queued one
  (``apple_music.queue_bridge``): their release group, genres, first release date and
  exact Spotify link.
- ``link_genre_albums`` ("Link genre albums to Apple", hourly, featured queue): the
  Apple album of each genre page album, as tier-3 link rows for genre pages and
  featured lists.

Both do nothing while the Apple Music key isn't set.
"""

import logging
import re
import time
from collections import Counter, defaultdict
from datetime import timedelta
from itertools import batched
from urllib.parse import urlsplit, urlunsplit

import requests
from django.db.models import Max, Min, Q
from django.utils import timezone

from app import music_catalog
from app.models import AppleAlbum, Item, MediaTypes, MusicAlbumLink, MusicGenreAlbum, Sources
from app.providers import apple_music, musicbrainz, services

logger = logging.getLogger(__name__)

# The bridge task works at most a minute a run, spaces its MusicBrainz requests 3
# seconds apart on the shared MusicBrainz session and makes at most 1,500 a day, so
# the requests people are waiting on keep most of MusicBrainz's one a second.
BRIDGE_RUN_SECONDS = 60
BRIDGE_REQUEST_SPACING = 3
BRIDGE_DAILY_REQUESTS = 1500
BRIDGE_DAILY_KEY = "apple_music:bridge_requests:{day}"
# An album takes up to three requests, and isn't started without time for them.
BRIDGE_REQUESTS_PER_ALBUM = 3
# Albums taken from the queue per run: more than a minute's worth.
BRIDGE_BATCH = 25
SPOTIFY_HOST = "open.spotify.com"
# The genre link task works at most 10 minutes a run, most read-about albums first,
# and searches an album it couldn't match again after 30 days.
GENRE_LINK_RUN_SECONDS = 10 * 60
GENRE_LINK_RECHECK = timedelta(days=30)
GENRE_LINK_BATCH = 100
_LINKED = (MusicAlbumLink.Status.MATCHED, MusicAlbumLink.Status.APPLIED)


class _Stop(Exception):  # noqa: N818
    """The run ends here; what is left waits for the next one."""


class _MusicBrainzPacer:
    """Spaces the bridge's MusicBrainz requests and counts them against the day's budget."""

    def __init__(self, deadline, *, clock, sleep):
        self.deadline = deadline
        self.clock = clock
        self.sleep = sleep
        self.last = None
        self.requests = 0

    def call(self, fetch, *args):
        wait = 0 if self.last is None else BRIDGE_REQUEST_SPACING - (self.clock() - self.last)
        if self.clock() + max(wait, 0) > self.deadline:
            raise _Stop("out of time")
        if not _spend_daily_request():
            raise _Stop("today's MusicBrainz requests are spent")
        if wait > 0:
            self.sleep(wait)
        self.last = self.clock()
        self.requests += 1
        return fetch(*args)


def fill_bridges(*, clock=None, sleep=None):
    """Fill the MusicBrainz bridge of the albums album pages queued.

    Each album's ``AppleAlbum`` row gets its release group, MusicBrainz genres, first
    release date and exact Spotify link, and the check time, so it isn't queued again
    for a month:

    - from a matched or applied link row that names one of the album's IDs, with no
      MusicBrainz request when the row has genres and the genre table has the album;
    - else from its barcode, in up to three requests: the releases with that barcode
      (as 12 and 13 digits), keeping one whose artist and title match; that release's
      links and group; the group's genres, title and first release date. An album
      with no barcode is skipped.

    Then an Item that holds the album gets its genre and date filters again, with the
    bridge's genres, and the album's cached detail pages are dropped. An album
    MusicBrainz answers a lasting error for (a 404, say) is only checked; any other
    failed request ends the run, and the albums left stay queued. ``clock`` and
    ``sleep`` stand in for ``time.monotonic`` and ``time.sleep``.
    """
    if not apple_music.is_configured():
        return {"skipped": "Apple Music isn't configured"}
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    queue = apple_music.bridge_queue_key()
    album_ids = sorted(_text(member) for member in services.redis_db.srandmember(queue, BRIDGE_BATCH) or [])
    counts = Counter()
    rows = {row.apple_album_id: row for row in AppleAlbum.objects.filter(apple_album_id__in=album_ids)}
    now = timezone.now()
    todo = [album_id for album_id in album_ids if album_id in rows and apple_music.bridge_is_stale(rows[album_id], now)]
    done = [album_id for album_id in album_ids if album_id not in todo]
    if done:
        # Filled since they were queued, or never seen.
        services.redis_db.srem(queue, *done)
        counts["skipped"] += len(done)
    deadline = clock() + BRIDGE_RUN_SECONDS
    pacer = _MusicBrainzPacer(deadline, clock=clock, sleep=sleep)
    stopped = ""
    try:
        found = {str(album["id"]): album for album in apple_music.albums(todo, background=True)} if todo else {}
        for album_id in todo:
            if clock() + BRIDGE_REQUEST_SPACING * BRIDGE_REQUESTS_PER_ALBUM > deadline:
                raise _Stop("out of time")
            try:
                filled = _fill_bridge(rows[album_id], found.get(album_id), pacer)
            except services.ProviderAPIError as error:
                if not _lasting(error):
                    raise
                # MusicBrainz will answer the same next time: checked, so it isn't
                # queued again for a month, and the albums after it get their turn.
                logger.warning("Apple album %s has no MusicBrainz bridge: %s", album_id, error)
                rows[album_id].bridge_checked_at = timezone.now()
                rows[album_id].save(update_fields=["bridge_checked_at"])
                filled = False
            counts["filled" if filled else "unmatched"] += 1
            services.redis_db.srem(queue, album_id)
    except _Stop as reason:
        stopped = str(reason)
    except services.ProviderAPIError as error:
        logger.warning("Apple album bridges stopped: %s", error)
        stopped = "a request failed"
    return {**counts, "requests": pacer.requests, "queued": services.redis_db.scard(queue), "stopped": stopped}


def _lasting(error):
    """Return whether a MusicBrainz error would come back on every try: a 4xx but 429."""
    status = error.status_code or 0
    return (
        error.provider == Sources.MUSICBRAINZ.value
        and requests.codes.bad_request <= status < requests.codes.internal_server_error
        and status != requests.codes.too_many_requests
    )


def barcode_forms(upc):
    """Return an Apple barcode as MusicBrainz stores it: 12 digits (UPC-A), then 13 (EAN-13).

    Apple's barcodes have 12, 13 or 14 digits, padded with leading zeros. One that
    needs 13 digits has no 12-digit form.
    """
    digits = re.sub(r"\D", "", str(upc or "")).lstrip("0")
    if not digits or len(digits) > 13:  # noqa: PLR2004
        return []
    return [*([digits.zfill(12)] if len(digits) <= 12 else []), digits.zfill(13)]  # noqa: PLR2004


def _fill_bridge(row, album, pacer):
    """Fill one album's bridge; return whether MusicBrainz knew the album."""
    attributes = (album or {}).get("attributes") or {}
    link = _known_link(row)
    if link is not None:
        group, spotify_url = _group_from_link(link, pacer), link.spotify_url
    elif attributes:
        group, spotify_url = _group_from_barcode(attributes.get("upc") or row.upc, attributes, pacer)
    else:
        group, spotify_url = None, ""
    row.bridge_checked_at = timezone.now()
    fields = ["bridge_checked_at"]
    if group is not None:
        row.release_group_mbid = group["id"]
        row.same_title = bool(attributes) and apple_music.same_name(
            apple_music.album_title(attributes)[0],
            group["title"],
        )
        row.first_release_date = group["first_release_date"]
        row.genres = group["genres"]
        row.spotify_url = spotify_url or ""
        row.spotify_exact = bool(spotify_url)
        fields += ["release_group_mbid", "same_title", "first_release_date", "genres", "spotify_url", "spotify_exact"]
    row.save(update_fields=fields)
    if group is not None:
        _refresh_album(row, attributes)
    return group is not None


def _known_link(row):
    """Return the matched or applied link row naming any of the album's IDs, or None."""
    listing = AppleAlbum.objects.filter(canonical_id=row.canonical_id or row.apple_album_id)
    links = MusicAlbumLink.objects.filter(
        Q(apple_album_id=row.apple_album_id) | Q(apple_album_id__in=listing.values("apple_album_id")),
        status__in=_LINKED,
    )
    return min(
        links,
        key=lambda link: (link.status != MusicAlbumLink.Status.APPLIED, link.tier or 0, link.release_group_mbid),
        default=None,
    )


def _group_from_link(link, pacer):
    """Return a linked release group's title, date and genres: stored, else one request."""
    known = (
        MusicGenreAlbum.objects.filter(release_group_mbid=link.release_group_mbid)
        .values("title", "release_date")
        .first()
    )
    if link.genres and known:
        return {
            "id": link.release_group_mbid,
            "title": known["title"],
            "first_release_date": known["release_date"],
            "genres": list(link.genres),
        }
    return _group(pacer.call(musicbrainz.lookup_release_group_genres, link.release_group_mbid))


def _group_from_barcode(upc, attributes, pacer):
    """Return ``(group, Spotify link)`` for an album's barcode, or ``(None, "")``."""
    forms = barcode_forms(upc)
    if not forms:
        return None, ""
    releases = pacer.call(musicbrainz.search_releases_by_barcode, forms).get("releases") or []
    release = _pick_release(releases, attributes)
    if release is None:
        return None, ""
    details = pacer.call(musicbrainz.lookup_release_links, release["id"])
    group_id = (details.get("release-group") or release.get("release-group") or {}).get("id")
    if not group_id:
        return None, ""
    group = _group(pacer.call(musicbrainz.lookup_release_group_genres, group_id))
    return group, _spotify_album(details.get("relations"))


def _pick_release(releases, attributes):
    """Return the release the album is: its artist and title match, best score first.

    The barcode is the album's own, so a release titled like the album or like its
    group counts ("SOS Deluxe: LANA" is a release of SOS). One whose group has the
    album's title comes first.
    """
    title = apple_music.album_title(attributes)[0]
    artist = attributes.get("artistName")

    def names_group(release):
        return apple_music.same_title(title, (release.get("release-group") or {}).get("title"))

    fits = [
        release
        for release in releases
        if release.get("id")
        and _credited(release, artist)
        and (apple_music.same_title(title, release.get("title")) or names_group(release))
    ]
    return min(fits, key=lambda release: (not names_group(release), -_int(release.get("score"))), default=None)


def _credited(release, artist):
    credits = release.get("artist-credit") or []
    text = "".join(f"{credit.get('name') or ''}{credit.get('joinphrase') or ''}" for credit in credits)
    names = [text, *(credit.get("name") for credit in credits)]
    names += [(credit.get("artist") or {}).get("name") for credit in credits]
    return any(apple_music.same_name(name, artist) for name in names)


def _group(data):
    return {
        "id": str(data["id"]),
        "title": data.get("title") or "",
        "first_release_date": str(data.get("first-release-date") or "")[:10],
        "genres": [
            str(genre["name"]) for genre in data.get("genres") or [] if isinstance(genre, dict) and genre.get("name")
        ],
    }


def _spotify_album(relations):
    """Return a release's Spotify album link, or ""."""
    for relation in relations or []:
        parsed = urlsplit(str((relation.get("url") or {}).get("resource") or ""))
        if (parsed.hostname or "").lower() == SPOTIFY_HOST and parsed.path.startswith("/album/"):
            return urlunsplit(parsed._replace(scheme="https", query="", fragment=""))
    return ""


def _refresh_album(row, attributes):
    """Give the album's Item its filters again and drop its cached detail pages."""
    from api.services.filters import update_item_filter_metadata  # noqa: PLC0415
    from api.services.media import drop_music_detail_cache  # noqa: PLC0415

    item = Item.objects.filter(
        source=Sources.APPLE_MUSIC.value,
        media_type=MediaTypes.MUSIC.value,
        media_id=row.apple_album_id,
        season_number=None,
        episode_number=None,
    ).first()
    if item is not None and attributes:
        update_item_filter_metadata(item, apple_music.filter_metadata(row.apple_album_id, attributes, row))
    drop_music_detail_cache(Sources.APPLE_MUSIC.value, row.apple_album_id)


def _spend_daily_request():
    """Count one MusicBrainz request against today's budget; return whether it fits."""
    key = apple_music.redis_key(BRIDGE_DAILY_KEY.format(day=timezone.localdate().isoformat()))
    count = services.redis_db.incr(key)
    if count == 1:
        services.redis_db.expire(key, int(timedelta(days=2).total_seconds()))
    return count <= BRIDGE_DAILY_REQUESTS


def link_genre_albums(*, clock=None):
    """Link genre page albums to Apple Music albums, for genre pages and featured lists.

    Works at most 10 minutes a run, on the background Apple bucket, most read-about
    albums first. Each album with no link row, or whose unmatched row is the fill's
    own (``music_catalog.genre_fill_rows``) and is unchecked or a month old, is linked:

    1. through Wikidata's Apple Music ID, when the weekly genre refresh stored one,
       checked 100 at a time (``apple_music.match_album_ids``);
    2. else through Apple search (``apple_music.match_album``).

    It writes tier-3 ``matched`` rows (method ``wikidata`` or ``search``) or
    ``unmatched`` rows, with the album's title, cover and genres. It makes no
    MusicBrainz requests and never writes the genre table. A failed Apple request ends
    the run; the next one carries on from the link rows. ``clock`` stands in for
    ``time.monotonic``.
    """
    if not apple_music.is_configured():
        return {"skipped": "Apple Music isn't configured"}
    clock = clock or time.monotonic
    deadline = clock() + GENRE_LINK_RUN_SECONDS
    counts = Counter()
    stopped = ""
    try:
        for chunk in batched(_genre_link_candidates(), GENRE_LINK_BATCH):
            if clock() > deadline:
                raise _Stop("out of time")
            _link_chunk(chunk, counts, clock=clock, deadline=deadline)
    except _Stop as reason:
        stopped = str(reason)
    except services.ProviderAPIError as error:
        logger.warning("Linking genre albums to Apple Music stopped: %s", error)
        stopped = "a request failed"
    return {**counts, "stopped": stopped}


def _genre_link_candidates():
    """Return the genre albums to link, as dicts, most read about first."""
    recheck = music_catalog.genre_fill_rows().filter(
        Q(checked_at__isnull=True) | Q(checked_at__lt=timezone.now() - GENRE_LINK_RECHECK),
    )
    settled = MusicAlbumLink.objects.exclude(pk__in=recheck.values("pk")).values("release_group_mbid")
    return list(
        MusicGenreAlbum.objects.exclude(release_group_mbid__in=settled)
        .values("release_group_mbid")
        .annotate(
            album_title=Min("title"),
            album_artist=Min("artist"),
            reads=Max("page_views"),
            album_listeners=Max("listeners"),
        )
        .order_by("-reads", "-album_listeners", "release_group_mbid"),
    )


def _link_chunk(chunk, counts, *, clock, deadline):
    mbids = [album["release_group_mbid"] for album in chunk]
    links = {link.release_group_mbid: link for link in MusicAlbumLink.objects.filter(release_group_mbid__in=mbids)}
    genres = defaultdict(list)
    for mbid, genre in (
        MusicGenreAlbum.objects.filter(release_group_mbid__in=mbids)
        .order_by("genre")
        .values_list("release_group_mbid", "genre")
    ):
        genres[mbid].append(genre)
    # Wikidata's IDs that the weekly genre refresh stored and nobody has checked yet.
    waiting = {}
    for album in chunk:
        link = links.get(album["release_group_mbid"])
        if (
            link is not None
            and link.method == MusicAlbumLink.Method.WIKIDATA
            and link.checked_at is None
            and link.apple_album_id
        ):
            waiting[album["release_group_mbid"]] = (link.apple_album_id, album["album_title"], album["album_artist"])
    wikidata = apple_music.match_album_ids(waiting) if waiting else {}

    def save(mbid, match, method):
        failed_id = waiting[mbid][0] if mbid in waiting else ""
        fields = apple_music.link_defaults(match, method=method, genres=genres[mbid], apple_album_id=failed_id)
        if _save_link(mbid, links.get(mbid), fields):
            counts["matched" if match else "unmatched"] += 1

    # Wikidata's matches first: they need no more requests, so time running out
    # doesn't lose them.
    for mbid, match in wikidata.items():
        save(mbid, match, MusicAlbumLink.Method.WIKIDATA)
    for album in chunk:
        mbid = album["release_group_mbid"]
        if mbid in wikidata:
            continue
        if clock() > deadline:
            raise _Stop("out of time")
        save(mbid, apple_music.match_album(album["album_title"], album["album_artist"]), MusicAlbumLink.Method.SEARCH)


def _save_link(mbid, link, fields):
    """Write a genre fill row; return False when another writer got there first."""
    if link is None:
        _, created = MusicAlbumLink.objects.get_or_create(release_group_mbid=mbid, defaults=fields)
        return created
    return bool(music_catalog.genre_fill_rows().filter(pk=link.pk).update(**fields))


def _text(value):
    return value.decode() if isinstance(value, bytes) else str(value)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
