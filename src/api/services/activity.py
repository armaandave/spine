"""Activity: logs and each day's progress, newest first (docs/activity-contract.md).

Activity is computed from the diary and from recorded progress changes, so it can't
drift from them. A page merges both, ordered by date: a log by its log date, a day's
progress by that day. Within a date, the most recently written comes first.
"""

import base64
import binascii
import json
from datetime import UTC, datetime, time
from functools import reduce
from operator import or_

from django.contrib.auth import get_user_model
from django.db.models import (
    Case,
    CharField,
    DateField,
    Exists,
    FloatField,
    Max,
    Min,
    OuterRef,
    Q,
    Sum,
    Value,
    When,
)
from django.db.models.fields.json import KT
from django.db.models.functions import Cast, Concat, TruncDate
from django.utils import timezone
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.utils.urls import replace_query_param

from api.permissions import can_view_user_profile
from api.serializers.common import media_summary_from_item, user_summary
from api.services import diary as diary_service
from app import single_weight
from app.curation import prime_curated_artwork
from app.models import DiaryEntry, Item, MediaTypes
from social.models import ContentLike, Follow, FollowStatus, ProgressChange, Visibility

PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

# A day's progress is told in the first of these units that moved forward, so a game
# played for an hour and 5% further reads as time played, and a start shows only on a
# day without progress.
UNITS = ("episode", "chapter", "issue", "page", "minute", "percent", ProgressChange.START)
TV_TYPES = (MediaTypes.TV.value, MediaTypes.SEASON.value, MediaTypes.EPISODE.value)

# Within one date and write time, logs sort before progress.
PROGRESS, LOG = 0, 1


def profile_sources(viewer, target_user):
    """Return the logs and progress a viewer may see on a profile, or None."""
    if not can_view_user_profile(viewer, target_user):
        return None
    logs = DiaryEntry.objects.filter(user=target_user)
    if viewer != target_user:
        follows = viewer.is_authenticated and Follow.objects.filter(
            from_user=viewer,
            to_user=target_user,
            status=FollowStatus.ACCEPTED,
        ).exists()
        logs = _visible_logs(logs, follows=follows)
    return logs, ProgressChange.objects.filter(actor=target_user)


def feed_sources(user):
    """Return the logs and progress of the people a user follows."""
    following = Follow.objects.filter(
        from_user=user,
        status=FollowStatus.ACCEPTED,
    ).values("to_user")
    logs = _visible_logs(DiaryEntry.objects.filter(user__in=following), follows=True)
    return logs, ProgressChange.objects.filter(actor__in=following)


def media_type_family(media_type):
    """Return a media type's item types: a show with its seasons and episodes."""
    return list(TV_TYPES) if media_type == MediaTypes.TV.value else [media_type]


def page_response(request, logs, progress):
    """Return one cursor page of activity in the shape installed apps read."""
    size = _page_size(request)
    after = _decode_cursor(request)
    logs = logs.select_related("user", "item").order_by("-consumed_at", "-created_at", "-id")
    days = _progress_days(progress).order_by("-day", "-last_at", "-progress_id")
    if after:
        logs = _logs_after(logs, after)
        days = _days_after(days, after)
    rows = [(_log_key(entry), entry) for entry in logs[: size + 1]]
    rows += [(_day_key(day), day) for day in days[: size + 1]]
    rows.sort(key=lambda row: row[0], reverse=True)
    page = rows[:size]

    entries = [row for key, row in page if key[2] == LOG]
    progress_days = [row for key, row in page if key[2] == PROGRESS]
    actors, titles = _day_people_and_titles(progress_days)
    prime_curated_artwork([entry.item for entry in entries] + list(titles.values()))
    diary_service.prime_diary_likes(entries, request.user)
    results = [
        _log_payload(row, request)
        if key[2] == LOG
        else _day_payload(row, actors, titles, request)
        for key, row in page
    ]
    next_cursor = None
    if len(rows) > size:
        next_cursor = replace_query_param(
            request.build_absolute_uri(),
            "cursor",
            _encode_cursor(page[-1][0]),
        )
    return Response(
        {"next_cursor": next_cursor, "previous_cursor": None, "results": results},
    )


