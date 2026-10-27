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
"""

from functools import partial

from django.db import transaction

from api.services import completion as completion_service
from api.services.filters import _runtime_minutes, update_item_filter_metadata
from app.mixins import collect_external_rating_item_id, collect_external_rating_item_ids
from app.models import MediaTypes

# The metadata rules each media type is filled under. Bump a type when what is stored
# for it changes, so the fill revisits its Items (Item.metadata_version).
CURRENT_METADATA_VERSION = {
    # 2: the original language.
    MediaTypes.MOVIE.value: 2,
    MediaTypes.TV.value: 2,
    MediaTypes.SEASON.value: 2,
    MediaTypes.EPISODE.value: 2,
    # 2: runtime text, details.start_date, first_publish_year, album length, themes.
    MediaTypes.ANIME.value: 2,
    MediaTypes.MANGA.value: 2,
    MediaTypes.COMIC.value: 2,
    MediaTypes.MUSIC.value: 2,
    MediaTypes.BOOK.value: 2,
    MediaTypes.GAME.value: 2,
}


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
