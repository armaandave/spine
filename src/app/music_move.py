"""Moving existing music albums from MusicBrainz to Apple Music, and back.

The ``music_catalog`` management command runs these. See "Moving existing albums" in
docs/music/apple-music-catalog.md for the runbook.

- ``map_albums``: matches MusicBrainz albums that have Items to Apple Music albums.
  It writes only ``MusicAlbumLink`` rows (and, through the listing rule, the
  ``AppleAlbum`` rows of the albums it sees).
- ``report``: every album and what would happen to it, as CSV, and the counts.
- ``link``: records a manual pick.
- ``apply``: moves matched albums. An album's Item is re-keyed in place, so it keeps
  its pk and every row that points at it.
- ``revert``: undoes moves.
- ``check``: tests the Apple Music key and counts what needs attention; ``repair``
  joins stray MusicBrainz Items' rows to the album that moved.

Every step can run again: it skips what is done and carries on from what isn't. Each
album is its own transaction, so a killed run loses at most the album it was on.
"""

import csv
import logging
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from app import config, music_links, music_listing
from app.models import (
    AppleAlbum,
    DiaryEntry,
    Item,
    ItemFilterFacet,
    MediaLike,
    MediaTypes,
    Music,
    MusicAlbumLink,
    MusicGenreAlbum,
    Sources,
)
from app.providers import apple_music, musicbrainz, services
from lists.models import CustomListItem
from social.models import Activity, ProgressChange

logger = logging.getLogger(__name__)

MUSIC = MediaTypes.MUSIC.value
APPLE_MUSIC = Sources.APPLE_MUSIC.value
MUSICBRAINZ = Sources.MUSICBRAINZ.value
Status = MusicAlbumLink.Status
Method = MusicAlbumLink.Method
# map's MusicBrainz requests go out at least 3 seconds apart, leaving most of
# MusicBrainz's one a second (shared with the requests people are waiting on), and
# wait 30 seconds, then 60, when MusicBrainz is busy (503). Each attempt is one request:
# the MusicBrainz client's own quick retries are turned off.
MUSICBRAINZ_SPACING = 3
MUSICBRAINZ_ATTEMPTS = 3
MUSICBRAINZ_BACKOFF = 30
# MusicBrainz's answers for an album or release it doesn't have (removed, or a bad ID).
GONE_STATUSES = (400, 404)
RELEASE_PAGE = 100
MAX_RELEASES = 1000
# Barcodes have 12 digits (UPC-A) or 13 (EAN-13); MusicBrainz also has 14-digit ones
# padded with zeros. Compared as 13 digits.
BARCODE_DIGITS = 13
MIN_BARCODE_DIGITS = 12
# Tier 1 and tier 2 need a track count within one of MusicBrainz's usual count.
TRACK_SLACK = 1
# MusicBrainz names a clean or explicit copy in its disambiguation ("clean", "explicit
# version"); Apple's content ratings are "clean" and "explicit".
_CLEAN_NOTE = re.compile(r"\b(clean|edited|censored)\b", re.IGNORECASE)
_EXPLICIT_NOTE = re.compile(r"\bexplicit\b", re.IGNORECASE)
# ISRCs looked up when no barcode finds the album, and albums checked for them.
ISRC_LOOKUP = 25
ISRC_CANDIDATES = 10
# An unmatched album with no user data is searched again after this long.
RECHECK = timedelta(days=30)
# apply re-checks its work every this many albums.
APPLY_BATCH = 50
# The gate for switching the catalog to Apple Music: every genre page with 20 or more
# albums has 80% of them linked.
GENRE_GATE_ALBUMS = 20
GENRE_GATE_SHARE = 0.8
# The Apple Music album check makes one request for (SOS).
KEY_CHECK_ALBUM = "1657869377"
REDIRECT_TIERS = (1, 2)
# The Item fields a move changes, saved so revert can put them back.
SAVED_FIELDS = (
    "source",
    "media_id",
    "title",
    "image",
    "poster_accent_color",
    "release_date",
    "release_year",
    "filter_metadata_updated_at",
)
# revert's outcomes when users with rows on two Items keep them where they are.
KEPT_ON_BOTH = "Apple album kept: users have rows on both"
STRAY_KEPT = "reverted; users on both keep rows on a stray"
REPORT_COLUMNS = (
    "group",
    "item_id",
    "release_group_mbid",
    "title",
    "status",
    "method",
    "tier",
    "apple_album_id",
    "apple_title",
    "users",
    "kept_users",
    "note",
)


class MoveError(Exception):
    """A step can't run, or found a problem it must not carry on past."""


@dataclass(frozen=True)
class _UserTable:
    """One table of user rows that point at an Item: moved for a user, or not at all."""

    label: str
    model: type
    field: str
    user: str
    filters: dict = dataclass_field(default_factory=dict)


def user_tables():
    """Return the tables of user rows, in the order they move.

    Tracking, logs, likes, rows in people's own lists, activity and progress, and the
    Hall of Fame and profile backdrop picks. Featured list rows, cover picks,
    notification exclusions and rows derived from the provider (genre filters,
    calendar events, external ratings, series) aren't user rows: they stay.
    """
    user_model = get_user_model()
    return (
        _UserTable("app.music", Music, "item", "user"),
        _UserTable("app.diaryentry", DiaryEntry, "item", "user"),
        _UserTable("app.medialike", MediaLike, "item", "user"),
        _UserTable(
            "lists.customlistitem",
            CustomListItem,
            "item",
            "custom_list__owner",
            {"custom_list__is_featured": False},
        ),
        _UserTable("social.activity", Activity, "item", "actor"),
        _UserTable("social.progresschange", ProgressChange, "item", "actor"),
        _UserTable("users.user.hof_music", user_model, "hof_music", "pk"),
        _UserTable("users.user.profile_backdrop_item", user_model, "profile_backdrop_item", "pk"),
    )


# Rows that point at an Item but aren't user rows, as (table, field): they stay on
# the Item they are on. check counts rows in any table not here or in user_tables(),
# so a table added later shows up.
STAYING_ROWS = frozenset(
    {
        ("app.externalrating", "item"),
        ("app.itemfilterfacet", "item"),
        ("app.mediaseriesitem", "item"),
        ("app.musicalbumlink", "item"),
        ("app.customposterpreference", "item"),
        ("app.custombackdroppreference", "item"),
        ("app.customlogopreference", "item"),
        ("events.event", "item"),
        ("lists.customlistitem", "item"),
        ("users.user_notification_excluded_items", "item"),
    },
)


class _Pacer:
    """Spaces map's MusicBrainz requests, and waits when MusicBrainz is busy.

    ``fetch`` is called with ``max_attempts=1``, so each attempt sends one request.
    """

    def __init__(self, *, clock=None, sleep=None):
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.last = None
        self.requests = 0

    def musicbrainz(self, fetch, *args, **kwargs):
        for attempt in range(MUSICBRAINZ_ATTEMPTS):
            if self.last is not None:
                wait = MUSICBRAINZ_SPACING - (self.clock() - self.last)
                if wait > 0:
                    self.sleep(wait)
            self.last = self.clock()
            self.requests += 1
            try:
                return fetch(*args, **kwargs, max_attempts=1)
            except services.ProviderAPIError as error:
                busy = error.status_code == 503  # noqa: PLR2004
                if not busy or attempt == MUSICBRAINZ_ATTEMPTS - 1:
                    raise
                self.sleep(MUSICBRAINZ_BACKOFF * (attempt + 1))
        raise AssertionError("unreachable")


# ---------------------------------------------------------------------------
# User rows


def users_with_rows(item_pks):
    """Return ``{Item pk: {user IDs}}`` for the users who have rows on these Items."""
    users = defaultdict(set)
    item_pks = list(item_pks)
    if not item_pks:
        return users
    for table in user_tables():
        rows = table.model.objects.filter(**{f"{table.field}__in": item_pks, **table.filters})
        for item_pk, user_id in rows.values_list(table.field, table.user):
            users[item_pk].add(user_id)
    return users


