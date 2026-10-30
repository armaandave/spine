"""Store provider metadata on an Item and record which metadata rules filled it.

Every path that holds provider metadata for an Item stores it through
store_item_metadata. update_item_filter_metadata (api.services.filters) reads these
keys from a provider's metadata dict; a key that is missing, None or empty never
erases a stored value:

- Release date: "release_date", "first_air_date", "start_date", "end_date" and
  "first_release_date" at the top level, then "release_date", "first_release_date",
  "first_air_date", "start_date", "publish_date" and "published_date" under
  "details". Values are dates, "YYYY", "YYYY-MM" or "YYYY-MM-DD"; other integers
  (Unix timestamps) are ignored. For books only, "original_release_date" (top level or
  under "details") is read first: the book's first publication, not the edition's.
- Release year, when there is no date: "year", then "first_publish_year" (top level
  or under "details"), then a 4-digit year inside a text date such as "March 2004".
- Runtime: "runtime" (top level or under "details") in minutes, as a number or text
  such as "24 min", "1 hr 30 min", "1h 30m" or "1.5 hours". For music it is the album
  length; an album payload without "runtime" gets it from its tracklist
  (music.representative_release.media[].tracks[].length_ms) when every track has a
  length, whichever catalog (MusicBrainz or Apple Music) the payload came from.
- Facets, each a list of names or {"name": ...} dicts (top level or under "details"):
  "genres", "languages" (spoken languages) and "themes".
- "original_language" (top level only): the title's original language as one English
  name string, for example metadata["original_language"] = "Japanese". Map the ISO
  code to an English name first; a code no table names is stored as the code itself.
- Credits (app.models.ItemCredit, stats people, STAT-219), each role replaced only
  when the metadata carries its list (a missing or None list keeps what is stored):
  - movie (TMDB): directors from details.directors ({id, name}, pictures from the
    crew entry of the same person_id) and the top 15 billed of "cast" ({person_id,
    name, image}). Both need "cast" to be a list, which says credits were fetched
    (tmdb.get_directors gives [] without them);
  - TV show (TMDB): creators from details.creators ({id, name, image}, from
    created_by) and the top 15 of "cast" when it is a list;
  - music: album artists from details.artist_credits (or music.artist_credit): an
    Apple artist by artist_source and artist_id (picture: image_url), a MusicBrainz
    artist by artist_mbid. Credits with no ID, Various Artists and MusicBrainz's
    [unknown] are left out; a list with no ID at all isn't carried;
  - book: details.authors ({person_id, source, name, image_url}), the same rule.
  Seasons, episodes and other types store none.
"""

from functools import partial

from django.conf import settings
from django.db import transaction

from api.services import completion as completion_service
from api.services.filters import _runtime_minutes, update_item_filter_metadata
from app.mixins import collect_external_rating_item_id, collect_external_rating_item_ids
from app.models import ItemCredit, MediaTypes, Sources

# The metadata rules each media type is filled under. Bump a type when what is stored
# for it changes, so the fill revisits its Items (Item.metadata_version).
CURRENT_METADATA_VERSION = {
    # 2: the original language. 3: credits (directors, creators and top cast).
    MediaTypes.MOVIE.value: 3,
    MediaTypes.TV.value: 3,
    MediaTypes.SEASON.value: 2,
    MediaTypes.EPISODE.value: 2,
    # 2: runtime text, details.start_date, first_publish_year, album length, themes.
    MediaTypes.ANIME.value: 2,
    MediaTypes.MANGA.value: 2,
    MediaTypes.COMIC.value: 2,
    # 3: album artists.
    MediaTypes.MUSIC.value: 3,
    # 3: the page count, which Goodreads imports left blank. 4: authors.
    MediaTypes.BOOK.value: 4,
    MediaTypes.GAME.value: 2,
}

# Types whose Items keep credits (ItemCredit), and the cast members kept per title.
CREDIT_TYPES = (
    MediaTypes.MOVIE.value,
    MediaTypes.TV.value,
    MediaTypes.MUSIC.value,
    MediaTypes.BOOK.value,
)
CAST_LIMIT = 15
# Credits that name no person: MusicBrainz's Various Artists and [unknown], and any
# artist or author called Various Artists.
NOT_A_PERSON_IDS = {
    "89ad4ac3-39f7-470e-963a-56509c546377",
    "125ec42a-7229-4250-afc5-e057484327fe",
}
NOT_A_PERSON_NAMES = {"various artists"}
CREDIT_SOURCES = {source.value for source in Sources} - {Sources.MANUAL.value}


def is_metadata_current(item):
    """Return whether the Item was filled under the current metadata rules."""
    return item.metadata_version >= CURRENT_METADATA_VERSION.get(item.media_type, 0)