def _visible_logs(queryset, *, follows):
    """Keep logs another user may see; half-star media logs are always public."""
    visibilities = [Visibility.PUBLIC]
    if follows:
        visibilities.append(Visibility.FOLLOWERS)
    return queryset.filter(
        Q(item__media_type__in=list(single_weight.HALF_STAR_MEDIA_TYPES))
        | Q(visibility__in=visibilities),
    )


def _progress_days(changes):
    """Group progress changes into one row per person, title, and day.

    A show's and its seasons' changes count toward the show. Each unit's net change is
    summed, and a day that didn't move forward is left out, as is a title the person
    logged that day.
    """
    show_types = [MediaTypes.TV.value, MediaTypes.SEASON.value]
    logs_that_day = (
        DiaryEntry.objects.filter(user=OuterRef("actor"))
        .annotate(log_day=_log_day())
        .filter(log_day=OuterRef("day"))
    )
    return (
        changes.annotate(
            day=TruncDate("created_at", tzinfo=timezone.get_current_timezone()),
            title_key=Case(
                When(
                    item__media_type__in=show_types,
                    then=Concat(Value("tv:"), "item__source", Value(":"), "item__media_id"),
                ),
                default=Concat(Value("item:"), Cast("item_id", CharField())),
                output_field=CharField(),
            ),
            delta=Cast(KT("current_progress__value"), FloatField())
            - Cast(KT("previous_progress__value"), FloatField()),
            logged=Exists(logs_that_day.filter(item_id=OuterRef("item_id"))),
            show_logged=Exists(
                logs_that_day.filter(
                    item__media_type__in=TV_TYPES,
                    item__source=OuterRef("item__source"),
                    item__media_id=OuterRef("item__media_id"),
                ),
            ),
        )
        .filter(logged=False)
        .exclude(item__media_type__in=show_types, show_logged=True)
        .values("actor_id", "title_key", "day")
        .annotate(
            progress_id=Min("id"),
            last_at=Max("created_at"),
            any_item_id=Min("item_id"),
            **{f"amount_{unit}": Sum("delta", filter=Q(current_progress__unit=unit)) for unit in UNITS},
        )
        .filter(reduce(or_, (Q(**{f"amount_{unit}__gt": 0}) for unit in UNITS)))
    )


def _log_day():
    """Return a log's calendar day, as the diary shows it."""
    return Case(
        When(
            item__media_type__in=list(single_weight.HALF_STAR_MEDIA_TYPES),
            then=TruncDate("consumed_at", tzinfo=UTC),
        ),
        default=TruncDate("consumed_at", tzinfo=timezone.get_current_timezone()),
        output_field=DateField(),
    )


def _log_key(entry):
    return (entry.consumed_at, entry.created_at, LOG, entry.id)


def _day_key(day):
    # A day sorts as that date's midnight UTC, where half-star logs of the date sit.
    return (datetime.combine(day["day"], time.min, tzinfo=UTC), day["last_at"], PROGRESS, day["progress_id"])


def _logs_after(logs, key):
    at, tie, kind, row_id = key
    after = Q(consumed_at__lt=at) | Q(consumed_at=at, created_at__lt=tie)
    if kind == LOG:
        after |= Q(consumed_at=at, created_at=tie, id__lt=row_id)
    return logs.filter(after)


def _days_after(days, key):
    at, tie, kind, row_id = key
    at = at.astimezone(UTC)
    if at.time() != time.min:
        # Days sort at midnight, so none tie with a key later in the day.
        return days.filter(day__lte=at.date())
    after = Q(day__lt=at.date()) | Q(day=at.date(), last_at__lt=tie)
    if kind == LOG:
        after |= Q(day=at.date(), last_at=tie)
    else:
        after |= Q(day=at.date(), last_at=tie, progress_id__lt=row_id)
    return days.filter(after)