def user_row_counts(item_pks):
    """Return ``{(table, user ID): rows}`` over these Items together."""
    counts = Counter()
    item_pks = list(item_pks)
    for table in user_tables():
        rows = table.model.objects.filter(**{f"{table.field}__in": item_pks, **table.filters})
        for user_id, rows_count in rows.values(table.user).annotate(rows=Count("pk")).values_list(table.user, "rows"):
            counts[(table.label, user_id)] += rows_count
    return counts


def _rows_per_item(item_pks):
    """Return ``{Item pk: user rows}``."""
    counts = Counter()
    item_pks = list(item_pks)
    for table in user_tables():
        rows = table.model.objects.filter(**{f"{table.field}__in": item_pks, **table.filters})
        for item_pk, rows_count in rows.values(table.field).annotate(rows=Count("pk")).values_list(table.field, "rows"):
            counts[item_pk] += rows_count
    return counts


def move_user_rows(source, target):
    """Move the rows of every user who has none on ``target`` from ``source`` to it.

    By foreign-key update, so no signal fires and no history is written, and every
    row keeps its pk. A user with rows on both keeps them all where they are: moving
    only some would part a tracking row from the log that holds its rating. Returns
    ``(moved, kept)``: each move as ``{"model", "pk", "from", "to"}``, for undoing it,
    and the IDs of the users kept on both.
    """
    on = users_with_rows([source.pk, target.pk])
    kept = on[source.pk] & on[target.pk]
    movable = on[source.pk] - kept
    moved = []
    if movable:
        for table in user_tables():
            rows = table.model.objects.filter(
                **{table.field: source, f"{table.user}__in": movable, **table.filters},
            )
            pks = sorted(rows.values_list("pk", flat=True))
            if not pks:
                continue
            table.model.objects.filter(pk__in=pks).update(**{table.field: target})
            moved += [{"model": table.label, "pk": pk, "from": source.pk, "to": target.pk} for pk in pks]
    return moved, sorted(kept)


def undo_moves(moved):
    """Put recorded moves back; return how many rows couldn't go back.

    A row that has gone, or whose old place is taken (its user has a row there now),
    stays where it is.
    """
    tables = {table.label: table for table in user_tables()}
    stayed = 0
    for move in reversed(moved or []):
        table = tables.get(move.get("model"))
        if table is None or not Item.objects.filter(pk=move.get("from")).exists():
            stayed += 1
            continue
        column = f"{table.field}_id"
        try:
            with transaction.atomic():
                changed = table.model.objects.filter(pk=move["pk"], **{column: move["to"]}).update(
                    **{column: move["from"]},
                )
        except IntegrityError:
            changed = 0
        stayed += 0 if changed else 1
    return stayed


# ---------------------------------------------------------------------------
# map


def map_albums(*, limit=None, clock=None, sleep=None):
    """Match the MusicBrainz albums that have Items to Apple Music albums.

    Writes only link rows, in three groups, in this order:

    1. Albums with user data, matched by barcode (``match_release_group``), whatever
       their row says, unless it is applied or a manual pick. Their rows get the
       Item's genres.
    2. Albums only featured lists hold, and 3. the other albums: their link row, else
       Apple search with an artist the database already knows, with no MusicBrainz
       requests. An unmatched one is searched again after 30 days.

    ``limit`` caps the albums looked at. An album MusicBrainz no longer has (a 404 or
    400) is ``unmatched``, checked now, and the run carries on. Any other failed request
    stops the run (after waiting out a busy MusicBrainz); the next run carries on, least
    recently checked first. ``clock`` and ``sleep`` stand in for ``time.monotonic`` and
    ``time.sleep``.
    """
    _require_key()
    pacer = _Pacer(clock=clock, sleep=sleep)
    today = timezone.localdate()
    counts = Counter({"looked at": 0})
    stopped = ""
    for item, group, link in _map_candidates(limit):
        counts["looked at"] += 1
        try:
            if group == 1:
                fields = match_release_group(item.media_id, item.title, pacer=pacer, today=today)
                fields["genres"] = _item_genres(item) or (list(link.genres) if link else [])
            else:
                fields = _match_listed(item, today=today)
                if fields is None:
                    counts[f"group {group}: no artist known"] += 1
                    continue
            outcome = _outcome(fields)
        except services.ProviderAPIError as error:
            if not _gone_from_musicbrainz(error):
                logger.warning("music_catalog map stopped at %s: %s", item.media_id, error)
                stopped = f"a {error.provider} request failed at {item.media_id}"
                break
            logger.warning("music_catalog map: MusicBrainz has no album %s: %s", item.media_id, error)
            fields = {**_row_fields(None, method=Method.SEARCH, tier=None), "genres": _item_genres(item)}
            outcome = "MusicBrainz has no such album"
        if _save_map_row(item.media_id, fields, group_one=group == 1):
            counts[f"group {group}: {outcome}"] += 1
    counts["MusicBrainz requests"] = pacer.requests
    return {**dict(sorted(counts.items())), "stopped": stopped}


def _gone_from_musicbrainz(error):
    """Return whether MusicBrainz answered that the album, or one of its releases, isn't there."""
    return error.provider == MUSICBRAINZ and error.status_code in GONE_STATUSES


def _map_candidates(limit):
    """Yield ``(item, group, link row)`` for map, group by group."""
    items = list(_musicbrainz_items())
    links = {
        link.release_group_mbid: link
        for link in MusicAlbumLink.objects.filter(release_group_mbid__in=[item.media_id for item in items])
    }
    grouped = _groups(items)
    recheck_before = timezone.now() - RECHECK
    candidates = []
    for item in items:
        link = links.get(item.media_id)
        group = grouped[item.pk]
        if link is not None and (link.status == Status.APPLIED or link.method == Method.MANUAL):
            continue
        if group != 1 and link is not None:
            fresh = link.checked_at is not None and link.checked_at > recheck_before
            if link.status != Status.UNMATCHED or fresh:
                continue
        checked = link.checked_at if link is not None and link.checked_at else None
        candidates.append((group, checked is not None, checked or timezone.now(), item.pk, item, link))
    candidates.sort(key=lambda candidate: candidate[:4])
    for group, _, _, _, item, link in candidates[:limit]:
        yield item, group, link


def _groups(items):
    """Return ``{Item pk: group}``: 1 with user data, 2 only in featured lists, 3 neither."""
    pks = [item.pk for item in items]
    with_users = set(users_with_rows(pks))
    featured = set(
        CustomListItem.objects.filter(item__in=pks, custom_list__is_featured=True).values_list("item", flat=True),
    )
    return {pk: 1 if pk in with_users else 2 if pk in featured else 3 for pk in pks}