def store_item_metadata(item, metadata, *, complete=True, persist_series=True):
    """Store provider metadata on an Item; return whether it is now current.

    complete means the metadata is the provider's full record for the Item, so
    whatever is still missing doesn't exist; only then is the Item stamped with the
    current version. Empty metadata (a failed fetch) and music without an album
    length are never stamped. A complete series in the metadata is stored too.
    """
    if item is None or not metadata:
        return False
    if item.media_type == MediaTypes.MUSIC.value and _runtime_minutes(metadata) is None:
        runtime = album_runtime_minutes((metadata.get("music") or {}).get("representative_release"))
        if runtime is not None:
            metadata = {**metadata, "runtime": runtime}
    update_item_filter_metadata(item, metadata)
    if item.media_type == MediaTypes.BOOK.value:
        _store_book_pages(item, metadata)
    if item.media_type in CREDIT_TYPES:
        store_item_credits(item, metadata)
    if persist_series:
        _persist_series(item, metadata)
    if not complete or (
        item.media_type == MediaTypes.MUSIC.value and _runtime_minutes(metadata) is None
    ):
        return is_metadata_current(item)
    version = CURRENT_METADATA_VERSION.get(item.media_type, 0)
    if item.metadata_version < version:
        item.metadata_version = version
        item.save(update_fields=["metadata_version"])
    return True


def _store_book_pages(item, metadata):
    """Fill a book's blank page count from the provider (imports left it empty)."""
    if item.total_pages:
        return
    details = metadata.get("details") or {}
    for value in (
        metadata.get("total_pages"),
        metadata.get("max_progress"),
        details.get("number_of_pages"),
    ):
        try:
            pages = int(value)
        except (TypeError, ValueError):
            continue
        if pages > 0:
            item.total_pages = pages
            item.save(update_fields=["total_pages"])
            return


def store_item_credits(item, metadata):
    """Replace the credits of each role the metadata carries; return if any changed.

    One read when nothing changed; otherwise one delete and one bulk insert, in a
    transaction. No provider call.
    """
    by_role = metadata_credits(item, metadata)
    if not by_role:
        return False
    rows = [
        ItemCredit(
            item=item,
            role=role,
            person_source=person_source,
            person_id=person_id,
            name=name,
            order=order,
            image_url=image_url,
        )
        for role, entries in by_role.items()
        for order, (person_source, person_id, name, image_url) in enumerate(entries)
    ]
    stored = ItemCredit.objects.filter(item=item, role__in=list(by_role))
    fields = ("role", "person_source", "person_id", "name", "order", "image_url")
    wanted = {tuple(getattr(row, field) for field in fields) for row in rows}
    if set(stored.values_list(*fields)) == wanted:
        return False
    with transaction.atomic():
        stored.delete()
        # Two requests storing one title at once insert the same rows: keep either.
        ItemCredit.objects.bulk_create(rows, ignore_conflicts=True)
    return True


def metadata_credits(item, metadata):
    """Return ``{role: [(person_source, person_id, name, image_url), ...]}`` (STAT-219).

    Only the roles the metadata carries are keys (see the module docstring); each
    list is in the provider's order, one entry per person.
    """
    details = metadata.get("details") or {}
    if not isinstance(details, dict):
        return {}
    source = metadata.get("source") or item.source
    if item.media_type in (MediaTypes.MOVIE.value, MediaTypes.TV.value):
        roles = _screen_credits(item, metadata, details) if source == Sources.TMDB.value else {}
    elif item.media_type == MediaTypes.MUSIC.value:
        roles = _artist_credits(metadata, details)
    elif item.media_type == MediaTypes.BOOK.value:
        roles = _author_credits(details, source)
    else:
        roles = {}
    return {role: _clean_credits(entries, role) for role, entries in roles.items()}


def _screen_credits(item, metadata, details):
    """Return a TMDB movie's directors and cast, or a show's creators and cast."""
    roles = {}
    source = Sources.TMDB.value
    cast = metadata.get("cast")
    # get_directors gives [] without credits too, so directors need the cast list.
    if item.media_type == MediaTypes.MOVIE.value:
        if isinstance(cast, list) and isinstance(details.get("directors"), list):
            crew_images = {
                str(member.get("person_id")): member.get("image")
                for member in metadata.get("crew") or []
                if isinstance(member, dict)
            }
            roles[ItemCredit.Role.DIRECTOR.value] = [
                (source, person.get("id"), person.get("name"), crew_images.get(str(person.get("id"))))
                for person in details["directors"]
                if isinstance(person, dict)
            ]
    elif isinstance(details.get("creators"), list):
        roles[ItemCredit.Role.CREATOR.value] = [
            (source, person.get("id"), person.get("name"), person.get("image"))
            for person in details["creators"]
            if isinstance(person, dict)
        ]
    if isinstance(cast, list):
        roles[ItemCredit.Role.CAST.value] = [
            (source, person.get("person_id"), person.get("name"), person.get("image"))
            for person in cast
            if isinstance(person, dict)
        ]
    return roles


