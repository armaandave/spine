"""Apple Music's listing rule: which Apple albums Spine shows as one album.

Pure functions over Apple album resources (``{"id", "attributes", "views"}``), with no
network or database calls. See docs/music/apple-music-catalog.md.

- A **listing** is the albums that are in one another's ``other-versions`` family and
  share the artist and normalized title: an explicit album and its clean twin, or an
  older upload and its re-upload. Edition words keep listings apart, so "SOS" and
  "SOS Deluxe: LANA" are two listings.
- Its **identity** is the one album ID Spine stores for it (``pick_identity``).
"""

import calendar
import re
import unicodedata
from datetime import date
from urllib.parse import urlsplit

FAMILY_VIEW = "other-versions"
# Curly quotes read as straight ones, and square brackets as round ones.
_TITLE_CHARACTERS = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "′": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "″": '"',
        "[": "(",
        "]": ")",
    },
)
_CONTENT_RATING_ORDER = {"explicit": 0, "clean": 2}
_RELEASE_DATE = re.compile(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?")
_DIGITS = re.compile(r"[0-9]+")


def normalize_title(title):
    """Return a title for comparing: NFKC, casefolded, straight quotes, round brackets."""
    text = unicodedata.normalize("NFKC", str(title or ""))
    return " ".join(text.translate(_TITLE_CHARACTERS).casefold().split())


def album_id_from_url(url):
    """Return the album ID at the end of an Apple Music album link, or None.

    Apple keeps old duplicate IDs whose link names the real album, so the link's ID
    is the one to store: Year Zero's 1147788150 links to 1440826538.
    """
    last = urlsplit(str(url or "")).path.rstrip("/").rsplit("/", 1)[-1]
    return last if _DIGITS.fullmatch(last) else None


def url_album_id(album):
    """Return an album resource's own ID: the one in its link, else its ``id``."""
    attributes = album.get("attributes") or {}
    return album_id_from_url(attributes.get("url")) or str(album.get("id") or "")


def group_listings(albums):
    """Split album resources into listings, each a list of members.

    Listings come in the order their first member appears, and members in input
    order. Two albums are one listing when either is in the other's
    ``other-versions`` family and they have the same artist and normalized title.
    Copies of one album under two IDs (the same link ID) are always one listing.
    """
    members = []
    seen = set()
    for album in albums:
        album_id = str(album.get("id") or "")
        if album_id and album_id not in seen:
            seen.add(album_id)
            members.append(album)

    parent = list(range(len(members)))

    def root(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def join(first, second):
        parent[max(root(first), root(second))] = min(root(first), root(second))

    by_id = {}
    for index, album in enumerate(members):
        by_id.setdefault(str(album["id"]), index)
        link_id = url_album_id(album)
        if link_id in by_id:
            join(index, by_id[link_id])
        by_id.setdefault(link_id, index)

    keys = [_listing_key(album) for album in members]
    for index, album in enumerate(members):
        for family_id in _family_ids(album):
            other = by_id.get(family_id)
            if other is not None and keys[other] == keys[index]:
                join(index, other)

    listings = {}
    for index, album in enumerate(members):
        listings.setdefault(root(index), []).append(album)
    return list(listings.values())


def pick_identity(members, item_ids=(), *, today):
    """Return the album ID Spine stores for a listing.

    A member that already has a Spine Item wins, and the identity is the ID that Item
    holds (its link ID when both are held), so an identity never moves once an Item
    exists, even when Apple later points that ID's link at a re-upload. Then, in
    order: it can be streamed; it is complete or a pre-order; explicit, then no
    rating, then clean; the most tracks; the lowest ID.
    ``today`` is today's date in production's time zone.
    """
    held = {str(item_id) for item_id in item_ids}

    def key(album):
        ids = {str(album.get("id") or ""), url_album_id(album)}
        return (not ids & held, *_rank(album, today))

    winner = min(members, key=key)
    link_id = url_album_id(winner)
    own_id = str(winner.get("id") or "")
    if link_id not in held and own_id in held:
        return own_id
    return link_id


def pick_content_member(members, *, today):
    """Return the member whose tracks and Apple link an album page shows.

    The identity's order without its first rule: the best member that can be
    streamed, even when an older member holds the Item.
    """
    return min(members, key=lambda album: _rank(album, today))


def is_hidden(members, today):
    """Return whether a listing is left out of search, artist pages and Other Versions.

    Hidden when no member can be streamed, or when every member is incomplete with a
    release date that has passed (a promo or a partial upload). Its IDs still open.
    A partial date ("2010", "1994-12") counts as the last day it could mean.
    """
    if not any(_streamable(album) for album in members):
        return True
    return all(_incomplete_and_past(album, today) for album in members)


def release_day(value):
    """Return the last day a release date could mean ("2010" is 2010-12-31), or None."""
    match = _RELEASE_DATE.fullmatch(str(value or "").strip())
    if not match:
        return None
    year = int(match[1])
    month = int(match[2] or 12)
    try:
        day = int(match[3]) if match[3] else calendar.monthrange(year, month)[1]
        return date(year, month, day)
    except ValueError:
        return None


def _rank(album, today):
    attributes = album.get("attributes") or {}
    complete_or_upcoming = bool(attributes.get("isComplete")) or _upcoming(attributes, today)
    return (
        not _streamable(album),
        not complete_or_upcoming,
        _CONTENT_RATING_ORDER.get(attributes.get("contentRating"), 1),
        -_track_count(attributes),
        _id_order(url_album_id(album)),
    )


def _listing_key(album):
    attributes = album.get("attributes") or {}
    return (
        normalize_title(attributes.get("artistName")),
        normalize_title(attributes.get("name")),
    )


def _family_ids(album):
    view = (album.get("views") or {}).get(FAMILY_VIEW) or {}
    return [str(member.get("id")) for member in view.get("data") or [] if member.get("id")]


def _streamable(album):
    return bool((album.get("attributes") or {}).get("playParams"))


def _upcoming(attributes, today):
    day = release_day(attributes.get("releaseDate"))
    return day is not None and day >= today


def _incomplete_and_past(album, today):
    attributes = album.get("attributes") or {}
    if attributes.get("isComplete"):
        return False
    day = release_day(attributes.get("releaseDate"))
    return day is not None and day < today


def _track_count(attributes):
    try:
        return int(attributes.get("trackCount") or 0)
    except (TypeError, ValueError):
        return 0


def _id_order(album_id):
    # Numeric IDs compare as numbers; anything else sorts after them.
    if _DIGITS.fullmatch(album_id):
        return (0, int(album_id), album_id)
    return (1, 0, album_id)