def match_release_group(release_group_mbid, title, *, pacer, today):
    """Return a MusicBrainz album's link row fields from its barcodes, songs or name.

    1. **Barcodes.** Every Official release's barcode (12 digits or more, compared as
       13) is looked up on Apple Music, 25 a request, which answers each with its
       whole product family. An album passes when its title is the album's (" - EP",
       " - Single" and trailing bracketed tags left out), or contains it and the
       album's own barcode is one of MusicBrainz's; and when its artist is the
       album's (a credited name, or the artist's sort name in natural order, which
       covers most names in other scripts) or, failing that, its own barcode is one
       of MusicBrainz's and the title is the same. The pick prefers its own barcode
       over a family's, the same title, no edition words the album lacks, the track
       count nearest MusicBrainz's usual one, explicit then unrated then clean, and
       the lowest ID, and is then its listing's identity.
    2. **Songs.** With no barcode match: the ISRCs of one release, looked up on Apple
       Music, and the album that has the most of them, when one listing does.
    3. **Search** (``apple_music.match_album``).

    Tier 1 is the album's own barcode, the same title and a track count within one;
    tier 2 the same through the family only; both are ``matched``. Anything else
    found is tier 3 and ``review``; nothing is ``unmatched``. Two or three paced
    MusicBrainz requests: the release browse (cached for a day, shared with album
    pages) and a release's songs when no barcode matches.
    """
    releases = _releases(release_group_mbid, pacer)
    official = [release for release in releases if release.get("status") == "Official"]
    standard = _standard_track_count(official, title)
    names = _artist_names(releases)
    barcodes = {}
    for release in official:
        code = barcode13(release.get("barcode"))
        if code:
            barcodes.setdefault(code, []).append(release)
    found = _match_barcodes(barcodes, title, names, standard, today=today)
    if found is None:
        found = _match_songs(official or releases, title, names, standard, pacer=pacer, today=today)
    if found is None:
        artist = _credit_text((official or releases or [{}])[0])
        match = apple_music.match_album(title, artist) if artist else None
        found = (match, Method.SEARCH, 3, None) if match else None
    if found is None:
        return _row_fields(None, method=Method.SEARCH, tier=None)
    match, method, tier, members = found
    # The Spotify album is the one the page shows, not the pick: a clean twin's barcode
    # can find an explicit album's listing.
    album = music_listing.pick_content_member(members, today=today) if members else None
    spotify = _spotify_link(releases, album) if album is not None else ""
    return _row_fields(match, method=method, tier=tier, spotify_url=spotify)


def _match_barcodes(barcodes, title, names, standard, *, today):
    """Return ``(match, method, tier, members)`` for the listing the barcodes find, or None."""
    if not barcodes:
        return None
    candidates = {}
    for album in apple_music.albums_by_upc(sorted(barcodes), background=True):
        attributes = album.get("attributes") or {}
        album_id = music_listing.url_album_id(album)
        exact = barcode13(attributes.get("upc")) in barcodes
        equal = _same_title(attributes, title)
        contains = not equal and _contains_title(attributes, title)
        if not album_id or not (equal or (contains and exact)):
            continue
        if not (_artist_matches(attributes.get("artistName"), names) or (exact and equal)):
            continue
        entry = candidates.setdefault(album_id, {"album": album, "exact": False, "equal": equal, "copies": []})
        entry["exact"] |= exact
        entry["copies"].append(album)
        if str(album.get("id")) == album_id:
            # An old duplicate ID's resource stands in only until the album's own comes.
            entry["album"] = album
    if not candidates:
        return None
    album_id, entry = min(candidates.items(), key=lambda pair: _pick_key(pair[0], pair[1], title, standard))
    album = entry["album"]
    # Its old duplicate IDs too, so they are recorded as aliases of the album.
    listings = apple_music._listings_with_families(entry["copies"], today=today, background=True)
    identity, members = listings[str(album["id"])]
    close = _close_track_count(album, standard)
    if entry["exact"] and entry["equal"] and close:
        tier = 1
    elif entry["equal"] and close:
        tier = 2
    else:
        tier = 3
    method = Method.UPC_EXACT if entry["exact"] else Method.UPC_FAMILY
    return apple_music._match(identity, members, today=today), method, tier, members


def _match_songs(releases, title, names, standard, *, pacer, today):
    """Return ``(match, "isrc", 3, members)`` for the listing that has a release's songs."""
    release = min(
        releases,
        key=lambda release: (
            not apple_music.same_name(release.get("title"), title),
            standard is not None and _release_track_count(release) != standard,
            not _digital(release),
            str(release.get("date") or "9999"),
            str(release.get("id") or ""),
        ),
        default=None,
    )
    if release is None or not release.get("id"):
        return None
    detail = pacer.musicbrainz(musicbrainz.lookup_release, release["id"])
    isrcs = []
    for medium in detail.get("media") or []:
        for track in medium.get("tracks") or []:
            for isrc in ((track.get("recording") or {}).get("isrcs") or [])[:1]:
                if isrc not in isrcs:
                    isrcs.append(isrc)
    if not isrcs:
        return None
    shared = defaultdict(set)
    for song in apple_music.songs_by_isrc(isrcs[:ISRC_LOOKUP], background=True):
        isrc = str((song.get("attributes") or {}).get("isrc") or "")
        for entry in ((song.get("relationships") or {}).get("albums") or {}).get("data") or []:
            if entry.get("id") and isrc:
                shared[str(entry["id"])].add(isrc)
    if not shared:
        return None
    ranked = sorted(shared, key=lambda album_id: (-len(shared[album_id]), music_listing._id_order(album_id)))
    batch = apple_music.albums(
        ranked[:ISRC_CANDIDATES],
        views=(music_listing.FAMILY_VIEW,),
        background=True,
    )
    kept = []
    for album in batch:
        attributes = album.get("attributes") or {}
        if (
            attributes.get("isCompilation")
            or not _same_title(attributes, title)
            or not _artist_matches(attributes.get("artistName"), names)
            or not _close_track_count(album, standard)
        ):
            continue
        kept.append(album)
    if not kept:
        return None
    most = max(len(shared.get(str(album["id"]), ())) for album in kept)
    best = [album for album in kept if len(shared.get(str(album["id"]), ())) == most]
    listings = {}
    for identity, members in apple_music.remember_listings(
        [member for album in best for member in apple_music._family(album)],
        today=today,
    ):
        for album_id in apple_music._member_ids(members):
            listings.setdefault(album_id, (identity, members))
    identities = {listings[str(album["id"])][0] for album in best}
    if len(identities) != 1:
        return None
    identity, members = listings[str(best[0]["id"])]
    return apple_music._match(identity, members, today=today), Method.ISRC, 3, members


def _match_listed(item, *, today):  # noqa: ARG001
    """Return the link row fields for an album with no user data, or None.

    Apple search, with the artist the genre table or the cached MusicBrainz album
    knows; None when neither does, since no MusicBrainz request is made for it.
    """
    artist = _known_artist(item.media_id)
    if not artist:
        return None
    match = apple_music.match_album(item.title, artist)
    fields = apple_music.link_defaults(match, method=Method.SEARCH, genres=_item_genres(item))
    fields.pop("checked_at")
    fields.setdefault("spotify_url", "")
    return fields


def _known_artist(release_group_mbid):
    known = (
        MusicGenreAlbum.objects.filter(release_group_mbid=release_group_mbid)
        .exclude(artist="")
        .values_list("artist", flat=True)
        .first()
    )
    if known:
        return known
    group = musicbrainz.cached_release_group(release_group_mbid)
    return _credit_text(group) if group else ""


def _row_fields(match, *, method, tier, spotify_url=""):
    """Return map's link row fields; tier 3 is a review for an album with user data."""
    if match is None:
        return {
            "status": Status.UNMATCHED,
            "method": method,
            "tier": None,
            "apple_album_id": "",
            "apple_title": "",
            "apple_image": "",
            "spotify_url": "",
        }
    return {
        "status": Status.MATCHED if tier in REDIRECT_TIERS else Status.REVIEW,
        "method": method,
        "tier": tier,
        "apple_album_id": match["media_id"],
        "apple_title": match["title"],
        "apple_image": match["image"],
        "spotify_url": spotify_url,
    }


def _save_map_row(release_group_mbid, fields, *, group_one):
    """Write map's row, unless the album was moved, picked or matched meanwhile."""
    with transaction.atomic():
        link = MusicAlbumLink.objects.select_for_update().filter(release_group_mbid=release_group_mbid).first()
        if link is not None and (link.status == Status.APPLIED or link.method == Method.MANUAL):
            return False
        if link is not None and not group_one and link.status != Status.UNMATCHED:
            return False
        MusicAlbumLink.objects.update_or_create(
            release_group_mbid=release_group_mbid,
            defaults={**fields, "checked_at": timezone.now()},
        )
    return True


def _outcome(fields):
    if fields["status"] == Status.UNMATCHED:
        return "unmatched"
    return f"{fields['status']} tier {fields['tier']}"


