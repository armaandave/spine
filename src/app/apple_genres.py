"""Apple's music genre IDs, used by album chips and genre charts.

Names/parents recorded from Apple's public music genre taxonomy on 2026-10-27:
https://itunes.apple.com/WebObjects/MZStoreServices.woa/ws/genres?id=34
Only identifiers, names and parents are stored; no album matching is involved.
"""

import json
from pathlib import Path

GENRES = json.loads((Path(__file__).parent / "data" / "apple_music_genres.json").read_text())
BY_ID = {genre["id"]: genre for genre in GENRES}
# A few names occur both at the root and in subgenres. Prefer the root, then
# Apple's older, lower-numbered genre ID for an ambiguous subgenre name.
BY_NAME = {}
for _genre in sorted(GENRES, key=lambda row: (row["parent"] is not None, int(row["id"]))):
    BY_NAME.setdefault(_genre["name"].casefold(), _genre["id"])
# Names older installed pages send, before album chips used Apple's own spelling.
ALIASES = {
    "hip hop": "Hip-Hop/Rap",
    "hip-hop": "Hip-Hop/Rap",
    "r&b": "R&B/Soul",
    "alternative rock": "Alternative",
    "electronica": "Electronic",
    "neo soul": "Neo-Soul",
    "rock and roll": "Rock & Roll",
    "singer-songwriter": "Singer/Songwriter",
    "contemporary r&b": "Contemporary R&B",
    "conscious hip hop": "Hip-Hop/Rap",
    "jazz rap": "Hip-Hop/Rap",
    "political hip hop": "Hip-Hop/Rap",
    "alternative hip hop": "Alternative Rap",
    "west coast hip hop": "West Coast Rap",
    "southern hip hop": "Dirty South",
    "art pop": "Pop",
    "dance-pop": "Pop",
    "reggaeton": "Urbano latino",
}


def genre_id(name):
    """Resolve an Apple name/ID, or a supported legacy chip, without a request."""
    value = " ".join(str(name or "").split())
    if value in BY_ID:
        return value
    key = value.casefold()
    return BY_NAME.get(key) or BY_NAME.get(ALIASES.get(key, "").casefold())


def refresh_stored_genres(*, apply=False):
    """Refresh only genre facets of Apple Items, in catalog batches; preserve logs.

    A dry run makes no provider calls. Missing albums keep their saved genres.
    The production wrapper backs up before applying this resumable operation.
    """
    from django.db import transaction  # noqa: PLC0415
    from django.utils import timezone  # noqa: PLC0415

    from api.services import stats_cache  # noqa: PLC0415
    from api.services.filters import update_item_filter_metadata  # noqa: PLC0415
    from api.services.media import drop_music_detail_cache  # noqa: PLC0415
    from app import music_catalog, music_listing  # noqa: PLC0415
    from app.models import Item, MediaTypes, Sources  # noqa: PLC0415
    from app.providers import apple_music  # noqa: PLC0415

    items = list(Item.objects.filter(source=Sources.APPLE_MUSIC.value, media_type=MediaTypes.MUSIC.value))
    counts = {"Apple albums": len(items), "updated": 0, "missing": 0}
    if not apply:
        return counts
    for start in range(0, len(items), 100):
        batch = items[start:start + 100]
        albums = apple_music.albums([item.media_id for item in batch], views=(music_listing.FAMILY_VIEW,), background=True)
        by_id = {str(album["id"]): album for album in albums}
        for item in batch:
            album = by_id.get(item.media_id)
            if album is None:
                counts["missing"] += 1
                continue
            members = next(
                group for group in music_listing.group_listings(apple_music._family(album))
                if any(str(member.get("id")) == str(album["id"]) for member in group)
            )
            best = music_listing.pick_content_member(members, today=timezone.localdate())
            attributes = best.get("attributes") or {}
            if "genreNames" not in attributes:
                counts["missing"] += 1
                continue
            genres = music_catalog.apple_genre_names(attributes["genreNames"])
            with transaction.atomic():
                update_item_filter_metadata(item, {"genres": genres})
            drop_music_detail_cache(Sources.APPLE_MUSIC.value, item.media_id)
            counts["updated"] += 1
    if counts["updated"]:
        stats_cache.bump_epoch()
    return counts
