import logging
import re
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from functools import reduce
from operator import or_

from django.apps import apps
from django.conf import settings
from django.db import models
from django.db.models import (
    Avg,
    Case,
    DateField,
    F,
    FilteredRelation,
    OuterRef,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import (
    ExtractHour,
    ExtractMinute,
    ExtractSecond,
    Lower,
    TruncDate,
)
from django.db.models.lookups import Exact
from django.utils import timezone
from django.utils.dateparse import parse_date
from requests import RequestException
from rest_framework.exceptions import ValidationError

from app import exposure, user_calendar
from app.external_ratings import RATING_SOURCES, rating_source_is_exposed
from app.models import (
    ExternalRating,
    Item,
    ItemFilterFacet,
    MediaTypes,
    Sources,
    Status,
)

logger = logging.getLogger(__name__)

LEGACY_RATING_SORTS = {
    "letterboxd_rating": "letterboxd",
    "imdb_rating": "imdb",
    "rotten_tomatoes_rating": "tomatoes",
}

API_SORTS = [
    {"value": "title", "label": "Title"},
    {"value": "release_date", "label": "Release Date"},
    {"value": "your_rating", "label": "Your Rating"},
    {"value": "average_rating", "label": "Average Rating"},
]

DESC_SORTS = {
    "popularity",
    "release_date",
    "your_rating",
    "average_rating",
    "letterboxd_rating",
    "imdb_rating",
    "rotten_tomatoes_rating",
    "consumed_at",
    "date_added",
}

METADATA_SORTS = {"release_date"}
SHORT_FILM_MINUTES = 40
FACET_VALUE_MAX_LENGTH = 100
RUNTIME_HOURS = re.compile(r"(\d+(?:\.\d+)?)\s*h")
RUNTIME_MINUTES = re.compile(r"(\d+(?:\.\d+)?)\s*m")
TEXT_YEAR = re.compile(r"(?<!\d)(\d{4})(?!\d)")
MIN_YEAR = 1000
MAX_YEAR = 9999


def values(params, key):
    """Return non-empty repeated query values."""
    if not hasattr(params, "getlist"):
        value = params.get(key)
        return [value] if value not in (None, "") else []
    return [value for value in params.getlist(key) if value not in (None, "")]


def bool_param(params, key):
    """Return a boolean query parameter when present."""
    value = params.get(key)
    if value is None:
        return None
    return str(value).lower() in {"1", "true", "yes", "on"}


def decimal_param(params, key):
    """Return a decimal query parameter when valid."""
    value = params.get(key)
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def int_param(params, key):
    """Return an integer query parameter when valid."""
    value = params.get(key)
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def date_param(params, key):
    """Return an ISO date query parameter when valid."""
    value = params.get(key)
    if not value:
        return None
    return parse_date(value)


def update_item_filter_metadata(item, metadata):
    """Cache provider fields needed for fast collection filtering.

    A provider value fills or replaces the stored one; a missing value (None, or an
    empty facet list) never erases what is stored. Nothing is written when nothing
    changed and the Item was cached before. Returns whether anything was written.
    The metadata keys read are listed in api.services.item_metadata.
    """
    if not item or not metadata:
        return False

    release_date = _release_date(metadata, item.media_type)
    # A year without a date never replaces the year of a stored date, so the two agree.
    if release_date is None and item.release_date is not None:
        release_year = item.release_date.year
    else:
        release_year = _release_year(metadata, release_date, item.media_type)
    updates = {
        "release_date": release_date,
        "release_year": release_year,
        "runtime_minutes": _runtime_minutes(metadata),
    }
    changed = []
    for field, value in updates.items():
        if value is not None and getattr(item, field) != value:
            setattr(item, field, value)
            changed.append(field)

    facets_changed = _replace_facets(item, _metadata_facets(metadata))
    if changed or facets_changed or item.filter_metadata_updated_at is None:
        item.filter_metadata_updated_at = timezone.now()
        item.save(update_fields=[*changed, "filter_metadata_updated_at"])
        return True
    return False


def apply_item_filters(queryset, params, *, item_path="item__"):
    """Apply shared Item-backed filters to a queryset."""
    query = params.get("q", "").strip()
    if query:
        queryset = queryset.filter(**{f"{item_path}title__icontains": query})

    media_types = values(params, "media_type")
    if media_types:
        queryset = queryset.filter(**{f"{item_path}media_type__in": media_types})

    year_values = values(params, "year")
    if year_values:
        queryset = queryset.filter(**{f"{item_path}release_year__in": year_values})

    year_min = int_param(params, "year_min")
    if year_min is not None:
        queryset = queryset.filter(**{f"{item_path}release_year__gte": year_min})

    year_max = int_param(params, "year_max")
    if year_max is not None:
        queryset = queryset.filter(**{f"{item_path}release_year__lte": year_max})

    release_status = params.get("release_status")
    if release_status == "released":
        queryset = queryset.filter(**{f"{item_path}release_date__lte": timezone.localdate()})
    elif release_status == "unreleased":
        queryset = queryset.filter(**{f"{item_path}release_date__gt": timezone.localdate()})

    length = params.get("length")
    if length == "feature":
        queryset = queryset.filter(**{f"{item_path}runtime_minutes__gte": SHORT_FILM_MINUTES})
    elif length == "short":
        queryset = queryset.filter(**{f"{item_path}runtime_minutes__lt": SHORT_FILM_MINUTES})

    queryset = _apply_facet_filter(queryset, params, item_path, "genre")
    queryset = _apply_facet_filter(queryset, params, item_path, "language")
    queryset = _apply_facet_exclude(queryset, params, item_path, "genre")
    queryset = _apply_facet_exclude(queryset, params, item_path, "language")
    return queryset


def ensure_filter_metadata(queryset, params, *, item_path="item__", force=False, limit=1000):
    """Populate missing Item filter metadata before DB filtering/sorting.

    An Item filled under the current metadata rules is skipped even when a value is
    missing: its provider has nothing more, so asking again would only repeat the call.
    """
    needed_facets = set()
    needs_year = force or bool(values(params, "year")) or params.get("year_min") or params.get("year_max")
    needs_release_date = (
        force
        or (params.get("sort") or params.get("ordering")) in METADATA_SORTS
        or params.get("release_status") in {"released", "unreleased"}
    )
    needs_runtime = force or params.get("length") in {"feature", "short"}
    if force or values(params, "genre") or values(params, "exclude_genre"):
        needed_facets.add(ItemFilterFacet.FacetType.GENRE)
    if force or values(params, "language") or values(params, "exclude_language"):
        needed_facets.add(ItemFilterFacet.FacetType.LANGUAGE)
    if not (needs_year or needs_release_date or needs_runtime or needed_facets):
        return

    from api.services.item_metadata import is_metadata_current, store_item_metadata
    from app.providers import services as provider_services

    item_ids = queryset.values_list(f"{item_path}id", flat=True)
    # Apple Music albums are filled by the paced metadata fill (app.metadata_fill), on
    # Apple's background session; here they would fall through to MusicBrainz.
    candidates = (
        Item.objects.filter(id__in=item_ids)
        .exclude(source=Sources.APPLE_MUSIC.value)
        .prefetch_related("filter_facets")[:limit]
    )
    for item in candidates:
        if is_metadata_current(item):
            continue
        facets = {facet.facet_type for facet in item.filter_facets.all()}
        has_needed_values = (
            (not needs_year or item.release_year is not None)
            and (not needs_release_date or item.release_date is not None)
            and (not needs_runtime or item.runtime_minutes is not None)
            and needed_facets.issubset(facets)
        )
        if has_needed_values:
            continue
        # Album length needs the track enrichment (several MusicBrainz calls); filters
        # need only the album itself, so music is stored without being stamped.
        is_music = item.media_type == MediaTypes.MUSIC.value
        try:
            metadata = provider_services.get_media_metadata(
                item.media_type,
                item.media_id,
                item.source,
                season_numbers=[item.season_number] if item.season_number else None,
                episode_number=item.episode_number,
                include_music_enrichment=not is_music,
            )
        except (
            provider_services.ProviderAPIError,
            RequestException,
            NotImplementedError,
            TypeError,
            ValueError,
        ) as error:
            logger.debug(
                "Skipping filter metadata backfill for item %s: %s",
                item.id,
                error,
            )
            continue
        store_item_metadata(item, metadata, complete=not is_music)


def apply_rating_range(queryset, params, field):
    """Apply rating_min/rating_max to a numeric field."""
    rating_min = decimal_param(params, "rating_min")
    if rating_min is not None:
        queryset = queryset.filter(**{f"{field}__gte": rating_min})
    rating_max = decimal_param(params, "rating_max")
    if rating_max is not None:
        queryset = queryset.filter(**{f"{field}__lte": rating_max})
    return queryset


def log_is_date_carrier(field="consumed_at"):
    """Match logs stored at exactly 00:00:00 UTC: a calendar-date carrier, not a moment.

    Single-weight, TV and MAL-export logs store their calendar day this way.
    """
    return Q(
        Exact(ExtractHour(field, tzinfo=UTC), 0),
        Exact(ExtractMinute(field, tzinfo=UTC), 0),
        Exact(ExtractSecond(field, tzinfo=UTC), 0),
    )


def log_day_expression(owner, field="consumed_at"):
    """Return a log's calendar day, decided per row.

    A carrier keeps its UTC date; any other time is a moment, read on the owner's
    calendar (app.user_calendar), so every viewer gets the same day.
    """
    return Case(
        When(log_is_date_carrier(field), then=TruncDate(field, tzinfo=UTC)),
        default=TruncDate(field, tzinfo=user_calendar.calendar_tz(owner)),
        output_field=DateField(),
    )


def log_day_range_q(owner, start_date=None, end_date=None, field="consumed_at"):
    """Match logs whose calendar day (log_day_expression) is within the inclusive dates.

    Carriers get UTC bounds and other rows bounds on the owner's calendar. The upper
    bound is "before the next day", so no time on the last day falls outside.
    """
    if start_date is None and end_date is None:
        return Q()
    zone = user_calendar.calendar_tz(owner)
    utc_bounds = Q()
    owner_bounds = Q()
    if start_date is not None:
        utc_bounds &= Q(**{f"{field}__gte": datetime.combine(start_date, time.min, tzinfo=UTC)})
        owner_bounds &= Q(**{f"{field}__gte": datetime.combine(start_date, time.min, tzinfo=zone)})
    if end_date is not None:
        next_day = end_date + timedelta(days=1)
        utc_bounds &= Q(**{f"{field}__lt": datetime.combine(next_day, time.min, tzinfo=UTC)})
        owner_bounds &= Q(**{f"{field}__lt": datetime.combine(next_day, time.min, tzinfo=zone)})
    carrier = log_is_date_carrier(field)
    return (carrier & utc_bounds) | (~carrier & owner_bounds)


def apply_diary_date_range(queryset, params, *, owner, field="consumed_at"):
    """Apply watched_from/watched_to to diary logs by their calendar day."""
    watched_from = date_param(params, "watched_from")
    watched_to = date_param(params, "watched_to")
    if watched_from is None and watched_to is None:
        return queryset
    return queryset.filter(log_day_range_q(owner, watched_from, watched_to, field))


# Item filters that tv_rollup matches through the log's title (STAT-400).
TITLE_MATCHED_PARAMS = ("year", "year_min", "year_max", "genre")
SHOW_PART_TYPES = (MediaTypes.SEASON.value, MediaTypes.EPISODE.value)


def diary_item_q(item_id, *, tv_rollup=False):
    """Match logs of one Item; with tv_rollup a show brings its seasons and episodes.

    Any other Item, a season included, stays exact (STAT-400).
    """
    exact = Q(item_id=item_id)
    if not tv_rollup or not str(item_id).isdigit():
        return exact
    show = Item.objects.filter(
        id=item_id,
        media_type=MediaTypes.TV.value,
        season_number__isnull=True,
        episode_number__isnull=True,
    )
    return exact | Q(
        item__media_type__in=SHOW_PART_TYPES,
        item__source=Subquery(show.values("source")[:1]),
        item__media_id=Subquery(show.values("media_id")[:1]),
    )


def item_filter_params(params):
    """Return the params apply_item_filters should read for a diary request.

    With tv_rollup, the title-matched filters move to apply_diary_drilldown_filters.
    """
    if not bool_param(params, "tv_rollup"):
        return params
    remaining = params.copy()
    for key in TITLE_MATCHED_PARAMS:
        remaining.pop(key, None)
    return remaining


def apply_diary_drilldown_filters(queryset, params, *, owner):
    """Apply the stats drill-down filters to the owner's diary logs (STAT-400).

    - ``genre_group`` (repeatable): a genre or theme value in a shared group, from
      the owner's own stored spellings, each matched on its own media type. Music
      never matches; an unknown key matches nothing.
    - ``original_language`` (repeatable): the title's original language, exactly.
    - ``tv_rollup=true``: these and ``year``, ``year_min``, ``year_max`` and
      ``genre`` match a season or episode log through its show's Item (STAT-102),
      or its own Item when the show Item doesn't exist.

    Stored values only: nothing here calls a provider.
    """
    from api.services import genre_groups
    from api.services.stats_scope import StatsScope

    target = "item_id"
    if bool_param(params, "tv_rollup"):
        queryset = queryset.annotate(drilldown_title_id=StatsScope.title_id())
        target = "drilldown_title_id"
        queryset = _apply_title_item_filters(queryset, params, target)

    group_keys = values(params, "genre_group")
    if group_keys:
        pairs = set()
        for key in dict.fromkeys(group_keys):
            pairs.update(genre_groups.raw_values_for(owner, key))
        if not pairs:
            return queryset.none()
        by_type = defaultdict(set)
        for value, media_type in pairs:
            by_type[media_type].add(value)
        matching = ItemFilterFacet.objects.filter(
            facet_type__in=genre_groups.GENRE_FACET_TYPES,
        ).filter(
            reduce(
                or_,
                (
                    Q(item__media_type=media_type, value__in=sorted(type_values))
                    for media_type, type_values in sorted(by_type.items())
                ),
            ),
        )
        queryset = queryset.filter(**{f"{target}__in": matching.values("item_id")})

    languages = values(params, "original_language")
    if languages:
        matching = ItemFilterFacet.objects.filter(
            facet_type=ItemFilterFacet.FacetType.ORIGINAL_LANGUAGE,
            value__in=languages,
        )
        queryset = queryset.filter(**{f"{target}__in": matching.values("item_id")})
    return apply_credit_filter(queryset, params, target)


def apply_credit_filter(queryset, params, target="item_id"):
    """Keep logs whose title credits a person: ``credit=<source>:<id>`` (STAT-219).

    Repeatable (any of them matches); ``role`` (repeatable) narrows to those roles.
    With tv_rollup the target is the log's title, so a season or episode log matches
    through its show's credits. A value without a source and an ID matches nothing.
    """
    from app.models import ItemCredit

    people = values(params, "credit")
    if not people:
        return queryset
    match = Q(pk__in=[])
    for value in people:
        person_source, _, person_id = value.partition(":")
        if person_source and person_id:
            match |= Q(person_source=person_source, person_id=person_id)
    matching = ItemCredit.objects.filter(match)
    roles = values(params, "role")
    if roles:
        matching = matching.filter(role__in=roles)
    return queryset.filter(**{f"{target}__in": matching.values("item_id")})


def apply_stats_count_filters(queryset, params, *, owner):
    """Keep the owner's logs that a Stats number counts (STAT-400).

    - ``repeat=true``: the logs Stats counts as repeats (STAT-108).
    - ``multi_title_days=true``: the logs on days Stats counts as days with 2+ titles
      (STAT-106 events on the owner's calendar). The days are those of the stats tab
      the request's ``media_type`` values name: one type bucket is that tab (TV with
      its seasons and episodes), anything else is Global.

    Both use the Stats helpers themselves, over the owner's own view. Whether a log is a
    repeat, and which titles a day holds, don't depend on the range, so the stats range
    is ``watched_from``/``watched_to`` when both are given (fewer rows to work out) and
    All Time otherwise; apply_diary_date_range narrows the rest.
    """
    repeat = bool_param(params, "repeat")
    multi_title_days = bool_param(params, "multi_title_days")
    if not (repeat or multi_title_days):
        return queryset
    from api.services import stats_v2_activity
    from api.services.stats_scope import AUDIENCE_OWNER, TV_ITEM_TYPES, StatsScope

    start_date = date_param(params, "watched_from")
    end_date = date_param(params, "watched_to")
    if start_date is None or end_date is None or start_date > end_date:
        start_date = end_date = None
    scope = StatsScope(owner=owner, viewer=owner, start_date=start_date, end_date=end_date, audience=AUDIENCE_OWNER)
    if repeat:
        queryset = queryset.filter(id__in=sorted(stats_v2_activity.rewatch_ids(scope)))
    if multi_title_days:
        buckets = {
            MediaTypes.TV.value if media_type in TV_ITEM_TYPES else media_type
            for media_type in values(params, "media_type")
        }
        tab = buckets.pop() if len(buckets) == 1 else None
        if tab not in exposure.primary_media_types():
            tab = None
        days = stats_v2_activity.multi_title_days(scope.with_media_type(tab).events)
        queryset = queryset.annotate(stats_drill_day=log_day_expression(owner)).filter(stats_drill_day__in=sorted(days))
    return queryset


def _apply_title_item_filters(queryset, params, target):
    """Apply year, year_min, year_max and genre to each log's title Item."""
    title_items = Item.objects.all()
    filtered = False
    year_values = [year for year in (_year_value(value) for value in values(params, "year")) if year]
    if values(params, "year"):
        title_items = title_items.filter(release_year__in=year_values)
        filtered = True
    year_min = int_param(params, "year_min")
    if year_min is not None:
        title_items = title_items.filter(release_year__gte=year_min)
        filtered = True
    year_max = int_param(params, "year_max")
    if year_max is not None:
        title_items = title_items.filter(release_year__lte=year_max)
        filtered = True
    if filtered:
        queryset = queryset.filter(**{f"{target}__in": title_items.values("id")})
    genres = values(params, "genre")
    if genres:
        matching = ItemFilterFacet.objects.filter(
            facet_type=ItemFilterFacet.FacetType.GENRE,
            value__in=genres,
        )
        queryset = queryset.filter(**{f"{target}__in": matching.values("item_id")})
    return queryset


def apply_watched_range(queryset, params, field):
    """Apply watched_from/watched_to to a DateTimeField."""
    watched_from = date_param(params, "watched_from")
    if watched_from:
        queryset = queryset.filter(**{f"{field}__date__gte": watched_from})
    watched_to = date_param(params, "watched_to")
    if watched_to:
        queryset = queryset.filter(**{f"{field}__date__lte": watched_to})
    return queryset


def apply_user_status_filter(queryset, user, status, *, item_id_field="item_id"):
    """Filter an Item-bearing queryset by the viewer's tracking status."""
    if not status or status == "All":
        return queryset
    tracked_only = str(status).lower() == "tracked"

    status_query = Q()
    for media_type in exposure.user_owned_media_types():
        model = apps.get_model("app", media_type)
        media_queryset = model.objects.filter(user=user)
        if tracked_only:
            media_queryset = media_queryset.exclude(status=Status.PLANNING.value)
        else:
            media_queryset = media_queryset.filter(status=status)
        status_query |= Q(
            **{
                "item__media_type": media_type,
                f"{item_id_field}__in": media_queryset.values("item_id"),
            },
        )
    return queryset.filter(status_query)


def annotate_user_rating(queryset, user, *, item_id_field="item_id", annotation="user_rating"):
    """Annotate mixed Item querysets with the current user's tracking score."""
    cases = []
    for media_type in exposure.user_owned_media_types():
        model = apps.get_model("app", media_type)
        cases.append(
            When(
                item__media_type=media_type,
                then=Subquery(
                    model.objects.filter(
                        user=user,
                        item_id=OuterRef(item_id_field),
                    ).values("score")[:1],
                ),
            ),
        )
    return queryset.annotate(
        **{
            annotation: Case(
                *cases,
                default=Value(None),
                output_field=models.DecimalField(max_digits=3, decimal_places=1),
            ),
        },
    )


def order_queryset(
    queryset,
    params,
    *,
    item_path="item__",
    your_rating_field=None,
    default_sort="title",
    extra_sorts=None,
    rating_scope_queryset=None,
    tiebreaks=None,
):
    """Apply shared sort options with stable null handling.

    ``tiebreaks`` maps a sort field to fields that order its ties, in the same
    direction, before the title fallback.
    """
    sort = params.get("sort") or params.get("ordering") or default_sort
    rating_source = rating_source_for_sort(sort)
    if rating_source:
        applicable_sources = _applicable_rating_sources(
            rating_scope_queryset if rating_scope_queryset is not None else queryset,
            params,
            item_path,
        )
        if rating_source not in applicable_sources:
            raise ValidationError({
                "sort": [
                    f"External rating source '{rating_source}' is not applicable to this collection.",
                ],
            })

    direction = params.get("direction")
    if direction not in {"asc", "desc"}:
        direction = "desc" if rating_source or sort in DESC_SORTS else "asc"
    descending = direction == "desc"

    if rating_source:
        relation = f"{item_path}external_ratings"
        queryset = queryset.alias(
            external_rating_sort=FilteredRelation(
                relation,
                condition=Q(
                    **{
                        f"{relation}__rating_source": rating_source,
                        f"{relation}__status": ExternalRating.Status.AVAILABLE,
                    },
                ),
            ),
        )
        value_order = (
            F("external_rating_sort__value").desc(nulls_last=True)
            if descending
            else F("external_rating_sort__value").asc(nulls_last=True)
        )
        return queryset.order_by(
            value_order,
            Lower(f"{item_path}title").asc(),
            f"{item_path}id",
            "id",
        )

    sort_fields = {
        "title": f"{item_path}title",
        "release_date": f"{item_path}release_date",
        **(extra_sorts or {}),
    }

    if sort == "average_rating":
        queryset = queryset.annotate(
            average_rating=Avg(
                f"{item_path}diaryentry__rating",
                filter=~Q(**{f"{item_path}diaryentry__visibility": "private"}),
            ),
        )
        return _order_by_field(queryset, "average_rating", descending, item_path)

    if sort == "your_rating" and your_rating_field:
        return _order_by_field(queryset, your_rating_field, descending, item_path)

    field = sort_fields.get(sort)
    if field is None:
        field = sort_fields[default_sort]

    if sort == "title":
        title_order = Lower(field).desc() if descending else Lower(field).asc()
        return queryset.order_by(title_order, "-id" if descending else "id")

    return _order_by_field(queryset, field, descending, item_path, (tiebreaks or {}).get(field, ()))


def filter_options_for_items(queryset, params=None, *, item_path="item__"):
    """Return available facet values for an Item-backed queryset."""
    item_ids = queryset.values_list(f"{item_path}id", flat=True)
    facets = ItemFilterFacet.objects.filter(item_id__in=item_ids)
    rating_sorts = [
        {
            "value": f"rating:{source}",
            "label": f"{RATING_SOURCES[source]['label']} Rating",
        }
        for source in _applicable_rating_sources(queryset, params or {}, item_path)
    ]
    return {
        "sorts": [*API_SORTS, *rating_sorts],
        "genres": _facet_values(facets, ItemFilterFacet.FacetType.GENRE),
        "languages": _facet_values(facets, ItemFilterFacet.FacetType.LANGUAGE),
        "years": list(
            Item.objects.filter(id__in=item_ids, release_year__isnull=False)
            .order_by("-release_year")
            .values_list("release_year", flat=True)
            .distinct(),
        ),
    }


def rating_source_for_sort(sort):
    """Return the registry key for a canonical or legacy rating sort."""
    if sort in LEGACY_RATING_SORTS:
        return LEGACY_RATING_SORTS[sort]
    if sort.startswith("rating"):
        if not sort.startswith("rating:"):
            raise ValidationError({"sort": ["Use rating:<rating_source>."]})
        parts = sort.split(":")
        if len(parts) != 2 or not parts[1]:
            raise ValidationError({"sort": ["Use rating:<rating_source>."]})
        source = parts[1]
        if source not in RATING_SOURCES:
            raise ValidationError({
                "sort": [f"Unknown external rating source '{source}'."],
            })
        return source
    return None


def _applicable_rating_sources(queryset, params, item_path):
    """Return registry sources matching represented Item identities."""
    media_types = values(params, "media_type")
    if media_types:
        queryset = queryset.filter(**{f"{item_path}media_type__in": media_types})
    identities = set(
        queryset.order_by()
        .values_list(f"{item_path}source", f"{item_path}media_type")
        .distinct(),
    )
    return [
        source
        for source, definition in RATING_SOURCES.items()
        if rating_source_is_exposed(source)
        and any(
            item_source in definition["item_sources"]
            and media_type in definition["media_types"]
            for item_source, media_type in identities
        )
    ]


def person_rating_source(credit_list, params):
    """Validate and return an external rating source for a person sort."""
    sort = params.get("sort") or params.get("ordering") or "average_rating"
    source = rating_source_for_sort(sort)
    if not source:
        return None
    if not settings.EXTERNAL_RATING_PERSON_PREPARATION_ENABLED:
        raise ValidationError({
            "sort": ["External rating filmography sorts are not enabled."],
        })

    media_types = set(values(params, "media_type"))
    definition = RATING_SOURCES[source]
    if not any(
        (not media_types or credit.get("media_type") in media_types)
        and credit.get("source") in definition["item_sources"]
        and credit.get("media_type") in definition["media_types"]
        for credit in credit_list
    ):
        raise ValidationError({
            "sort": [
                f"External rating source '{source}' is not applicable to this filmography.",
            ],
        })
    return source


def apply_person_credit_filters(
    credit_list,
    params,
    *,
    rating_source=None,
    default_sort=None,
):
    """Apply in-memory filters to provider person credits."""
    active_keys = {
        "media_type",
        "year",
        "year_min",
        "year_max",
        "release_status",
        "length",
        "genre",
        "language",
        "exclude_genre",
        "exclude_language",
        "rating_min",
        "rating_max",
        "sort",
        "ordering",
        "direction",
    }
    if default_sort is None and not any(values(params, key) for key in active_keys):
        return credit_list

    media_types = set(values(params, "media_type"))
    years = set(values(params, "year"))
    year_min = int_param(params, "year_min")
    year_max = int_param(params, "year_max")
    release_status = params.get("release_status")
    length = params.get("length")
    genres = set(values(params, "genre"))
    languages = set(values(params, "language"))
    excluded_genres = set(values(params, "exclude_genre"))
    excluded_languages = set(values(params, "exclude_language"))
    rating_min = decimal_param(params, "rating_min")
    rating_max = decimal_param(params, "rating_max")

    filtered = [
        credit
        for credit in credit_list
        if _person_credit_matches(
            credit,
            media_types,
            years,
            year_min,
            year_max,
            release_status,
            None,
            genres,
            languages,
            excluded_genres,
            excluded_languages,
        )
        and _credit_rating_matches(credit, rating_min, rating_max)
    ]
    if length in {"feature", "short"}:
        filtered = [
            credit
            for credit in _credits_with_runtime(filtered)
            if _person_credit_matches(
                credit,
                media_types,
                years,
                year_min,
                year_max,
                release_status,
                length,
                genres,
                languages,
                excluded_genres,
                excluded_languages,
            )
        ]

    return _sort_person_credits(
        filtered,
        params,
        rating_source=rating_source,
        default_sort=default_sort,
    )


def _sort_person_credits(credit_list, params, *, rating_source=None, default_sort=None):
    sort = params.get("sort") or default_sort or "average_rating"
    direction = params.get("direction")
    if direction not in {"asc", "desc"}:
        direction = "desc" if rating_source or sort in DESC_SORTS else "asc"
    descending = direction == "desc"

    if rating_source:
        def key(credit):
            rating = credit.get("_external_rating")
            value = rating.value if rating is not None and rating.value is not None else None
            return (
                value is None,
                -value if descending and value is not None else value,
                (credit.get("title") or "").casefold(),
                credit.get("_catalog_item_id") or 0,
            )
    elif sort == "popularity":
        return sorted(
            credit_list,
            key=lambda credit: _person_popularity_key(credit, descending),
        )
    elif sort == "release_date":
        def key(credit):
            release_date = _credit_release_date(credit)
            ordinal = release_date.toordinal() if release_date else 0
            return (release_date is None, -ordinal if descending else ordinal)
    elif sort in {"average_rating", "your_rating"}:
        def key(credit):
            rating = _credit_number(credit, "vote_average")
            value = float(rating or 0)
            return (rating is None, -value if descending else value)
    else:
        return sorted(
            credit_list,
            key=lambda credit: (credit.get("title") or "").lower(),
            reverse=descending,
        )
    return sorted(credit_list, key=key)


def _person_popularity_key(credit, descending):
    popularity = _credit_number(credit, "vote_count")
    rating = _credit_number(credit, "vote_average")
    release_date = _credit_release_date(credit)
    direction = -1 if descending else 1
    return (
        popularity is None,
        direction * popularity if popularity is not None else 0,
        rating is None,
        direction * rating if rating is not None else 0,
        {
            "MAIN": 0,
            "SUPPORTING": 1,
            "BACKGROUND": 2,
        }.get(str(credit.get("character_role") or "").upper(), 3),
        -(release_date.toordinal() if release_date else 0),
        (credit.get("title") or "").casefold(),
    )


def _person_credit_matches(  # noqa: C901, PLR0911
    credit,
    media_types,
    years,
    year_min,
    year_max,
    release_status=None,
    length=None,
    genres=None,
    languages=None,
    excluded_genres=None,
    excluded_languages=None,
):
    year = _credit_year(credit)
    if media_types and credit.get("media_type") not in media_types:
        return False
    if years and str(year) not in years:
        return False
    if year_min is not None and (year is None or year < year_min):
        return False
    if year_max is not None and (year is None or year > year_max):
        return False
    if genres and genres.isdisjoint(set(credit.get("genres") or [])):
        return False
    if languages and languages.isdisjoint(set(credit.get("languages") or [])):
        return False
    if excluded_genres and not excluded_genres.isdisjoint(set(credit.get("genres") or [])):
        return False
    if excluded_languages and not excluded_languages.isdisjoint(set(credit.get("languages") or [])):
        return False

    release_date = _credit_release_date(credit)
    today = timezone.localdate()
    if release_status == "released" and (release_date is None or release_date > today):
        return False
    if release_status == "unreleased" and (release_date is None or release_date <= today):
        return False

    runtime = _credit_runtime_minutes(credit)
    if length == "feature":
        return credit.get("media_type") == MediaTypes.MOVIE.value and runtime is not None and runtime >= SHORT_FILM_MINUTES
    if length == "short":
        return credit.get("media_type") == MediaTypes.MOVIE.value and runtime is not None and runtime < SHORT_FILM_MINUTES
    return True


def _credit_rating_matches(credit, rating_min, rating_max):
    rating = _credit_number(credit, "vote_average")
    if rating_min is not None and (rating is None or rating < rating_min):
        return False
    return rating_max is None or (rating is not None and rating <= rating_max)


def _replace_facets(item, facet_lists):
    """Replace each facet type that has new values; return whether any row changed.

    Values are trimmed, cut to the column length and deduplicated before comparing,
    so a long value matches the row it was stored as. A type with no new values keeps
    its stored rows.
    """
    wanted = {}
    for facet_type, facet_values in facet_lists.items():
        values_set = {
            value.strip()[:FACET_VALUE_MAX_LENGTH].strip()
            for value in facet_values
            if value and value.strip()
        }
        if values_set:
            wanted[facet_type] = values_set
    if not wanted:
        return False

    existing = defaultdict(set)
    for facet_type, value in ItemFilterFacet.objects.filter(
        item=item,
        facet_type__in=wanted,
    ).values_list("facet_type", "value"):
        existing[facet_type].add(value)

    changed = False
    for facet_type, values_set in wanted.items():
        if existing[facet_type] == values_set:
            continue
        ItemFilterFacet.objects.filter(item=item, facet_type=facet_type).delete()
        ItemFilterFacet.objects.bulk_create(
            [
                ItemFilterFacet(item=item, facet_type=facet_type, value=value)
                for value in sorted(values_set)
            ],
            ignore_conflicts=True,
        )
        changed = True
    return changed


def _metadata_facets(metadata):
    """Return the facet values metadata carries, by facet type."""
    original_language = metadata.get("original_language")
    return {
        ItemFilterFacet.FacetType.GENRE: _metadata_list(metadata, "genres"),
        ItemFilterFacet.FacetType.LANGUAGE: _metadata_list(metadata, "languages"),
        ItemFilterFacet.FacetType.THEME: _metadata_list(metadata, "themes"),
        ItemFilterFacet.FacetType.ORIGINAL_LANGUAGE: (
            [original_language] if isinstance(original_language, str) else []
        ),
    }


def _metadata_list(metadata, key):
    details = metadata.get("details") or {}
    values_list = metadata.get(key) or details.get(key) or []
    normalized = []
    for raw_value in values_list or []:
        value = raw_value
        if isinstance(raw_value, dict):
            value = raw_value.get("name") or raw_value.get("english_name") or raw_value.get("tag")
        if value:
            normalized.append(str(value))
    return normalized


def _release_date_values(metadata, media_type=None):
    """Return the date values metadata may carry, most trusted first."""
    details = metadata.get("details") or {}
    first = []
    if media_type == MediaTypes.BOOK.value:
        # A book's year is its first publication, not the edition's (Hardcover).
        first = [metadata.get("original_release_date"), details.get("original_release_date")]
    return [
        *first,
        metadata.get("release_date"),
        metadata.get("first_air_date"),
        metadata.get("start_date"),
        metadata.get("end_date"),
        metadata.get("first_release_date"),
        details.get("release_date"),
        details.get("first_release_date"),
        details.get("first_air_date"),
        details.get("start_date"),
        details.get("publish_date"),
        details.get("published_date"),
    ]


def _release_date(metadata, media_type=None):
    for value in _release_date_values(metadata, media_type):
        release_date = _date_value(value)
        if release_date is not None:
            return release_date
    return None


def _date_value(value):
    """Return a date from a date, a year, "YYYY-MM" or an ISO date; None otherwise.

    Other integers (IGDB's Unix timestamps) are not dates.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = value.strip() if isinstance(value, str) else ""
    year = None
    if isinstance(value, int) and not isinstance(value, bool):
        year = value
    elif len(text) == 4 and text.isdigit():
        year = int(text)
    if year is not None:
        return date(year, 1, 1) if MIN_YEAR <= year <= MAX_YEAR else None
    if len(text) == 7:
        text = f"{text}-01"
    try:
        return parse_date(text) if text else None
    except ValueError:
        return None


def _release_year(metadata, release_date, media_type=None):
    if release_date:
        return release_date.year
    details = metadata.get("details") or {}
    for value in (
        metadata.get("year"),
        details.get("year"),
        metadata.get("first_publish_year"),
        details.get("first_publish_year"),
    ):
        year = _year_value(value)
        if year is not None:
            return year
    # Last resort: a year inside a text date, such as "March 2004".
    for value in _release_date_values(metadata, media_type):
        if isinstance(value, str):
            match = TEXT_YEAR.search(value)
            if match and MIN_YEAR <= int(match.group(1)) <= MAX_YEAR:
                return int(match.group(1))
    return None


def _year_value(value):
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        year = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return year if MIN_YEAR <= year <= MAX_YEAR else None


def _runtime_minutes(metadata):
    details = metadata.get("details") or {}
    return _runtime_value_minutes(metadata.get("runtime") or details.get("runtime"))


def _runtime_value_minutes(value):
    """Return minutes from a number or text: "24 min", "1h 30m", "1.5 hours"."""
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return round(value) or None
    text = str(value).strip().lower()
    hours = RUNTIME_HOURS.search(text)
    minutes = RUNTIME_MINUTES.search(text)
    if hours or minutes:
        total = (float(hours.group(1)) * 60 if hours else 0) + (float(minutes.group(1)) if minutes else 0)
        return round(total) or None
    try:
        return round(float(text)) or None
    except (OverflowError, ValueError):
        return None


def _apply_facet_filter(queryset, params, item_path, facet_type):
    facet_values = values(params, facet_type)
    if not facet_values:
        return queryset
    path = f"{item_path}filter_facets__"
    return queryset.filter(
        **{
            f"{path}facet_type": facet_type,
            f"{path}value__in": facet_values,
        },
    ).distinct()


def _apply_facet_exclude(queryset, params, item_path, facet_type):
    facet_values = values(params, f"exclude_{facet_type}")
    if not facet_values:
        return queryset
    # One subquery, so the type and the value must match on the same facet row (a game's
    # "Horror" theme doesn't hide it from exclude_genre=Horror).
    matching = ItemFilterFacet.objects.filter(facet_type=facet_type, value__in=facet_values)
    return queryset.exclude(**{f"{item_path}id__in": matching.values("item_id")})


def _order_by_field(queryset, field, descending, item_path, tiebreak=()):
    order = (
        models.F(field).desc(nulls_last=True)
        if descending
        else models.F(field).asc(nulls_last=True)
    )
    # An empty tie value counts as the largest.
    ties = [
        models.F(name).desc(nulls_first=True) if descending else models.F(name).asc(nulls_last=True)
        for name in tiebreak
    ]
    return queryset.order_by(order, *ties, Lower(f"{item_path}title"))


def _facet_values(facets, facet_type):
    return [
        {"value": value, "label": value}
        for value in facets.filter(facet_type=facet_type)
        .order_by("value")
        .values_list("value", flat=True)
        .distinct()
    ]


def _credit_year(credit):
    year = credit.get("year") or (_credit_date_text(credit) or "")[:4]
    try:
        return int(year)
    except (TypeError, ValueError):
        return None


def _credit_release_date(credit):
    value = _credit_date_text(credit)
    if isinstance(value, date):
        return value
    if value:
        value = str(value)
        if len(value) == 4 and value.isdigit():
            return date(int(value), 1, 1)
        parsed = parse_date(value)
        if parsed:
            return parsed
    year = credit.get("year")
    if year:
        try:
            return date(int(year), 1, 1)
        except (TypeError, ValueError):
            return None
    return None


def _credit_date_text(credit):
    return credit.get("release_date") or credit.get("first_air_date") or credit.get("publish_date")


def _credits_with_runtime(credit_list):
    missing = [
        credit
        for credit in credit_list
        if credit.get("media_type") == MediaTypes.MOVIE.value and _credit_runtime_minutes(credit) is None
    ]
    if not missing:
        return credit_list

    ids = [str(credit.get("media_id") or "") for credit in missing]
    sources = {credit.get("source") for credit in missing if credit.get("source")}
    cached = {
        (item.source, item.media_id): item.runtime_minutes
        for item in Item.objects.filter(
            source__in=sources,
            media_type=MediaTypes.MOVIE.value,
            media_id__in=ids,
            runtime_minutes__isnull=False,
        )
    }
    enriched = [
        {**credit, "runtime_minutes": cached[(credit.get("source"), str(credit.get("media_id") or ""))]}
        if (credit.get("source"), str(credit.get("media_id") or "")) in cached
        else credit
        for credit in credit_list
    ]

    from app.providers import services as provider_services

    results = []
    for credit in enriched:
        if credit.get("media_type") != MediaTypes.MOVIE.value or _credit_runtime_minutes(credit) is not None:
            results.append(credit)
            continue
        try:
            metadata = provider_services.get_media_metadata(
                credit["media_type"],
                credit["media_id"],
                credit["source"],
            )
            runtime = _runtime_minutes(metadata)
        except (
            provider_services.ProviderAPIError,
            RequestException,
            NotImplementedError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            logger.debug("Skipping runtime lookup for person credit %s: %s", credit.get("media_id"), error)
            runtime = None
        results.append({**credit, "runtime_minutes": runtime} if runtime is not None else credit)
    return results


def _credit_runtime_minutes(credit):
    runtime = credit.get("runtime_minutes")
    if runtime is not None:
        try:
            return int(runtime)
        except (TypeError, ValueError):
            return None
    return _runtime_value_minutes(credit.get("runtime"))


def _credit_number(credit, field):
    try:
        return float(credit.get(field))
    except (TypeError, ValueError):
        return None