def _releases(release_group_mbid, pacer):
    releases = []
    offset = 0
    while offset < MAX_RELEASES:
        page = pacer.musicbrainz(musicbrainz.browse_releases, release_group_mbid, limit=RELEASE_PAGE, offset=offset)
        batch = page.get("releases") or []
        releases.extend(batch)
        offset += len(batch)
        if not batch or offset >= _int(page.get("release-count", page.get("count", offset))):
            break
    return releases


def barcode13(value):
    """Return a barcode as 13 digits, or "" when it has fewer than 12.

    MusicBrainz and Apple Music store 12-digit UPCs, 13-digit EANs and 14-digit codes
    padded with zeros; ``00602547582812`` and ``602547582812`` are one barcode.
    """
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < MIN_BARCODE_DIGITS:
        return ""
    digits = digits.lstrip("0").zfill(BARCODE_DIGITS)
    return digits if len(digits) == BARCODE_DIGITS else ""


def _same_title(attributes, title):
    return apple_music.same_title(apple_music.album_title(attributes)[0], title)


def _contains_title(attributes, title):
    wanted = apple_music._loose(title)
    return bool(wanted) and wanted in apple_music._loose(apple_music.album_title(attributes)[0])


def _artist_names(releases):
    """Return every way MusicBrainz credits the album's artists, without case or accents.

    The credit as written, each credited name and artist name, and each artist's sort
    name in natural order: "Utada, Hikaru" for 宇多田ヒカル reads "Hikaru Utada".
    """
    names = set()
    for release in releases:
        credits = release.get("artist-credit") or []
        names.add(_credit_text(release))
        for credit in credits:
            artist = credit.get("artist") or {}
            names.update((credit.get("name"), artist.get("name"), artist.get("sort-name")))
            last, comma, first = str(artist.get("sort-name") or "").partition(",")
            if comma:
                names.add(f"{first} {last}")
    return {loose for name in names if (loose := apple_music._loose(name))}


def _artist_matches(artist, names):
    return bool(apple_music._loose(artist)) and apple_music._loose(artist) in names


def _credit_text(entity):
    credits = (entity or {}).get("artist-credit") or []
    return "".join(f"{credit.get('name') or ''}{credit.get('joinphrase') or ''}" for credit in credits).strip()


def _pick_key(album_id, entry, title, standard):
    attributes = entry["album"].get("attributes") or {}
    name = apple_music.album_title(attributes)[0]
    tagged = bool(apple_music._EDITION_TAGS.search(name)) and not apple_music._EDITION_TAGS.search(str(title or ""))
    tracks = _int(attributes.get("trackCount"))
    rating = {"explicit": 0, "clean": 2}.get(attributes.get("contentRating"), 1)
    return (
        not entry["exact"],
        not entry["equal"],
        tagged,
        abs(tracks - standard) if standard else 0,
        rating,
        music_listing._id_order(album_id),
    )


def _close_track_count(album, standard):
    tracks = _int((album.get("attributes") or {}).get("trackCount"))
    return standard is not None and abs(tracks - standard) <= TRACK_SLACK


def _standard_track_count(releases, title):
    """Return the track count most Official releases titled like the album have."""
    counts = Counter(
        count
        for release in releases
        if apple_music.same_name(release.get("title"), title) and (count := _release_track_count(release))
    )
    if not counts:
        counts = Counter(count for release in releases if (count := _release_track_count(release)))
    if not counts:
        return None
    most = max(counts.values())
    return min(count for count, times in counts.items() if times == most)


def _release_track_count(release):
    return sum(_int(medium.get("track-count")) for medium in release.get("media") or [])


def _digital(release):
    media = release.get("media") or []
    return bool(media) and all(medium.get("format") == "Digital Media" for medium in media)


def _spotify_link(releases, album):
    """Return the exact Spotify album: the release with the album's barcode, else one alike.

    Alike is the same title and track count, and not a clean or explicit copy when the
    album is the other; one with the album's own rating comes first. From a release
    browse, which has every release's links. The album bridge uses it too.
    """
    attributes = album.get("attributes") or {}
    upc = barcode13(attributes.get("upc"))
    tracks = _int(attributes.get("trackCount"))
    rating = attributes.get("contentRating") or None
    same_barcode = [release for release in releases if upc and barcode13(release.get("barcode")) == upc]
    alike = [
        release
        for release in releases
        if _same_title(attributes, release.get("title"))
        and _release_track_count(release) == tracks
        and not (rating and _content_rating(release) not in {None, rating})
    ]
    alike.sort(key=lambda release: _content_rating(release) != rating)
    for release in [*same_barcode, *alike]:
        link = music_links._spotify_album(release.get("relations"))
        if link:
            return link
    return ""


def _content_rating(release):
    """Return "clean" or "explicit" when a release's disambiguation says so, else None."""
    note = str(release.get("disambiguation") or "")
    if _CLEAN_NOTE.search(note):
        return "clean"
    if _EXPLICIT_NOTE.search(note):
        return "explicit"
    return None


# ---------------------------------------------------------------------------
# link


def link(release_group_mbid, apple_album_id):
    """Record a manual pick: this MusicBrainz album is that Apple Music album.

    The Apple album is checked with one request and stored as its listing's identity.
    An album that has moved must be reverted first.
    """
    _require_key()
    release_group_mbid = str(release_group_mbid).strip().lower()
    apple_album_id = str(apple_album_id).strip()
    if not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", release_group_mbid):
        raise MoveError(f"Not a MusicBrainz release group ID: {release_group_mbid}")
    if not apple_album_id.isdigit():
        raise MoveError(f"Not an Apple Music album ID: {apple_album_id}")
    row = MusicAlbumLink.objects.filter(release_group_mbid=release_group_mbid).first()
    if row is not None and row.status == Status.APPLIED:
        raise MoveError(f"{release_group_mbid} has moved: revert it first")
    today = timezone.localdate()
    found = apple_music.albums([apple_album_id], views=(music_listing.FAMILY_VIEW,), background=True)
    album = next(
        (album for album in found if apple_album_id in {str(album.get("id")), music_listing.url_album_id(album)}),
        None,
    )
    if album is None:
        raise MoveError(f"Apple Music has no album {apple_album_id}")
    listing = next(
        (identity, members)
        for identity, members in apple_music.remember_listings(apple_music._family(album), today=today)
        if str(album["id"]) in apple_music._member_ids(members)
    )
    match = apple_music._match(*listing, today=today)
    item = _musicbrainz_items().filter(media_id=release_group_mbid).first()
    same_album = row is not None and row.apple_album_id == match["media_id"]
    MusicAlbumLink.objects.update_or_create(
        release_group_mbid=release_group_mbid,
        defaults={
            "status": Status.MATCHED,
            "method": Method.MANUAL,
            "tier": None,
            "apple_album_id": match["media_id"],
            "apple_title": match["title"],
            "apple_image": match["image"],
            "genres": (_item_genres(item) if item else []) or (list(row.genres) if row else []),
            "spotify_url": row.spotify_url if same_album else "",
            "checked_at": timezone.now(),
        },
    )
    return {"linked": 1, "apple album": match["media_id"]}


# ---------------------------------------------------------------------------
# apply