def _day_people_and_titles(days):
    """Load the people and titles a page of progress days names."""
    actors = get_user_model().objects.in_bulk({day["actor_id"] for day in days})
    items = Item.objects.in_bulk(
        {int(day["title_key"].removeprefix("item:")) for day in days if day["title_key"].startswith("item:")}
        | {day["any_item_id"] for day in days},
    )
    shows = {}
    show_keys = [day["title_key"].split(":", 2)[1:] for day in days if day["title_key"].startswith("tv:")]
    if show_keys:
        shows = {
            (show.source, show.media_id): show
            for show in Item.objects.filter(
                reduce(or_, (Q(source=source, media_id=media_id) for source, media_id in show_keys)),
                media_type=MediaTypes.TV.value,
            )
        }
    titles = {}
    for day in days:
        key = day["title_key"]
        if key.startswith("tv:"):
            title = shows.get(tuple(key.split(":", 2)[1:]))
        else:
            title = items.get(int(key.removeprefix("item:")))
        titles[(day["actor_id"], key, day["day"])] = title or items[day["any_item_id"]]
    return actors, titles


def _log_payload(entry, request):
    return {
        "id": entry.id,
        "type": "diary_created",
        "created_at": entry.created_at,
        "consumed_at": diary_service.consumed_at_payload(entry),
        "actor": user_summary(entry.user, request=request),
        "media": media_summary_from_item(entry.item, request=request),
        "person": None,
        "object": {
            "type": ContentLike.DIARY_ENTRY,
            "id": entry.id,
            "rating": diary_service.rating_payload(entry),
            "liked": entry.liked,
        },
        "viewer": {"can_view": True, "has_liked": entry.viewer_has_liked},
    }


def _day_payload(day, actors, titles, request):
    title = titles[(day["actor_id"], day["title_key"], day["day"])]
    summary, label = progress_words(day, title.media_type)
    return {
        # Negative, so ids stay unique across logs and progress days.
        "id": -day["progress_id"],
        "type": "progress_updated",
        "created_at": day["last_at"],
        "consumed_at": day["day"].isoformat(),
        "actor": user_summary(actors[day["actor_id"]], request=request),
        "media": media_summary_from_item(title, request=request),
        "person": None,
        "object": {
            "type": "progress_day",
            "id": day["progress_id"],
            "summary": summary,
            "label": label,
        },
        "viewer": {"can_view": True, "has_liked": False},
    }


def progress_words(day, media_type):
    """Say what a day's progress was, whole and short: "read 40 pages", "40 pages"."""
    unit, amount = next(
        (unit, day[f"amount_{unit}"])
        for unit in UNITS
        if day[f"amount_{unit}"] and day[f"amount_{unit}"] > 0
    )
    if unit == ProgressChange.START:
        verb = {
            MediaTypes.TV.value: "watching",
            MediaTypes.SEASON.value: "watching",
            MediaTypes.ANIME.value: "watching",
            MediaTypes.GAME.value: "playing",
        }.get(media_type, "reading")
        return f"started {verb}", "Started"
    if unit == "minute":
        hours, minutes = divmod(round(amount), 60)
        label = " ".join(part for part in (f"{hours}h" if hours else "", f"{minutes}m" if minutes else "") if part)
        return f"played {label}", label
    if unit == "percent":
        label = f"{_number(amount)}%"
        return (f"progressed {label}" if media_type == MediaTypes.GAME.value else f"read {label}"), label
    label = f"{_number(amount)} {unit if amount == 1 else unit + 's'}"
    return f"{'watched' if unit == 'episode' else 'read'} {label}", label


def _number(amount):
    rounded = round(amount, 1)
    return str(int(rounded)) if rounded == int(rounded) else str(rounded)


def _page_size(request):
    try:
        size = int(request.query_params.get("page_size", PAGE_SIZE))
    except ValueError:
        return PAGE_SIZE
    return min(size, MAX_PAGE_SIZE) if size > 0 else PAGE_SIZE


def _encode_cursor(key):
    at, tie, kind, row_id = key
    data = json.dumps({"at": at.isoformat(), "tie": tie.isoformat(), "kind": kind, "id": row_id})
    return base64.urlsafe_b64encode(data.encode()).decode()


def _decode_cursor(request):
    raw = request.query_params.get("cursor")
    if not raw:
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(raw.encode()))
        return (
            datetime.fromisoformat(data["at"]),
            datetime.fromisoformat(data["tie"]),
            int(data["kind"]),
            int(data["id"]),
        )
    except (binascii.Error, ValueError, KeyError, TypeError) as error:
        raise NotFound("Invalid cursor.") from error
