"""Library, list, and diary titles that need stored availability (ST-102, ST-400)."""

from collections import Counter, defaultdict
from itertools import batched

from django.db.models import Q
from django.utils import timezone

from app.models import TV, DiaryEntry, MediaTypes, Movie, Season, Sources
from lists.models import CustomListItem
from streaming.models import StreamingAvailability
from streaming.services import FRESH_FOR

# Seasons and episodes use their show's availability (ST-002).
TITLE_TYPE_BY_MEDIA_TYPE = {
    MediaTypes.MOVIE.value: "movie",
    MediaTypes.TV.value: "tv",
    MediaTypes.SEASON.value: "tv",
    MediaTypes.EPISODE.value: "tv",
}
LOOKUP_CHUNK_SIZE = 500


def library_titles(user=None):
    """Return ``{(title_type, tmdb_id)}`` for tracked, listed, and logged TMDB titles.

    Tracked means a Movie, TV, or Season row (episodes always sit under a
    tracked season); listed means an item in a custom list the user owns or
    collaborates on; logged means a diary entry, which for shows, seasons, and
    episodes need not have a tracking row. ``user=None`` covers every user.
    """
    titles = set()
    for model, title_type in ((Movie, "movie"), (TV, "tv"), (Season, "tv")):
        rows = model.objects.filter(item__source=Sources.TMDB.value)
        if user is not None:
            rows = rows.filter(user=user)
        titles.update(
            (title_type, media_id)
            for media_id in rows.order_by().values_list("item__media_id", flat=True).distinct()
        )
    listed = CustomListItem.objects.all()
    logged = DiaryEntry.objects.all()
    if user is not None:
        listed = listed.filter(Q(custom_list__owner=user) | Q(custom_list__collaborators=user))
        logged = logged.filter(user=user)
    for rows in (listed, logged):
        titles.update(
            (TITLE_TYPE_BY_MEDIA_TYPE[media_type], media_id)
            for media_type, media_id in rows.filter(
                item__source=Sources.TMDB.value,
                item__media_type__in=list(TITLE_TYPE_BY_MEDIA_TYPE),
            )
            .order_by()
            .values_list("item__media_type", "item__media_id")
            .distinct()
        )
    return titles


def titles_needing_refresh(titles, regions, *, now=None):
    """Return ``(missing, stale)`` title lists for the given regions.

    ``missing`` titles lack a snapshot in at least one region. ``stale``
    titles have one everywhere but at least one is older than FRESH_FOR; they
    are ordered oldest first.
    """
    regions = set(regions)
    if not titles or not regions:
        return [], []
    cutoff = (now or timezone.now()) - FRESH_FOR
    ids_by_type = defaultdict(list)
    for title_type, tmdb_id in titles:
        ids_by_type[title_type].append(tmdb_id)
    counts = Counter()
    oldest = {}
    for title_type, tmdb_ids in ids_by_type.items():
        for chunk in batched(sorted(tmdb_ids), LOOKUP_CHUNK_SIZE):
            rows = StreamingAvailability.objects.filter(
                title_type=title_type,
                tmdb_id__in=chunk,
                region__in=regions,
            ).values_list("tmdb_id", "fetched_at")
            for tmdb_id, fetched_at in rows:
                key = (title_type, tmdb_id)
                counts[key] += 1
                oldest[key] = min(oldest.get(key, fetched_at), fetched_at)
    missing = sorted(title for title in titles if counts[title] < len(regions))
    stale = sorted(
        (title for title in titles if counts[title] >= len(regions) and oldest[title] < cutoff),
        key=lambda title: (oldest[title], title),
    )
    return missing, stale