def apply(*, limit=None):
    """Move matched albums to Apple Music, one album per transaction.

    Moved: tier 1, tier 2 and manual matches, and tier 3 for albums with no user data.
    Featured music lists are paused while it runs, and ``repair`` runs at the end.
    Each album:

    - **re-keyed in place** when no Apple Music Item holds its listing: the Item keeps
      its pk, so every row that points at it follows. Its source, ID, title and cover
      become the Apple album's and its accent colour is cleared, by queryset update,
      so no signal fires. Its old fields and genres are saved on the link row first.
      Its genre and date filters follow the album page's rules, and its Apple album
      gets the MusicBrainz bridge from the link row.
    - **joined** when one does (another MusicBrainz album moved there first, or the
      album was first added from an Apple page): each user's rows move there, for
      users with no rows on it (``move_user_rows``). The MusicBrainz Item stays.

    Then the row is ``applied`` and, once committed, both refs' cached detail pages are
    dropped. Two MusicBrainz albums that are one Apple album: the one with more user
    rows goes first, so it is the one re-keyed. Every ``APPLY_BATCH`` albums and at
    the end, a check: each moved Item has its pk, each user has as many rows as
    before, and no stray is left; a problem stops the run.
    """
    from lists.featured import dispatch  # noqa: PLC0415

    _require_key()
    counts = Counter()
    dispatch.pause_music_lists()
    try:
        candidates = _apply_candidates()[:limit]
        albums = _apple_albums([_identity(row.apple_album_id) for row, _ in candidates])
        done = []
        for row, item in candidates:
            album = albums.get(_identity(row.apple_album_id))
            outcome = _apply_one(row.pk, item.pk, album)
            counts[outcome] += 1
            if outcome not in ("moved", "joined"):
                continue
            done.append(row.pk)
            if len(done) % APPLY_BATCH == 0:
                # A long run keeps the lists paused, and checks its work so far.
                dispatch.pause_music_lists()
                _require_clean(post_check(done[-APPLY_BATCH:]))
        _require_clean(post_check(done))
        repaired = repair()
    finally:
        dispatch.resume_music_lists()
    return {**dict(sorted(counts.items())), **{f"repair: {key}": value for key, value in repaired.items()}}


def _apply_candidates():
    """Return ``(row, Item)`` for each album apply may move, most user rows first."""
    rows = MusicAlbumLink.objects.filter(status=Status.MATCHED).exclude(apple_album_id="")
    items = {item.media_id: item for item in _musicbrainz_items().filter(media_id__in=rows.values("release_group_mbid"))}
    per_item = _rows_per_item(item.pk for item in items.values())
    candidates = []
    for row in rows:
        item = items.get(row.release_group_mbid)
        if item is None:
            continue
        sure = row.tier in REDIRECT_TIERS or row.method == Method.MANUAL
        if sure or (row.tier == 3 and not per_item[item.pk]):  # noqa: PLR2004
            candidates.append((row, item))
    candidates.sort(key=lambda candidate: (-per_item[candidate[1].pk], candidate[1].pk))
    return candidates


def _apple_albums(identities):
    """Return the Apple Music albums, by ID and link ID, in background batches."""
    albums = {}
    for album in apple_music.albums(sorted(set(identities)), background=True):
        for album_id in apple_music._member_ids([album]):
            albums.setdefault(album_id, album)
    return albums


def _apply_one(row_pk, item_pk, album):
    """Move one album; return "moved", "joined", or why it wasn't."""
    from api.services.filters import update_item_filter_metadata  # noqa: PLC0415

    try:
        with transaction.atomic():
            row = MusicAlbumLink.objects.select_for_update().get(pk=row_pk)
            item = _musicbrainz_items().select_for_update().filter(pk=item_pk).first()
            if item is None or row.status != Status.MATCHED or item.media_id != row.release_group_mbid:
                return "skipped: changed since"
            identity = _identity(row.apple_album_id)
            holder = _apple_holder(identity, row.apple_album_id)
            if holder is None and album is None:
                return "skipped: not on Apple Music"
            pks = [item.pk] if holder is None else [item.pk, holder.pk]
            before = user_row_counts(pks)
            if holder is not None:
                moved, kept = move_user_rows(item, holder)
                row.previous = {"item": item.pk, "joined": holder.pk, "kept_users": kept}
                row.moved_rows = moved
                row.item = holder
                row.apple_album_id = holder.media_id
                outcome = "joined"
            else:
                row.previous = _saved_state(item)
                Item.objects.filter(pk=item.pk).update(
                    source=APPLE_MUSIC,
                    media_id=identity,
                    title=row.apple_title or item.title,
                    image=row.apple_image or item.image,
                    poster_accent_color="",
                )
                bridge = _seed_bridge(identity, row, mb_title=item.title, mb_date=row.previous["release_date"])
                item.refresh_from_db()
                update_item_filter_metadata(
                    item,
                    apple_music.filter_metadata(identity, album.get("attributes") or {}, bridge),
                )
                row.moved_rows = []
                row.item = item
                row.apple_album_id = identity
                outcome = "moved"
            row.status = Status.APPLIED
            row.applied_at = timezone.now()
            row.save(update_fields=["previous", "moved_rows", "item", "apple_album_id", "status", "applied_at"])
            if user_row_counts(pks) != before:
                raise MoveError(f"{row.release_group_mbid}: user rows changed while moving; nothing was moved")
            _drop_caches_on_commit(row.release_group_mbid, row.apple_album_id)
    except IntegrityError as error:
        logger.warning("music_catalog apply skipped %s: %s", row_pk, error)
        return "skipped: busy"
    return outcome


def _seed_bridge(identity, row, *, mb_title, mb_date):
    """Give the Apple album the MusicBrainz bridge its link row knows.

    The release group, its genres and exact Spotify link from the row, and its first
    release date (the genre table's, which can be partial, else the Item's). Checked
    now, so the bridge task leaves it for a month. A bridge naming another release
    group is kept.
    """
    album, _ = AppleAlbum.objects.get_or_create(apple_album_id=identity, defaults={"canonical_id": identity})
    if row.apple_album_id != identity:
        AppleAlbum.objects.get_or_create(apple_album_id=row.apple_album_id, defaults={"canonical_id": identity})
    if album.release_group_mbid not in ("", row.release_group_mbid):
        return album
    known_date = (
        MusicGenreAlbum.objects.filter(release_group_mbid=row.release_group_mbid)
        .exclude(release_date="")
        .values_list("release_date", flat=True)
        .first()
    )
    album.release_group_mbid = row.release_group_mbid
    album.same_title = apple_music.same_title(row.apple_title, mb_title)
    album.first_release_date = known_date or mb_date or ""
    album.genres = list(row.genres)
    album.spotify_url = row.spotify_url
    album.spotify_exact = bool(row.spotify_url)
    album.bridge_checked_at = timezone.now()
    album.save()
    return album


def post_check(row_pks):
    """Return what is wrong with these moves: lines for the console, or none.

    Each moved Item still has its pk and its Apple ref; each joined album's
    MusicBrainz Item still exists; and none of these albums has a stray with rows
    ``repair`` could move. User row counts are checked inside each album's
    transaction, which rolls back if they changed.
    """
    problems = []
    rows = list(MusicAlbumLink.objects.filter(pk__in=list(row_pks)).select_related("item"))
    for row in rows:
        previous = row.previous or {}
        if row.status != Status.APPLIED or row.item is None:
            problems.append(f"{row.release_group_mbid}: not applied")
        elif "joined" in previous:
            if not Item.objects.filter(pk=previous.get("item")).exists():
                problems.append(f"{row.release_group_mbid}: its MusicBrainz Item is gone")
        elif (row.item.pk, row.item.source, row.item.media_id) != (previous.get("item"), APPLE_MUSIC, row.apple_album_id):
            problems.append(f"{row.release_group_mbid}: Item {previous.get('item')} isn't the Apple album")
    for row, stray, movable, _ in _leftovers([row.release_group_mbid for row in rows]):
        if movable:
            problems.append(f"{row.release_group_mbid}: Item {stray.pk} still holds rows")
    return problems


def _require_clean(problems):
    if problems:
        raise MoveError("Post-check failed:\n" + "\n".join(problems))


# ---------------------------------------------------------------------------
# revert


