"""Resolve entries to Spine identities.

Order, first hit wins:

1. an override from ``overrides/*.csv`` (``-`` skips the entry);
   an entry with a ``review`` id (its reason) is otherwise always unresolved;
2. the entry's ``ref``;
3. a direct provider ID (``tmdb``, ``igdb``, ``mal``, ``mangaupdates``, ``hardcover``,
   ``olid`` editions, ``apple_album`` before ``mbid_rg``, ``comicvine``; people:
   ``<source>_person``), no network;
   then, with ``remember=True`` (the engine's resolver), the Item this list's latest
   snapshot holds for the entry's key, if it still exists: the database outlives the
   cache, which every deploy clears;
4. ``imdb`` through ``tmdb.find`` and ``steam`` through ``igdb.external_game``;
5. ISBNs: Hardcover, then Open Library, per ISBN (``lookup_isbns``);
6. a strict title search per media type: exact normalized title (or the result's main
   title before a subtitle), year within ±1, creator when both sides have one. Without
   a year or creator the exact title must be unique. As a fallback the entry's own main
   title may match, when the year or creator agrees and only one result qualifies.
   Capped per run by ``Guards.max_title_searches``.

Steps 4-6 are cached under ``featured:resolve:…``: hits for 45 days (longer than the
longest cadence, and refreshed on every hit), misses for one day. Provider errors and
an exhausted budget are never cached. An entry whose lookup hit a provider's daily
budget or rate limit (``app.providers.quota``), or that the run's title-search cap
cut off, is *deferred*: unresolved, uncached, and left out of the resolve rate until
the next run.

Music matches, whichever step found them, are then made canonical in one batch
(``canonical_resolutions``), so an album that moved to Apple Music, or an Apple alias,
is the Item that holds it; the cache keeps the raw matches. With the music catalog on
Apple Music, a MusicBrainz album that no MusicBrainz Item holds then becomes its Apple
album (``Resolver._apple_albums``).
"""

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

import requests
from django.core.cache import cache
from django.utils import timezone

from app import config, music_catalog
from app.models import PRIMARY_MEDIA_TYPES, Item, MediaTypes, MusicAlbumLink, Sources
from app.providers import apple_music, hardcover, igdb, openlibrary, quota, services, tmdb
from lists.featured import overrides as overrides_module
from lists.featured.helpers import sanitize
from lists.featured.text import (
    creator_names,
    creators_match,
    normalize_title,
    parse_year,
    title_forms,
)
from lists.featured.types import PERSON_MEDIA_TYPE, Match
from lists.models import FeaturedListSnapshot

logger = logging.getLogger(__name__)

CACHE_PREFIX = "featured:resolve:"
HIT_TTL = 60 * 60 * 24 * 45  # longer than the longest cadence (33 days)
MISS_TTL = 60 * 60 * 24
MISS = {"miss": True}
# Entries cut off by Guards.max_title_searches wait this long for the next run, which
# finds the earlier ones cached and searches the next batch.
TITLE_SEARCH_RETRY = timedelta(hours=1)

ITEM_SOURCES = {
    MediaTypes.MOVIE.value: (Sources.TMDB.value,),
    MediaTypes.TV.value: (Sources.TMDB.value,),
    MediaTypes.ANIME.value: (Sources.MAL.value,),
    MediaTypes.MANGA.value: (Sources.MAL.value, Sources.MANGAUPDATES.value),
    MediaTypes.GAME.value: (Sources.IGDB.value,),
    MediaTypes.BOOK.value: (Sources.HARDCOVER.value, Sources.OPENLIBRARY.value),
    MediaTypes.COMIC.value: (Sources.COMICVINE.value,),
    MediaTypes.MUSIC.value: (Sources.APPLE_MUSIC.value, Sources.MUSICBRAINZ.value),
}
# With the catalog on Apple Music, Apple searches a run may make for MusicBrainz albums
# with no link row yet (two requests each). The rest keep their MusicBrainz album until
# a later run, so a long list never shrinks or runs out of time.
MAX_APPLE_SEARCHES = 50
_LINKED = (MusicAlbumLink.Status.MATCHED, MusicAlbumLink.Status.APPLIED)
PERSON_SOURCES = ("tmdb", "hardcover", "openlibrary", "musicbrainz", "mal", "mangaupdates", "anilist")
PROVIDER_ERRORS = (
    services.ProviderAPIError,
    requests.RequestException,
    KeyError,
    ValueError,
    TypeError,
)