def _artist_credits(metadata, details):
    """Return an album's artists: Apple's by artist ID, else MusicBrainz's by MBID."""
    artists = details.get("artist_credits")
    if artists is None:
        artists = (metadata.get("music") or {}).get("artist_credit")
    entries = [
        (
            (credit.get("artist_source"), credit.get("artist_id"))
            if credit.get("artist_id")
            else (Sources.MUSICBRAINZ.value, credit.get("artist_mbid"))
        )
        + (credit.get("name"), credit.get("image_url"))
        for credit in artists or []
        if isinstance(credit, dict)
    ]
    return {ItemCredit.Role.ARTIST.value: entries} if any(entry[1] for entry in entries) else {}


def _author_credits(details, source):
    """Return a book's authors, each under its own source (Hardcover, Open Library)."""
    entries = [
        (author.get("source") or source, author.get("person_id"), author.get("name"), author.get("image_url"))
        for author in details.get("authors") or []
        if isinstance(author, dict)
    ]
    return {ItemCredit.Role.AUTHOR.value: entries} if any(entry[1] for entry in entries) else {}


def _clean_credits(entries, role):
    """Keep credits naming a person once, with IDs and names that fit, in order."""
    id_length, name_length, image_length = (
        ItemCredit._meta.get_field(field).max_length for field in ("person_id", "name", "image_url")
    )
    result = []
    seen = set()
    for person_source, raw_id, raw_name, raw_image in entries:
        person_id = str(raw_id or "").strip()
        name = str(raw_name or "").strip()
        if (
            person_source not in CREDIT_SOURCES
            or not person_id
            or len(person_id) > id_length
            or not name
            or person_id in NOT_A_PERSON_IDS
            or name.casefold() in NOT_A_PERSON_NAMES
            or (person_source, person_id) in seen
        ):
            continue
        seen.add((person_source, person_id))
        keep_image = isinstance(raw_image, str) and raw_image != settings.IMG_NONE and len(raw_image) <= image_length
        result.append((person_source, person_id, name[:name_length], raw_image if keep_image else ""))
    if role == ItemCredit.Role.CAST.value:
        return result[:CAST_LIMIT]
    return result


def album_runtime_minutes(representative_release):
    """Return the album length in minutes, or None unless every track has a length."""
    tracks = [
        track
        for medium in (representative_release or {}).get("media") or []
        for track in medium.get("tracks") or []
    ]
    if not tracks:
        return None
    lengths = [
        track.get("length_ms") or (track.get("recording") or {}).get("length_ms")
        for track in tracks
    ]
    if not all(isinstance(length, int) and length > 0 for length in lengths):
        return None
    return max(1, round(sum(lengths) / 60000))


def _persist_series(item, metadata):
    """Store the metadata's series and queue external ratings for its new members.

    New member Items join the caller's external-ratings collector when there is one
    (imports enqueue it once); otherwise they are queued in batches after the commit,
    instead of one task each.
    """
    if not (metadata.get("details") or {}).get("series_id"):
        return None
    with collect_external_rating_item_ids() as created_ids:
        series = _persist_detail_series(metadata, source=item.source, media_type=item.media_type)
    pending = sorted(item_id for item_id in created_ids if not collect_external_rating_item_id(item_id))
    if pending:
        from app.tasks import enqueue_external_rating_batches

        transaction.on_commit(partial(enqueue_external_rating_batches, pending), robust=True)
    return series


def _persist_detail_series(metadata, *, source, media_type):
    """Persist a complete canonical series already present in detail metadata."""
    details = metadata.get("details") or {}
    series_id = details.get("series_id")
    if not series_id:
        return None

    related = metadata.get("related") or {}
    if media_type == MediaTypes.ANIME.value:
        members = related.get("series") or []
    elif media_type == MediaTypes.GAME.value:
        members = related.get("collection") or []
    elif media_type == MediaTypes.BOOK.value:
        members = related.get(details.get("series_name")) or []
    elif media_type == MediaTypes.MOVIE.value:
        members = next(
            (
                values
                for key, values in related.items()
                if key not in {"recommendations", "similar"} and values
            ),
            [],
        )
    else:
        members = []

    return completion_service.persist_complete_series({
        "series_id": series_id,
        "source": details.get("series_source") or source,
        "media_type": details.get("series_media_type") or media_type,
        "name": details.get("series_name") or "",
        "item_count": len(members),
        "items": members,
    })