def revert(*, release_group_mbid=None, everything=False, clock=None, sleep=None):
    """Undo moves: one album, or all of them.

    - A **re-keyed** album gets back its old source, ID, title, cover, accent colour,
      dates and genre filters, and its moved rows go back; its cover picks apply
      again. If a stray MusicBrainz Item has taken its old ID, the stray's rows join
      the album instead (``move_user_rows``) and the stray takes the album's Apple ID,
      so the album still gets its own Item back.
    - A **joined** album's moved rows go back to its MusicBrainz Item. The Item it
      joined keeps its own Apple ref.
    - A **manual pick** whose Apple album has an Item that wasn't moved (first added
      from an Apple page) goes to MusicBrainz as below. An Apple album that a moved
      album holds is left alone.

    The row becomes ``reverted``. Once no Apple Item of the album holds user rows, it
    names the album's Apple identity, so the Apple refs the album was known by reach
    its MusicBrainz Item again (canonical ref rule 1). Users with rows on both keep
    them where they are, and the row records them for ``report`` and ``check``.

    ``everything`` refuses while the catalog is Apple Music: switch it back first. It
    reverts every move, newest first, then gives each remaining Apple Music album with
    no move to MusicBrainz: the MusicBrainz album a joined move came from, else its
    bridge's release group when the titles match. It is re-keyed with MusicBrainz's
    title and cover (one paced request), or its rows join the MusicBrainz Item that
    album has, under a ``bridge`` row. An album whose release group a move was already
    reverted to stays on Apple Music, so that move keeps its row. Albums left are
    listed for ``link`` and ``revert --mbid``. Featured music lists are paused meanwhile.
    """
    from lists.featured import dispatch  # noqa: PLC0415

    if everything and config.music_catalog_source() == Sources.APPLE_MUSIC:
        raise MoveError("The music catalog is Apple Music: set MUSIC_CATALOG_SOURCE=musicbrainz first")
    pacer = _Pacer(clock=clock, sleep=sleep)
    counts = Counter()
    if release_group_mbid:
        row = MusicAlbumLink.objects.filter(release_group_mbid=str(release_group_mbid).strip().lower()).first()
        if row is None:
            raise MoveError(f"No link row for {release_group_mbid}")
        if row.status == Status.APPLIED:
            counts[_revert_row(row.pk)] += 1
        elif row.status == Status.MATCHED and row.method == Method.MANUAL:
            counts[_revert_manual_pick(row, pacer)] += 1
        else:
            counts["nothing to revert"] += 1
        return dict(counts)
    if not everything:
        raise MoveError("Pass --mbid or --all")
    dispatch.pause_music_lists()
    try:
        applied = MusicAlbumLink.objects.filter(status=Status.APPLIED).order_by("-applied_at", "-pk")
        just_kept = set()
        for row_pk in list(applied.values_list("pk", flat=True)):
            outcome = _revert_row(row_pk)
            counts[outcome] += 1
            if outcome == STRAY_KEPT:
                just_kept.add(row_pk)
        joined = {
            row.previous["joined"]: row
            for row in MusicAlbumLink.objects.filter(status=Status.REVERTED).order_by("-pk")
            if "joined" in (row.previous or {})
        }
        for item in _apple_items_not_moved().order_by("pk"):
            row = joined.get(item.pk)
            if row is not None and row.pk in just_kept:
                # Its stray kept rows a moment ago; the next run tries again.
                continue
            counts[_revert_apple_page_album(item, row, pacer)] += 1
    finally:
        dispatch.resume_music_lists()
    return dict(sorted(counts.items()))


def _revert_row(row_pk):
    with transaction.atomic():
        # No select_related: Postgres can't lock the nullable side of an outer join.
        row = MusicAlbumLink.objects.select_for_update().get(pk=row_pk)
        if row.status != Status.APPLIED:
            return "skipped: changed since"
        previous = row.previous or {}
        apple_album_id = row.apple_album_id
        stayed = 0
        if "joined" in previous:
            stayed = undo_moves(row.moved_rows)
            item = Item.objects.filter(pk=previous.get("item")).first()
            row.moved_rows = []
            # The Item it joined still holds other rows (an album first added from an
            # Apple page, or another album's move), so it keeps its Apple ref.
            row.apple_album_id = ""
            outcome = "joined album reverted"
        else:
            item = Item.objects.get(pk=previous["item"])
            stray = _musicbrainz_items().filter(media_id=previous["media_id"]).exclude(pk=item.pk).first()
            if stray is None:
                stayed = undo_moves(row.moved_rows)
                _restore(item, previous)
                row.moved_rows = []
                outcome = "reverted"
            else:
                # Rows check --repair moved from the stray stay on the album.
                moved, kept = move_user_rows(stray, item)
                _restore_over_stray(item, stray, previous)
                row.moved_rows = [*(row.moved_rows or []), *moved]
                outcome = "reverted, with a stray's rows"
                if kept:
                    # The stray, under the Apple ID now, keeps their rows and its ref.
                    row.previous = {**previous, "joined": stray.pk, "kept_users": kept}
                    row.apple_album_id = ""
                    outcome = STRAY_KEPT
        row.status = Status.REVERTED
        row.item = item
        row.save(update_fields=["status", "item", "moved_rows", "previous", "apple_album_id"])
        _drop_caches_on_commit(row.release_group_mbid, apple_album_id)
    if stayed:
        logger.warning("music_catalog revert: %s rows of %s stayed", stayed, row.release_group_mbid)
    return outcome


def _restore_over_stray(item, stray, previous):
    """Restore a re-keyed Item whose old ID a stray holds: the stray takes its Apple ID.

    In three updates, so no key is ever held twice. The stray keeps its other rows
    (genre filters, events, cover picks).
    """
    apple_id = item.media_id
    Item.objects.filter(pk=stray.pk).update(source=APPLE_MUSIC, media_id=f"stray-{stray.pk}")
    _restore(item, previous)
    Item.objects.filter(pk=stray.pk).update(media_id=apple_id)


def _revert_manual_pick(row, pacer):
    """Give a manual pick's Apple album to MusicBrainz, if no move put it there."""
    identity = _identity(row.apple_album_id)
    holder = _apple_holder(identity, row.apple_album_id, among=_apple_items_not_moved())
    if holder is not None:
        return _revert_apple_album(holder, row.release_group_mbid, Method.MANUAL, pacer)
    if _apple_holder(identity, row.apple_album_id) is not None:
        return "nothing to revert: a moved album holds its Apple album"
    return "nothing to revert"


def _restore(item, previous):
    """Put an Item's saved fields and filters back."""
    fields = {name: previous.get(name) for name in SAVED_FIELDS}
    fields["release_date"] = date.fromisoformat(fields["release_date"]) if fields["release_date"] else None
    stamp = fields["filter_metadata_updated_at"]
    fields["filter_metadata_updated_at"] = datetime.fromisoformat(stamp) if stamp else None
    Item.objects.filter(pk=item.pk).update(**fields)
    ItemFilterFacet.objects.filter(item=item).delete()
    ItemFilterFacet.objects.bulk_create(
        [ItemFilterFacet(item=item, facet_type=facet_type, value=value) for facet_type, value in previous.get("facets") or []],
        ignore_conflicts=True,
    )


def _apple_items_not_moved():
    """Apple Music album Items that are no move's: first added from Apple pages.

    Not those an applied row holds, nor empty ones whose Apple ID a reverted row sends
    to MusicBrainz (a stray that took a reverted album's Apple ID, or an album whose
    rows all joined their MusicBrainz album).
    """
    apple = Item.objects.filter(source=APPLE_MUSIC, media_type=MUSIC, season_number=None, episode_number=None)
    held = MusicAlbumLink.objects.filter(status=Status.APPLIED, item__isnull=False).values("item")
    sent_back = list(
        apple.filter(
            media_id__in=MusicAlbumLink.objects.filter(status=Status.REVERTED)
            .exclude(apple_album_id="")
            .values("apple_album_id"),
        ).values_list("pk", flat=True),
    )
    with_rows = users_with_rows(sent_back)
    return apple.exclude(pk__in=held).exclude(pk__in=[pk for pk in sent_back if pk not in with_rows])