def parse_ref(ref):
    """Return ``(source, media_type, media_id)`` from ``<source>:<media_type>:<media_id>``."""
    parts = str(ref or "").strip().split(":", 2)
    if len(parts) != 3 or not all(parts):  # noqa: PLR2004
        msg = f"invalid ref {ref!r}: use <source>:<media_type>:<media_id>"
        raise ValueError(msg)
    source, media_type, media_id = parts
    if media_type == PERSON_MEDIA_TYPE:
        if source not in PERSON_SOURCES:
            msg = f"invalid ref {ref!r}: unknown person source {source!r}"
            raise ValueError(msg)
    elif media_type not in PRIMARY_MEDIA_TYPES:
        msg = f"invalid ref {ref!r}: unknown media type {media_type!r}"
        raise ValueError(msg)
    elif source not in ITEM_SOURCES[media_type]:
        msg = f"invalid ref {ref!r}: {source} does not provide {media_type}"
        raise ValueError(msg)
    return source, media_type, media_id


def match_from_ref(ref, entry=None):
    """Return a ``Match`` for a ref, carrying the entry's title, image and year."""
    source, media_type, media_id = parse_ref(ref)
    return Match(
        source=source,
        media_type=media_type,
        media_id=media_id,
        title=entry.title if entry else "",
        image=entry.image if entry else "",
        year=entry.year if entry else None,
    )


def lookup_isbns(isbns):
    """Return the first Hardcover, then Open Library, book for any of ``isbns``.

    Same order as the original NYT sync: every ISBN on Hardcover first. Provider
    errors don't stop the search; if nothing matched, the last error is raised.
    """
    last_error = None
    for lookup in (hardcover.lookup_book_by_isbn, openlibrary.lookup_book_by_isbn):
        for isbn in isbns:
            try:
                result = lookup(isbn)
            except (services.ProviderAPIError, requests.RequestException) as error:
                last_error = error
                continue
            if result:
                return result
    if last_error is not None:
        raise last_error
    return None


def remembered_items(key):
    """Return ``{entry_key: Item}`` from the latest snapshot of the list synced as ``key``.

    Two queries: the snapshot, then its Items (deleted ones drop out).
    """
    entries = (
        FeaturedListSnapshot.objects.filter(sync__key=key)
        .order_by("-taken_on")
        .values_list("entries", flat=True)
        .first()
    )
    item_ids = {entry_key: item_id for _, entry_key, item_id in entries or () if item_id is not None}
    if not item_ids:
        return {}
    items = Item.objects.only("id", "source", "media_type", "media_id", "title").in_bulk(set(item_ids.values()))
    return {entry_key: items[item_id] for entry_key, item_id in item_ids.items() if item_id in items}


def _match_from_book(result):
    image = result.get("image") or ""
    return Match(
        source=str(result["source"]),
        media_type=MediaTypes.BOOK.value,
        media_id=str(result["media_id"]),
        title=result.get("title") or "",
        image=image,
        total_pages=result.get("total_pages") or result.get("max_progress"),
    )


@dataclass(frozen=True)
class Resolution:
    """The outcome for one entry."""

    entry: object
    match: Match | None
    how: str = ""  # override, ref, id, snapshot, imdb, steam, isbn, title, cache, skip, review, deferred
    reason: str = ""  # why it is unresolved
    retry_at: datetime | None = None  # deferred: when the provider may be called again

    @property
    def skipped(self):
        """Return whether an override removed this entry from the list."""
        return self.how == "skip"

    @property
    def deferred(self):
        """Return whether the entry waits for a provider's budget or rate limit."""
        return self.how == "deferred"


def deferred_resolution(entry, error):
    """Return the ``Resolution`` for an entry whose lookup raised ``quota.Deferred``."""
    return Resolution(entry, None, "deferred", str(error), retry_at=error.until)


def canonical_resolutions(resolutions):
    """Return resolutions with every music match under its canonical ref, in one lookup.

    ``app.music_catalog.canonical_music_refs`` reads the database only. A match that
    moves to another source keeps its title but not its cover, which is the old
    source's: ``writer.prepare`` fetches the new one when the Item is new.
    """
    refs = [_ref(resolution.match) for resolution in resolutions if _is_music(resolution.match)]
    if not refs:
        return list(resolutions)
    canonical = music_catalog.canonical_music_refs(refs)
    moved = []
    for resolution in resolutions:
        match = resolution.match
        new = canonical.get(_ref(match)) if _is_music(match) else None
        if new is None or new == _ref(match):
            moved.append(resolution)
            continue
        image = match.image if new[0] == match.source else ""
        moved.append(replace(resolution, match=replace(match, source=new[0], media_id=new[2], image=image)))
    return moved


def _is_music(match):
    return match is not None and match.media_type == MediaTypes.MUSIC.value


def _ref(match):
    return (match.source, match.media_type, str(match.media_id))


class _Uncacheable(Exception):  # noqa: N818
    """The answer depends on a transient condition (a provider error)."""


class _TitleSearchesSpent(Exception):  # noqa: N818
    """This run's title-search cap is used up; the entry waits for the next run."""