def _revert_apple_page_album(item, joined, pacer):
    """Give an Apple Music album that never moved to MusicBrainz, if Spine knows which.

    ``joined`` is the reverted row of a MusicBrainz album that had joined it, if any:
    its users' rows join that album, and once none are left the row sends the Apple
    album's refs there too.
    """
    if joined is not None:
        target = Item.objects.filter(pk=joined.previous.get("item")).first()
        if target is not None:
            with transaction.atomic():
                row = MusicAlbumLink.objects.select_for_update().get(pk=joined.pk)
                if row.status != Status.REVERTED:
                    return "skipped: changed since"
                moved, kept = move_user_rows(item, target)
                row.moved_rows = [*(row.moved_rows or []), *moved]
                row.previous = {**(row.previous or {}), "kept_users": kept}
                row.apple_album_id = "" if kept else item.media_id
                row.save(update_fields=["moved_rows", "previous", "apple_album_id"])
                _drop_caches_on_commit(row.release_group_mbid, item.media_id)
            return KEPT_ON_BOTH if kept else "Apple album joined its MusicBrainz album"
    bridge = AppleAlbum.objects.filter(apple_album_id=item.media_id, same_title=True).exclude(release_group_mbid="").first()
    if bridge is None:
        return "left on Apple Music: no bridge"
    return _revert_apple_album(item, bridge.release_group_mbid, Method.BRIDGE, pacer)


def _revert_apple_album(item, release_group_mbid, method, pacer):
    """Give an Apple Music album that never moved to a MusicBrainz release group.

    It joins that album's MusicBrainz Item if there is one, else it is re-keyed with
    MusicBrainz's title and cover. A ``reverted`` row records it either way, and
    sends the Apple album's refs to MusicBrainz unless users keep rows on both. A
    release group whose row is already ``reverted`` keeps that row: it names the Apple
    ID a move gave back, and the album stays on Apple Music with its own ref.
    """
    row = MusicAlbumLink.objects.filter(release_group_mbid=release_group_mbid).first()
    if row is not None and row.status == Status.REVERTED:
        return "left on Apple Music: its MusicBrainz album was reverted already"
    target = _musicbrainz_items().filter(media_id=release_group_mbid).first()
    title = None
    if target is None:
        try:
            group = pacer.musicbrainz(musicbrainz.lookup_release_group, release_group_mbid)
        except services.ProviderAPIError as error:
            logger.warning("music_catalog revert couldn't read %s: %s", release_group_mbid, error)
            return "left on Apple Music: MusicBrainz failed"
        title = group.get("title") or item.title
    with transaction.atomic():
        row = MusicAlbumLink.objects.select_for_update().filter(release_group_mbid=release_group_mbid).first()
        if row is not None and row.status in (Status.APPLIED, Status.REVERTED):
            return "skipped: changed since"
        moved, kept = [], []
        if target is not None:
            moved, kept = move_user_rows(item, target)
            previous = {"item": target.pk, "joined": item.pk, "kept_users": kept}
            outcome = KEPT_ON_BOTH if kept else "Apple album joined its MusicBrainz album"
        else:
            previous = _saved_state(item)
            Item.objects.filter(pk=item.pk).update(
                source=MUSICBRAINZ,
                media_id=release_group_mbid,
                title=title,
                image=musicbrainz._cover_art_url(release_group_mbid),
                poster_accent_color="",
            )
            target = item
            outcome = "Apple album re-keyed to MusicBrainz"
        defaults = {
            "status": Status.REVERTED,
            "apple_album_id": "" if kept else previous.get("media_id") or item.media_id,
            "item": target,
            "previous": previous,
            "moved_rows": moved,
            "checked_at": timezone.now(),
        }
        if row is None or row.method != Method.MANUAL:
            defaults.update(method=method, tier=None)
        MusicAlbumLink.objects.update_or_create(release_group_mbid=release_group_mbid, defaults=defaults)
        _drop_caches_on_commit(release_group_mbid, defaults["apple_album_id"])
    return outcome


# ---------------------------------------------------------------------------
# check and repair


def check(*, repair_strays=False):
    """Test the Apple Music key and count what needs attention.

    One Apple Music request. Counts link rows by status; strays (an old MusicBrainz
    Item of a moved album with rows ``repair`` can move); albums kept on both (users
    with rows on both Items, of a moved album or of one reverted since); rows left in
    tables the move doesn't know; Apple Music albums not from the move; and genre pages
    under the gate for switching the catalog to Apple Music. ``repair_strays`` runs
    ``repair`` first.
    """
    result = {"Apple Music key": _key_status()}
    if repair_strays:
        result.update({f"repair: {key}": value for key, value in repair().items()})
    statuses = Counter(MusicAlbumLink.objects.values_list("status", flat=True))
    result.update({f"link rows {status}": statuses.get(status, 0) for status in Status.values})
    strays = kept_albums = 0
    kept_users = set()
    unlisted = Counter()
    leftovers = _leftovers()
    for _, _, movable, kept in leftovers:
        strays += bool(movable)
        kept_albums += bool(kept)
        kept_users |= kept
    for _, kept in _kept_after_revert():
        kept_albums += 1
        kept_users |= kept
    unlisted.update(_unlisted_rows([stray.pk for _, stray, _, _ in leftovers]))
    result.update(
        {
            "strays": strays,
            "albums kept on both": kept_albums,
            "users kept on both": len(kept_users),
            "rows in other tables": sum(unlisted.values()),
            **{f"rows in {table}": rows for table, rows in sorted(unlisted.items())},
            "Apple Music albums not from the move": _apple_items_not_moved().count(),
        },
    )
    genres, below = genre_coverage()
    result["genre pages"] = len(genres)
    result["genre pages under the gate"] = len(below)
    for genre, total, linked in below[:10]:
        result[f"genre {genre}"] = f"{linked} of {total} linked"
    return result


def repair():
    """Join strays' rows to the albums that moved, for users with none there.

    A stray is a MusicBrainz Item of a moved album, made by a path that didn't send
    the album's old ref to its new Item. Its rows move like a joined album's, and
    the moves are added to the row. ``revert`` keeps them on a re-keyed album, which
    takes its old ID back from the stray, and puts a joined album's back.
    """
    counts = Counter()
    for row, stray, movable, _ in _leftovers():
        if not movable:
            continue
        with transaction.atomic():
            row = MusicAlbumLink.objects.select_for_update().get(pk=row.pk)
            if row.status != Status.APPLIED or row.item is None:
                continue
            moved, kept = move_user_rows(stray, row.item)
            row.moved_rows = [*(row.moved_rows or []), *moved]
            row.save(update_fields=["moved_rows"])
            _drop_caches_on_commit(row.release_group_mbid, row.item.media_id)
        counts["albums"] += 1
        counts["rows moved"] += len(moved)
        counts["users kept on both"] += len(kept)
    return {"albums": counts["albums"], "rows moved": counts["rows moved"], "users kept on both": counts["users kept on both"]}


def _leftovers(release_group_mbids=None):
    """Return ``(row, stray, movable users, kept users)`` for each moved album's old Item.

    The old Item is a MusicBrainz Item with the moved album's release group that isn't
    the Item holding the album now: a stray, or a joined album's MusicBrainz Item.
    """
    rows = MusicAlbumLink.objects.filter(status=Status.APPLIED, item__isnull=False).select_related("item")
    if release_group_mbids is not None:
        rows = rows.filter(release_group_mbid__in=list(release_group_mbids))
    rows = {row.release_group_mbid: row for row in rows}
    strays = [
        (rows[item.media_id], item)
        for item in _musicbrainz_items().filter(media_id__in=list(rows))
        if item.pk != rows[item.media_id].item_id
    ]
    users = users_with_rows([pk for row, stray in strays for pk in (stray.pk, row.item_id)])
    return [
        (row, stray, users[stray.pk] - users[row.item_id], users[stray.pk] & users[row.item_id])
        for row, stray in strays
    ]