class Resolver:
    """Resolve a definition's entries; one instance per run (it holds the budget)."""

    def __init__(
        self,
        definition,
        *,
        overrides=None,
        use_cache=True,
        isbn_lookup=None,
        max_title_searches=None,
        remember=False,
    ):
        """Configure the run; ``isbn_lookup`` replaces ``lookup_isbns`` and disables the cache.

        ``remember`` reuses the matches in this list's latest snapshot (a database read).
        """
        self.definition = definition
        self.overrides = overrides_module.for_definition(definition) if overrides is None else overrides
        self.use_cache = use_cache and isbn_lookup is None
        self.isbn_lookup = isbn_lookup or lookup_isbns
        self.title_budget = (
            definition.guards.max_title_searches if max_title_searches is None else max_title_searches
        )
        self.title_searches = 0
        self.people = definition.list_type == "people"
        self.remember = remember and not self.people  # people lists' snapshots hold no Items
        self.remembered = None  # loaded on first use
        self.apple_searches = 0
        self.apple_failed = False

    # Public API -----------------------------------------------------------------

    def resolve(self, entries):
        """Return a ``Resolution`` for every entry, in order.

        Music matches are made canonical in one batch, and with the catalog on Apple
        Music the remaining MusicBrainz albums get their Apple albums.
        """
        resolutions = canonical_resolutions([self._resolve_one(entry) for entry in entries])
        if config.music_catalog_source() == Sources.APPLE_MUSIC:
            resolutions = self._apple_albums(resolutions)
        return resolutions

    def resolve_one(self, entry):
        """Resolve one entry."""
        return self.resolve([entry])[0]

    def _resolve_one(self, entry):
        override = self.overrides.get(entry.key)
        if override == overrides_module.SKIP:
            return Resolution(entry, None, "skip")
        if override:
            return self._checked(entry, match_from_ref(override, entry), "override")
        if entry.ids.get("review"):  # held for review: only an override can match it
            return Resolution(entry, None, "review", entry.ids["review"])
        if entry.ids.get("ref"):
            try:
                return self._checked(entry, match_from_ref(entry.ids["ref"], entry), "ref")
            except ValueError as error:
                return Resolution(entry, None, "", str(error))
        direct = self._direct_person(entry) if self.people else self._direct(entry)
        if direct is not None:
            return self._checked(entry, direct, "id")
        remembered = self._from_snapshot(entry)
        if remembered is not None:
            return self._checked(entry, remembered, "snapshot")
        return self._cached_network(entry)

    # Steps ----------------------------------------------------------------------

    def _allowed(self, entry):
        """Return the media types this entry may resolve to."""
        if self.people:
            return (PERSON_MEDIA_TYPE,)
        if entry.media_type:
            return (entry.media_type,) if entry.media_type in self.definition.media_types else ()
        return self.definition.media_types

    def _checked(self, entry, match, how):
        allowed = (PERSON_MEDIA_TYPE,) if self.people else self.definition.media_types
        if match.media_type not in allowed:
            return Resolution(entry, None, "", f"{match.media_type} is not one of this list's media types")
        return Resolution(entry, match, how)

    def _single(self, entry, choices):
        allowed = [value for value in self._allowed(entry) if value in choices]
        return allowed[0] if len(allowed) == 1 else None

    def _direct(self, entry):
        ids = entry.ids

        def build(source, media_type, media_id):
            return Match(source, media_type, str(media_id), entry.title, entry.image, entry.year)

        if ids.get("tmdb"):
            media_type = self._single(entry, (MediaTypes.MOVIE.value, MediaTypes.TV.value))
            if media_type:
                return build(Sources.TMDB.value, media_type, ids["tmdb"])
        if ids.get("mal"):
            media_type = self._single(entry, (MediaTypes.ANIME.value, MediaTypes.MANGA.value))
            if media_type:
                return build(Sources.MAL.value, media_type, ids["mal"])
        simple = (
            ("igdb", Sources.IGDB.value, MediaTypes.GAME.value),
            ("mangaupdates", Sources.MANGAUPDATES.value, MediaTypes.MANGA.value),
            ("hardcover", Sources.HARDCOVER.value, MediaTypes.BOOK.value),
            ("apple_album", Sources.APPLE_MUSIC.value, MediaTypes.MUSIC.value),
            ("mbid_rg", Sources.MUSICBRAINZ.value, MediaTypes.MUSIC.value),
            ("comicvine", Sources.COMICVINE.value, MediaTypes.COMIC.value),
        )
        for id_name, source, media_type in simple:
            if ids.get(id_name) and media_type in self._allowed(entry):
                return build(source, media_type, ids[id_name])
        olid = ids.get("olid", "")
        if olid.upper().endswith("M") and MediaTypes.BOOK.value in self._allowed(entry):
            return build(Sources.OPENLIBRARY.value, MediaTypes.BOOK.value, olid.upper())
        return None

    def _apple_albums(self, resolutions):
        """Give each MusicBrainz album no MusicBrainz Item holds its Apple Music album.

        An album with an Item of its own keeps it, so data on an album that hasn't
        moved stays together. Otherwise the album of its matched or applied link row
        (any tier); with no row, Apple search through
        ``apple_music.ref_for_release_group``, up to ``MAX_APPLE_SEARCHES`` a run and
        only while Apple answers. A miss keeps the MusicBrainz match, so lists don't
        shrink. The Apple albums are then made canonical in one more batch.
        """
        mbids = {
            str(resolution.match.media_id)
            for resolution in resolutions
            if _is_music(resolution.match) and resolution.match.source == Sources.MUSICBRAINZ.value
        }
        if not mbids:
            return resolutions
        held = set(
            Item.objects.filter(
                source=Sources.MUSICBRAINZ.value,
                media_type=MediaTypes.MUSIC.value,
                media_id__in=mbids,
            ).values_list("media_id", flat=True),
        )
        links = {
            link.release_group_mbid: link
            for link in MusicAlbumLink.objects.filter(release_group_mbid__in=mbids - held)
        }
        albums = {}
        for resolution in resolutions:
            match = resolution.match
            mbid = str(match.media_id) if _is_music(match) else ""
            if mbid not in mbids or mbid in held or mbid in albums:
                continue
            link = links.get(mbid)
            if link is not None:
                if link.status in _LINKED and link.apple_album_id:
                    albums[mbid] = {"media_id": link.apple_album_id, "title": link.apple_title, "image": link.apple_image}
                continue
            album = self._search_apple(mbid, match.title or resolution.entry.title, resolution.entry.creator)
            if album is not None:
                albums[mbid] = album
        if not albums:
            return resolutions
        canonical = music_catalog.canonical_music_refs(
            [(Sources.APPLE_MUSIC.value, MediaTypes.MUSIC.value, album["media_id"]) for album in albums.values()],
        )
        found = []
        for resolution in resolutions:
            album = albums.get(str(resolution.match.media_id)) if _is_music(resolution.match) else None
            if album is None or resolution.match.source != Sources.MUSICBRAINZ.value:
                found.append(resolution)
                continue
            source, _, media_id = canonical[(Sources.APPLE_MUSIC.value, MediaTypes.MUSIC.value, album["media_id"])]
            match = replace(
                resolution.match,
                source=source,
                media_id=media_id,
                title=album["title"] or resolution.match.title,
                image=album["image"] or "",
            )
            found.append(replace(resolution, match=match))
        return found

    def _search_apple(self, mbid, title, artist):
        """Return a MusicBrainz album's Apple album from Apple search, or None."""
        if not artist or self.apple_failed or self.apple_searches >= MAX_APPLE_SEARCHES:
            return None
        self.apple_searches += 1
        try:
            return apple_music.ref_for_release_group(mbid, title, artist)
        except PROVIDER_ERRORS as error:
            logger.warning("Featured Apple Music match failed for %s: %s", mbid, sanitize(error))
            self.apple_failed = True
            return None

    def _from_snapshot(self, entry):
        """Return a match for the Item this list's latest snapshot holds for the entry.

        The poster stays the entry's own: ``Item.image`` isn't trusted for artwork.
        """
        if not self.remember:
            return None
        if self.remembered is None:
            self.remembered = remembered_items(self.definition.key)
        item = self.remembered.get(entry.key)
        if item is None or item.media_type not in self._allowed(entry):
            return None
        return Match(item.source, item.media_type, item.media_id, item.title or entry.title, entry.image, entry.year)

    def _direct_person(self, entry):
        for source in PERSON_SOURCES:
            person_id = entry.ids.get(f"{source}_person")
            if person_id:
                return Match(source, PERSON_MEDIA_TYPE, person_id, entry.title, entry.image)
        return None

    def _cache_key(self, entry):
        key = entry.key
        if key.startswith("t:"):
            scope = "|".join(self._allowed(entry))
            creators = ",".join(sorted(creator_names(entry.creator)))
            return f"{CACHE_PREFIX}{scope}:{key}|{creators}"
        return f"{CACHE_PREFIX}{key}"

    def _cached_network(self, entry):
        cache_key = self._cache_key(entry)
        if self.use_cache:
            cached = cache.get(cache_key)
            if cached == MISS:
                return Resolution(entry, None, "cache", "no match (cached)")
            if cached:
                cache.touch(cache_key, HIT_TTL)
                return self._checked(entry, Match.from_cache(cached), "cache")
        try:
            match, how = self._network(entry)
        except _Uncacheable as reason:
            return Resolution(entry, None, "", str(reason))
        except _TitleSearchesSpent as reason:
            return Resolution(entry, None, "deferred", str(reason), retry_at=timezone.now() + TITLE_SEARCH_RETRY)
        except quota.Deferred as error:
            return deferred_resolution(entry, error)
        if self.use_cache:
            cache.set(cache_key, match.as_cache() if match else MISS, HIT_TTL if match else MISS_TTL)
        if match is None:
            return Resolution(entry, None, "", "no match")
        return self._checked(entry, match, how)

    def _network(self, entry):
        """Return ``(match, how)``; raise ``_Uncacheable`` for transient failures."""
        errors = []
        steps = (
            ("imdb", self._from_imdb),
            ("steam", self._from_steam),
            ("isbn", self._from_isbns),
            ("title", self._from_title),
        )
        for how, step in steps:
            try:
                match = step(entry)
            except _Uncacheable:
                if errors:
                    raise _Uncacheable(errors[-1]) from None
                raise
            except PROVIDER_ERRORS as error:
                logger.warning("Featured resolve %s failed for %s: %s", how, entry.key, sanitize(error))
                errors.append(f"{how} lookup failed: {sanitize(error, 160)}")
                continue
            if match is not None:
                return match, how
        if errors:
            raise _Uncacheable(errors[-1])
        return None, ""

    def _from_imdb(self, entry):
        imdb_id = entry.ids.get("imdb")
        allowed = [value for value in self._allowed(entry) if value in ("movie", "tv")]
        if not imdb_id or not allowed or self.people:
            return None
        response = tmdb.find(imdb_id, "imdb_id")
        for media_type in allowed:
            for result in response.get(f"{media_type}_results") or []:
                return Match(
                    Sources.TMDB.value,
                    media_type,
                    str(result["id"]),
                    title=result.get("title") or result.get("name") or entry.title,
                    image=tmdb.get_image_url(result.get("poster_path")) if result.get("poster_path") else entry.image,
                    year=parse_year(result.get("release_date") or result.get("first_air_date")) or entry.year,
                )
        return None

    def _from_steam(self, entry):
        steam_id = entry.ids.get("steam")
        if not steam_id or MediaTypes.GAME.value not in self._allowed(entry) or self.people:
            return None
        igdb_id = igdb.external_game(steam_id)
        if not igdb_id:
            return None
        return Match(Sources.IGDB.value, MediaTypes.GAME.value, str(igdb_id), entry.title, entry.image, entry.year)

    def _from_isbns(self, entry):
        if not entry.isbns or MediaTypes.BOOK.value not in self._allowed(entry) or self.people:
            return None
        result = self.isbn_lookup(list(entry.isbns))
        return _match_from_book(result) if result else None

    def _spend_search(self):
        if self.title_searches >= self.title_budget:
            msg = f"this run's {self.title_budget} title searches are used up"
            raise _TitleSearchesSpent(msg)
        self.title_searches += 1

    def _from_title(self, entry):
        if not entry.title or self.title_budget <= 0:
            return None
        if self.people:
            return self._person_from_name(entry)
        for media_type in self._allowed(entry):
            for source in _title_search_sources(media_type):
                self._spend_search()
                response = services.search(media_type, entry.title, 1, source=source, preserve_ranking_fields=True)
                result = pick_title_match(entry, response.get("results") or [])
                if result is not None:
                    return Match(
                        str(result.get("source") or source),
                        media_type,
                        str(result["media_id"]),
                        title=result.get("title") or entry.title,
                        image=result.get("image") or "",
                        year=_result_year(result) or entry.year,
                    )
        return None

    def _person_from_name(self, entry):
        source = self.definition.params.get("person_source")
        if source not in PERSON_SOURCES:
            return None
        self._spend_search()
        wanted = normalize_title(entry.title)
        found = [
            person
            for person in services.search_people(source, entry.title, limit=10)
            if normalize_title(person.get("name")) == wanted
        ]
        if len(found) != 1:
            return None
        person = found[0]
        return Match(
            source,
            PERSON_MEDIA_TYPE,
            str(person["person_id"]),
            title=person.get("name") or entry.title,
            image=person.get("profile_url") or entry.image,
            known_for_department=person.get("known_for_department") or "",
        )