def _kept_after_revert():
    """Return ``(row, kept users)`` for each reverted album users keep rows of on two Items.

    The other Item is the row's ``joined`` one, while it is still an Apple Music album:
    the Item a joined album went back from, an album first added from an Apple page, or
    a stray that took a reverted album's Apple ID.
    """
    rows = [
        row
        for row in MusicAlbumLink.objects.filter(status=Status.REVERTED)
        if (row.previous or {}).get("kept_users") and (row.previous or {}).get("joined")
    ]
    apple = set(
        Item.objects.filter(pk__in=[row.previous["joined"] for row in rows], source=APPLE_MUSIC).values_list(
            "pk",
            flat=True,
        ),
    )
    return [(row, set(row.previous["kept_users"])) for row in rows if row.previous["joined"] in apple]


def _unlisted_rows(item_pks):
    """Return ``{table: rows}`` on these Items in tables the move doesn't know."""
    counts = Counter()
    if not item_pks:
        return counts
    known = STAYING_ROWS | {(table.model._meta.label_lower, table.field) for table in user_tables()}
    for relation in Item._meta.get_fields(include_hidden=True):
        if not (relation.is_relation and relation.auto_created and not relation.concrete) or relation.many_to_many:
            continue
        key = (relation.related_model._meta.label_lower, relation.field.name)
        if key in known:
            continue
        rows = relation.related_model._base_manager.filter(**{f"{relation.field.name}__in": item_pks}).count()
        if rows:
            counts[f"{key[0]}.{key[1]}"] += rows
    return counts


def genre_coverage():
    """Return every genre's ``(genre, albums, linked)``, and those under the switch's gate.

    Linked albums have a matched or applied link row with an Apple album. Under the
    gate: 20 or more albums, and under 80% of them linked.
    """
    linked = (
        MusicAlbumLink.objects.filter(status__in=(Status.MATCHED, Status.APPLIED))
        .exclude(apple_album_id="")
        .values("release_group_mbid")
    )
    genres = [
        (row["genre"], row["total"], row["linked"])
        for row in MusicGenreAlbum.objects.values("genre")
        .annotate(total=Count("pk"), linked=Count("pk", filter=Q(release_group_mbid__in=linked)))
        .order_by("-total", "genre")
    ]
    below = [
        (genre, total, linked_count)
        for genre, total, linked_count in genres
        if total >= GENRE_GATE_ALBUMS and linked_count < GENRE_GATE_SHARE * total
    ]
    return genres, below


def _key_status():
    if not apple_music.is_configured():
        return "not configured"
    try:
        apple_music.albums([KEY_CHECK_ALBUM], background=True)
    except services.ProviderAPIError as error:
        status = f"HTTP {error.status_code}" if error.status_code else "no answer"
        return f"failed ({status})"
    return "works"


# ---------------------------------------------------------------------------
# report


def report(stream=None):
    """Return the counts, and write every album's row as CSV to ``stream`` if given.

    The CSV holds library titles and user IDs, so it belongs in a private file; the
    counts don't.
    """
    items = list(_musicbrainz_items())
    links = {link.release_group_mbid: link for link in MusicAlbumLink.objects.all()}
    groups = _groups(items)
    leftovers = {stray.pk: kept for _, stray, _, kept in _leftovers()}
    # Reverted albums whose users keep rows on an Apple Item too, by both Items.
    after = _kept_after_revert()
    kept_after = {row.previous["item"]: kept for row, kept in after}
    kept_after_apple = {row.previous["joined"]: kept for row, kept in after}
    users = users_with_rows([item.pk for item in items])
    rows = []
    counts = Counter()
    for item in items:
        link = links.get(item.media_id)
        group = groups[item.pk]
        status = link.status if link else "no row"
        note = "stray or joined" if item.pk in leftovers else ""
        if item.pk in kept_after:
            note = note or "kept on both after a revert"
        if group == 1 and link is None:
            note = note or "map hasn't looked"
        counts[f"group {group} {status}"] += 1
        kept = leftovers.get(item.pk) or kept_after.get(item.pk)
        rows.append(_report_row(f"group {group}", item, link, len(users[item.pk]), kept, note))
    moved = MusicAlbumLink.objects.filter(status=Status.APPLIED).select_related("item")
    for link in moved:
        kind = "joined" if "joined" in (link.previous or {}) else "moved"
        counts[kind] += 1
        kept = (link.previous or {}).get("kept_users") or []
        rows.append(_report_row(kind, link.item, link, None, kept, ""))
    for item in _apple_items_not_moved():
        bridge = AppleAlbum.objects.filter(apple_album_id=item.media_id).first()
        bridged = bridge is not None and bridge.release_group_mbid and bridge.same_title
        counts["Apple Music albums not from the move"] += 1
        counts["Apple Music albums with no bridge"] += 0 if bridged else 1
        note = f"bridge {bridge.release_group_mbid}" if bridged else "no bridge"
        rows.append(_report_row("apple page", item, None, None, kept_after_apple.get(item.pk), note))
    counts["users kept on both"] = len({user for kept in [*leftovers.values(), *kept_after.values()] for user in kept})
    if stream is not None:
        writer = csv.writer(stream)
        writer.writerow(REPORT_COLUMNS)
        writer.writerows(rows)
    return dict(sorted(counts.items()))


def _report_row(group, item, link, users, kept, note):
    return [
        group,
        item.pk if item else "",
        link.release_group_mbid if link else (item.media_id if item and item.source == MUSICBRAINZ else ""),
        item.title if item else "",
        link.status if link else "",
        link.method if link else "",
        link.tier if link and link.tier else "",
        link.apple_album_id if link else (item.media_id if item and item.source == APPLE_MUSIC else ""),
        link.apple_title if link else "",
        "" if users is None else users,
        " ".join(str(user) for user in sorted(kept or [])),
        note,
    ]


# ---------------------------------------------------------------------------
# Shared


def _require_key():
    if not apple_music.is_configured():
        raise MoveError("Apple Music isn't configured: set the APPLE_MUSIC_* key settings")


def _musicbrainz_items():
    return Item.objects.filter(source=MUSICBRAINZ, media_type=MUSIC, season_number=None, episode_number=None)


def _identity(apple_album_id):
    """Return an Apple album ID's listing identity, as Spine has recorded it."""
    return (
        AppleAlbum.objects.filter(apple_album_id=apple_album_id).values_list("canonical_id", flat=True).first()
        or apple_album_id
    )


def _apple_holder(identity, apple_album_id, *, among=None):
    """Return the Apple Music Item that holds any member of this listing, or None.

    ``among`` limits it to those Apple Music Items.
    """
    ids = {identity, apple_album_id, *AppleAlbum.objects.filter(canonical_id=identity).values_list("apple_album_id", flat=True)}
    if among is None:
        among = Item.objects.filter(source=APPLE_MUSIC, media_type=MUSIC, season_number=None, episode_number=None)
    holders = among.filter(media_id__in=ids)
    return min(holders, key=lambda item: (item.media_id != identity, item.pk), default=None)


def _item_genres(item):
    if item is None:
        return []
    return sorted(
        ItemFilterFacet.objects.filter(item=item, facet_type=ItemFilterFacet.FacetType.GENRE).values_list("value", flat=True),
    )


def _saved_state(item):
    """Return an Item's fields and filters as JSON, for undoing a move."""
    state = {"item": item.pk}
    for name in SAVED_FIELDS:
        value = getattr(item, name)
        state[name] = value.isoformat() if isinstance(value, date | datetime) else value
    facets = ItemFilterFacet.objects.filter(item=item).values_list("facet_type", "value")
    state["facets"] = [list(facet) for facet in sorted(facets)]
    return state


def _drop_caches_on_commit(release_group_mbid, apple_album_id):
    """Drop both refs' cached album pages once the move is committed."""
    from api.services.media import drop_music_detail_cache  # noqa: PLC0415

    def drop():
        drop_music_detail_cache(MUSICBRAINZ, release_group_mbid)
        if apple_album_id:
            drop_music_detail_cache(APPLE_MUSIC, apple_album_id)

    transaction.on_commit(drop, robust=True)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