def _title_search_sources(media_type):
    """Return the sources a title search asks: for music, only the catalog's."""
    if media_type == MediaTypes.MUSIC.value:
        return (config.music_catalog_source().value,)
    return ITEM_SOURCES.get(media_type, ())


def _result_year(result):
    for key in ("release_date", "first_publish_year", "first_release_date", "year"):
        year = parse_year(result.get(key))
        if year:
            return year
    return None


def _result_creators(result):
    for key in ("author_name", "authors", "artist_credits", "artist", "creator"):
        if result.get(key):
            return result[key]
    return None


def pick_title_match(entry, results):
    """Return the one search result that strictly matches the entry, or None.

    The entry's whole title must equal a result's title, or the result's main title
    before a subtitle. Only when nothing matches that way, the entry's own main title
    is tried too ("Master of the Senate: The Years of Lyndon Johnson" finds "Master of
    the Senate"); such a match needs the year or the creator to agree, and exactly one
    result to qualify.
    """
    wanted = normalize_title(entry.title)
    if not wanted:
        return None
    heads = title_forms(entry.title) - {wanted}
    exact, main, loose = [], [], []
    for result in results:
        title = result.get("title") or result.get("name") or ""
        full = normalize_title(title)
        forms = title_forms(title)
        if full == wanted:
            bucket = exact
        elif wanted in forms:
            bucket = main
        elif heads & forms:
            bucket = loose
        else:
            continue
        year = _result_year(result)
        if entry.year and year and abs(year - entry.year) > 1:
            continue
        creators = _result_creators(result)
        if entry.creator and not creators_match(entry.creator, creators):
            continue
        credited = bool(entry.creator and creators)
        if bucket is loose and not (credited or (entry.year and year)):
            continue
        bucket.append((result, year, credited))
    if not exact and not main:
        return loose[0][0] if len(loose) == 1 else None
    # A result whose credited creator matched beats one that credits nobody (often an
    # empty stub record), even when the stub's title is the exact one.
    confirmed = [candidate for candidate in exact if candidate[2]] or [candidate for candidate in main if candidate[2]]
    candidates = confirmed or exact or main
    if entry.year:
        same_year = [candidate for candidate in candidates if candidate[1] == entry.year]
        return (same_year or candidates)[0][0]
    if len(candidates) == 1 or candidates[0][2]:
        return candidates[0][0]
    return None
