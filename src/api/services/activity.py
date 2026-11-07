"""Activity: logs and each day's progress, newest first (docs/activity-contract.md).

Activity is the Activity calculation's rows (api.services.activity_rows), so it can't
drift from Stats or from the Activity list. A page merges logs and days' progress,
ordered by the person's calendar day: a log by its log date, a day's progress by the day
it was saved on. Within a day, the most recently written comes first.
"""

import base64
import binascii
import json
from datetime import UTC, date, datetime

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.models import (
    Case,
    Count,
    DateField,
    Q,
    When,
)
from django.db.models.functions import TruncDate, TruncDay
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.utils.urls import replace_query_param

from api.permissions import can_view_user_profile
from api.serializers.common import (
    media_summary_from_item,
    prime_collection_items,
    progress_for_media,
    tv_season_started,
    user_summary,
)
from api.services import activity_list, activity_rows
from api.services import diary as diary_service
from app import exposure, single_weight, tv_tracking, user_calendar
from app.curation import prime_curated_artwork
from app.models import (
    TV,
    BasicMedia,
    DiaryEntry,
    Episode,
    Item,
    MediaLike,
    MediaTypes,
    Season,
    Status,
)
from social.models import (
    Comment,
    ContentLike,
    Follow,
    FollowStatus,
    ProgressChange,
    ProgressPost,
    Visibility,
)

PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

_number = activity_list.number
_minutes_label = activity_list.minutes_label

# A day's progress is told in the first of these units that moved forward, so a game
# played for an hour and 5% further reads as time played, and a start shows only on a
# day without progress.
UNITS = ("episode", "chapter", "issue", "page", "minute", "percent", ProgressChange.START)
TV_TYPES = (MediaTypes.TV.value, MediaTypes.SEASON.value, MediaTypes.EPISODE.value)
CALENDAR_LOG_TYPES = (*TV_TYPES, MediaTypes.ANIME.value, MediaTypes.MANGA.value, MediaTypes.COMIC.value)
SHOW_TYPES = (MediaTypes.TV.value, MediaTypes.SEASON.value)
# The statuses with progress worth telling: finishing a title says it all.
UNFINISHED = (Status.IN_PROGRESS.value, Status.PAUSED.value, Status.DROPPED.value)


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


def media_friends(viewer, item, request=None):
    """Return the people a user follows who've done something with a title (AC-6).

    Newest first. Each shows their newest log, or their newest with a review when one
    has one. Someone without a log shows where they stand instead.
    """
    following = list(
        Follow.objects.filter(
            from_user=viewer,
            status=FollowStatus.ACCEPTED,
        ).values_list("to_user", flat=True),
    )
    logs, _ = feed_sources(viewer)
    visible_logs = logs.filter(item=item)
    logged = {}
    newest_log = {}
    for entry in visible_logs.annotate(log_day=_log_day()).order_by("-log_day", "-created_at", "-id"):
        newest_log.setdefault(entry.user_id, (entry.log_day, entry.created_at))
        shown = logged.get(entry.user_id)
        if shown is None or (entry.review and not shown.review):
            logged[entry.user_id] = entry
    # A private log can set the title's rating and heart, so someone with one shows
    # only what their visible logs say.
    private_loggers = set(
        DiaryEntry.objects.filter(user__in=following, item=item)
        .exclude(pk__in=visible_logs.values("pk"))
        .values_list("user_id", flat=True),
    )
    rows = _tracking_rows(following, item)
    without_log = [row for user_id, row in rows.items() if user_id not in logged]
    if item.media_type in (MediaTypes.TV.value, MediaTypes.SEASON.value):
        tv_tracking.prime_progress(without_log)
    elif item.media_type != MediaTypes.EPISODE.value:
        BasicMedia.objects.annotate_max_progress(without_log, item.media_type)
    liked = dict(
        MediaLike.objects.filter(user__in=following, item=item).values_list("user_id", "created_at"),
    )
    people = get_user_model().objects.in_bulk({*logged, *rows, *liked})
    friends = []
    for user_id, person in people.items():
        entry = logged.get(user_id)
        row = rows.get(user_id)
        standing = _standing(row)
        shows_title_taste = user_id not in private_loggers
        stored_rating = entry.rating if entry else None
        if stored_rating is None and shows_title_taste:
            stored_rating = getattr(row, "score", None)
        rating = single_weight.rating_to_api(stored_rating, item)
        is_liked = bool(entry and entry.liked) or (shows_title_taste and user_id in liked)
        if not (entry or standing or rating or is_liked):
            continue
        payload = {
            "user": user_summary(person, request=request),
            "diary_entry_id": entry.id if entry else None,
            "consumed_at": entry.log_day.isoformat() if entry else None,
            "rating": rating,
            "liked": is_liked,
            "is_rewatch": bool(entry and entry.is_rewatch),
            "review_title": entry.review_title if entry else "",
            "review": entry.review if entry else "",
            "contains_spoilers": bool(entry and entry.contains_spoilers),
            "status": standing,
            "progress_label": _progress_label(row) if not entry and standing in UNFINISHED else None,
        }
        friends.append((_friend_sort_key(newest_log.get(user_id), row, liked.get(user_id)), user_id, payload))
    friends.sort(key=lambda friend: friend[:2], reverse=True)
    return [payload for _, _, payload in friends]


def _tracking_rows(user_ids, item):
    """Return each person's tracking row for a title, by user id."""
    if item.media_type == MediaTypes.EPISODE.value:
        viewings = (
            Episode.objects.filter(related_season__user__in=user_ids, item=item)
            .select_related("related_season")
            .order_by("created_at")
        )
        # The newest viewing wins.
        return {viewing.related_season.user_id: viewing for viewing in viewings}
    rows = apps.get_model("app", item.media_type).objects.filter(user__in=user_ids, item=item).select_related("item")
    if item.media_type in (MediaTypes.BOOK.value, MediaTypes.GAME.value):
        rows = rows.select_related("current_session")
    return {row.user_id: row for row in rows}


def _standing(row):
    """Return the status someone's tracking row gives them on a title, if any."""
    if row is None:
        return None
    if isinstance(row, Episode):
        return Status.COMPLETED.value
    if isinstance(row, Season) and not tv_season_started(row):
        return None
    return None if row.status == Status.PLANNING.value else row.status


def _progress_label(row):
    """Say how far someone got: "212 of 476 pages", "45%", "2h 30m", or None."""
    progress = progress_for_media(row)
    value, maximum = progress["value"], progress["max"]
    if not value or progress["kind"] == "binary":
        return None
    if progress["kind"] == "percentage":
        return f"{_number(value)}%"
    if progress["kind"] == "minutes":
        return _minutes_label(value)
    if maximum:
        return f"{_number(value)} of {_number(maximum)} {progress['unit']}{'' if maximum == 1 else 's'}"
    return f"{_number(value)} {progress['unit']}{'' if value == 1 else 's'}"


def _friend_sort_key(newest_log, row, liked_at):
    """Sort by calendar day, then by when it was written, as activity does (AC-3)."""
    if newest_log:
        return newest_log
    moment = max(filter(None, (liked_at, _row_moved_at(row))))
    return (timezone.localtime(moment).date(), moment)


def _row_moved_at(row):
    """Return when a tracking row last moved, without loading a show's episodes."""
    if row is None:
        return None
    if isinstance(row, Episode):
        return row.end_date or row.created_at
    if isinstance(row, (TV, Season)):
        return getattr(row, "last_activity_at", None) or row.created_at
    return row.progressed_at or row.created_at


def followed_users(user):
    """Return the people a user follows."""
    return list(
        get_user_model().objects.filter(
            follower_edges__from_user=user,
            follower_edges__status=FollowStatus.ACCEPTED,
        ),
    )


def feed_response(request):
    """Return the activity of the people the current user follows, one cursor page."""
    media_type = request.query_params.get("media_type")
    filters = activity_list.Filters()
    if media_type:
        exposure.require_media_type(media_type)
        filters.media_type = MediaTypes.TV.value if media_type in TV_TYPES else media_type
    people = followed_users(request.user)
    calculation = activity_rows.rows_for(
        people,
        request.user,
        types=[filters.media_type] if filters.media_type else None,
        piles=False,
    )
    rows = activity_list.select(_exposed(calculation), filters)
    return page_response(request, rows)


def profile_response(request, user):
    """Return a profile's activity, one cursor page.

    A list opened from a Stats number has a type, a range of days or a count (5.6). It
    then also says, to signed-in viewers, the number it adds up to, the type's total and
    what that number includes with no date.
    """
    if not can_view_user_profile(request.user, user):
        return Response({"next_cursor": None, "previous_cursor": None, "results": []})
    filters = activity_list.parse_filters(request.query_params)
    calculation = activity_rows.rows_for(
        [user],
        request.user,
        types=[filters.media_type] if filters.media_type else None,
        piles=activity_list.needs_piles(filters),
    )
    calculation = _exposed(calculation)
    rows = activity_list.select(calculation, filters)
    # A list opened from a number, or asked to be a list, reads as the Diary does.
    list_view = filters.asked or request.query_params.get("view") == "list"
    response = page_response(request, rows, list_view=list_view)
    if filters.asked and request.user.is_authenticated:
        response.data.update(activity_list.extras(calculation, filters, rows, user))
        if _decode_cursor(request):
            response.data["undated"] = None
    return response


def activity_count(user, viewer):
    """Return how many rows the viewer sees on this profile's Activity list."""
    if not can_view_user_profile(viewer, user):
        return 0
    calculation = _exposed(activity_rows.rows_for([user], viewer, piles=False))
    return len(activity_list.select(calculation, activity_list.Filters()))


def _exposed(calculation):
    """Keep the rows and piles of the types the server exposes."""
    allowed = set(exposure.primary_media_types())
    calculation.rows = [row for row in calculation.rows if row.bucket in allowed]
    calculation.piles = [pile for pile in calculation.piles if pile.bucket in allowed]
    return calculation


def page_response(request, rows, *, list_view=False):
    """Return one cursor page of these rows, newest first, in the shape apps read."""
    size = _page_size(request)
    after = _decode_cursor(request)
    if after:
        rows = [row for row in rows if activity_list.row_order(row) < after]
    page = rows[:size]
    results = _page_items(page, request, list_view=list_view)
    next_cursor = None
    if len(rows) > size:
        next_cursor = replace_query_param(
            request.build_absolute_uri(),
            "cursor",
            _encode_cursor(activity_list.row_order(page[-1])),
        )
    return Response(
        {"next_cursor": next_cursor, "previous_cursor": None, "results": results},
    )


def _page_items(page, request, *, list_view):
    """Turn a page of rows into the items a client reads."""
    entries = {
        entry.pk: entry
        for entry in DiaryEntry.objects.filter(pk__in=[row.log_id for row in page if row.log_id]).select_related(
            "user",
            "item",
        )
    }
    for row in page:
        if row.log_id in entries:
            entries[row.log_id].log_day = row.day
    progress = [row for row in page if not row.log_id]
    actors = get_user_model().objects.in_bulk({row.actor_id for row in page})
    titles = Item.objects.in_bulk({row.title_id for row in progress})
    items = [*(entry.item for entry in entries.values()), *titles.values()]
    if list_view:
        prime_collection_items(items, request.user)
        diary_service.prime_show_titles(entries.values())
    else:
        prime_curated_artwork(items)
    diary_service.prime_diary_likes(entries.values(), request.user)
    engagement = _day_engagement([_post_key(row) for row in progress], request.user)
    results = []
    for row in page:
        if row.log_id:
            entry = entries.get(row.log_id)
            if entry is not None:
                results.append(_log_payload(entry, request, row, list_view=list_view))
        elif row.title_id in titles:
            results.append(
                _row_payload(row, actors[row.actor_id], titles[row.title_id], request, engagement, list_view=list_view),
            )
    return results


def _post_key(row):
    """Return what a day's likes and comments attach to: the person, title and day."""
    return {"actor_id": row.actor_id, "title_key": row.title_key, "day": row.day}


def _visible_logs(queryset, *, follows):
    """Keep logs another user may see; half-star media logs are always public."""
    visibilities = [Visibility.PUBLIC]
    if follows:
        visibilities.append(Visibility.FOLLOWERS)
    return queryset.filter(
        Q(item__media_type__in=list(single_weight.HALF_STAR_MEDIA_TYPES))
        | Q(visibility__in=visibilities),
    )


def _log_day():
    """Return a log's calendar day, as the diary shows it.

    Half-star media logs, and TV, anime, manga and comic logs saved as calendar dates
    (TV-407, AN-400, MC-000), store their day as UTC midnight; any other log is read on
    the server's local day.
    """
    return Case(
        When(
            Q(item__media_type__in=list(single_weight.HALF_STAR_MEDIA_TYPES))
            | Q(item__media_type__in=CALENDAR_LOG_TYPES, consumed_at=TruncDay("consumed_at", tzinfo=UTC)),
            then=TruncDate("consumed_at", tzinfo=UTC),
        ),
        default=TruncDate("consumed_at", tzinfo=timezone.get_current_timezone()),
        output_field=DateField(),
    )


def progress_post(viewer, progress_id):
    """Return the saved post of a day's progress the viewer may see, creating it if new.

    Any address the day has finds it, as ``object.id`` on the feed does.
    """
    day = progress_day(viewer, progress_id)
    post, _ = ProgressPost.objects.get_or_create(
        actor_id=day["actor_id"],
        title_key=day["title_key"],
        day=day["day"],
    )
    return post


# A row's address (plan 5.8). A book, game, manga or comic day with a change row keeps
# the lowest change id, so every id already posted or linked still resolves. Any other
# row is addressed by what names it: the person, the title and the day. The address sits
# at or above 2**44, where the change ids never reach, and fits a signed 64-bit id.
ADDRESS_BASE = 1 << 44
ITEM_BITS, DAY_BITS = 24, 20
# What an address can name: an Item below one and a person below the other.
ITEM_LIMIT = (1 << ITEM_BITS) - 1
USER_LIMIT = (1 << (63 - ITEM_BITS - DAY_BITS)) - 1
CHANGE_ADDRESSED = (
    MediaTypes.BOOK.value,
    MediaTypes.GAME.value,
    MediaTypes.MANGA.value,
    MediaTypes.COMIC.value,
)


def row_address(row):
    """Return the id that opens a day's progress: ``object.id`` on the feed."""
    if row.progress_id and row.bucket in CHANGE_ADDRESSED:
        return row.progress_id
    if row.title_id > ITEM_LIMIT or row.actor_id > USER_LIMIT:
        # Packed, it would name another title; SHIP.md stops before the ids get here.
        if row.progress_id:
            return row.progress_id
        msg = f"Row address overflow: person {row.actor_id}, Item {row.title_id}"
        raise OverflowError(msg)
    return (row.actor_id << (ITEM_BITS + DAY_BITS)) | (row.title_id << DAY_BITS) | row.day.toordinal()


def _from_address(progress_id):
    """Return (person id, Item id, day) a hybrid address names, or 404."""
    try:
        day = date.fromordinal(progress_id & ((1 << DAY_BITS) - 1))
    except ValueError as error:
        raise Http404 from error
    return progress_id >> (ITEM_BITS + DAY_BITS), (progress_id >> DAY_BITS) & ((1 << ITEM_BITS) - 1), day


def _from_change(progress_id):
    """Return (the person, the title's Item, the day) a saved change names, or 404."""
    change = get_object_or_404(ProgressChange.objects.select_related("actor", "item"), id=progress_id)
    item = change.item
    if item.media_type in SHOW_TYPES:
        # A show's and its seasons' changes count toward the show.
        item = get_object_or_404(
            Item,
            media_type=MediaTypes.TV.value,
            source=item.source,
            media_id=item.media_id,
            season_number=None,
            episode_number=None,
        )
    return change.actor, item, activity_rows.saved_day(change, user_calendar.calendar_tz(change.actor))


def progress_day(viewer, progress_id):
    """Return the day's progress an address belongs to, or 404.

    The day is read from the calculation as this viewer sees it: when a later fix leaves
    the day with nothing, or a log moves it, the page, its like and its comments are 404
    until it has a row again. Their rows stay stored (AC-7).
    """
    if progress_id >= ADDRESS_BASE:
        actor_id, item_id, day = _from_address(progress_id)
        owner = get_object_or_404(get_user_model(), pk=actor_id)
        item = get_object_or_404(Item, pk=item_id)
    else:
        owner, item, day = _from_change(progress_id)
    if not can_view_user_profile(viewer, owner):
        raise Http404
    exposure.require_media_type(item.media_type)
    bucket = MediaTypes.TV.value if item.media_type in SHOW_TYPES else item.media_type
    if bucket not in activity_rows.ADAPTERS:
        raise Http404
    rows = activity_rows.rows_for([owner], viewer, titles=[item.pk], types=[bucket], piles=False).rows
    row = next((row for row in rows if not row.log_id and row.title_id == item.pk and row.day == day), None)
    if row is None:
        raise Http404
    return {"actor_id": owner.pk, "title_key": row.title_key, "day": day, "row": row}


def progress_post_response(request, progress_id):
    """Return a day's progress in the shape the feed gives it."""
    day = progress_day(request.user, progress_id)
    row = day["row"]
    actor = get_user_model().objects.get(pk=row.actor_id)
    title = Item.objects.get(pk=row.title_id)
    prime_curated_artwork([title])
    return Response(_row_payload(row, actor, title, request, _day_engagement([_post_key(row)], request.user)))


def _title_key(item):
    """Return the key that groups a title's changes, as ``_progress_days`` builds it."""
    if item.media_type in SHOW_TYPES:
        return f"tv:{item.source}:{item.media_id}"
    return f"item:{item.id}"


def _day_engagement(days, viewer):
    """Return likes and comments of progress days, by person, title, and day."""
    if not days:
        return {}
    posts = ProgressPost.objects.filter(
        actor_id__in={day["actor_id"] for day in days},
        title_key__in={day["title_key"] for day in days},
        day__in={day["day"] for day in days},
    )
    keys = {post.id: (post.actor_id, post.title_key, post.day) for post in posts}
    likes = ContentLike.objects.filter(target_type=ContentLike.PROGRESS_POST, target_id__in=keys)
    like_counts = dict(likes.values("target_id").annotate(count=Count("id")).values_list("target_id", "count"))
    liked = (
        set(likes.filter(user=viewer).values_list("target_id", flat=True)) if viewer.is_authenticated else set()
    )
    comment_counts = dict(
        Comment.objects.visible_to(viewer)
        .counted()
        .filter(progress_post_id__in=keys)
        .order_by()
        .values("progress_post_id")
        .annotate(count=Count("id"))
        .values_list("progress_post_id", "count"),
    )
    return {
        key: {
            "like_count": like_counts.get(post_id, 0),
            "comment_count": comment_counts.get(post_id, 0),
            "has_liked": post_id in liked,
        }
        for post_id, key in keys.items()
    }


def _log_payload(entry, request, row=None, *, list_view=False):
    fields = activity_list.log_fields(row) if row is not None else {}
    return {
        "id": entry.id,
        "type": "diary_created",
        "created_at": entry.created_at,
        # The person's calendar day, so an evening log isn't shown as the next UTC day.
        "consumed_at": entry.log_day.isoformat(),
        "actor": user_summary(entry.user, request=request),
        "media": _media(entry.item, request, list_view=list_view, entry=entry),
        "person": None,
        "object": {
            "type": ContentLike.DIARY_ENTRY,
            "id": entry.id,
            "rating": diary_service.rating_payload(entry),
            "liked": entry.liked,
            "is_rewatch": entry.is_rewatch,
            **fields,
        },
        "viewer": {"can_view": True, "has_liked": entry.viewer_has_liked},
        "like_count": entry.viewer_like_count,
        "comment_count": entry.viewer_comment_count,
        # A list row shows no review text (decision 24): a spoiler would leak into a
        # list that has no cover for it.
        "review_title": "" if list_view else entry.review_title,
        "review": "" if list_view else entry.review,
        "contains_spoilers": entry.contains_spoilers,
    }


def _media(item, request, *, list_view, entry=None):
    """Return a title's summary; a list's reads as the Diary does."""
    if not list_view:
        return media_summary_from_item(item, request=request)
    media = media_summary_from_item(item, request=request, user=request.user, include_user_state=False)
    if entry is not None:
        media["show_title"] = entry.tv_show_title
        media["episode_title"] = entry.tv_episode_title
    return media


def _row_payload(row, actor, title, request, engagement, *, list_view=False):
    counts = engagement.get((row.actor_id, row.title_key, row.day), {})
    summary, label = activity_list.progress_words(row)
    address = row_address(row)
    return {
        # Negative, so ids stay unique across logs and progress days.
        "id": -address,
        "type": "progress_updated",
        "created_at": row.last_at,
        "consumed_at": row.day.isoformat(),
        "actor": user_summary(actor, request=request),
        "media": _media(title, request, list_view=list_view),
        "person": None,
        "object": {
            "type": "progress_day",
            "id": address,
            "summary": summary,
            "label": label,
            **activity_list.progress_fields(row),
        },
        "viewer": {"can_view": True, "has_liked": counts.get("has_liked", False)},
        "like_count": counts.get("like_count", 0),
        "comment_count": counts.get("comment_count", 0),
    }


def _page_size(request):
    try:
        size = int(request.query_params.get("page_size", PAGE_SIZE))
    except ValueError:
        return PAGE_SIZE
    return min(size, MAX_PAGE_SIZE) if size > 0 else PAGE_SIZE


def _encode_cursor(key):
    day, tie, kind, row_id, title_id, actor_id = key
    data = json.dumps(
        {
            "day": day.isoformat(),
            "tie": tie.isoformat(),
            "kind": kind,
            "id": row_id,
            "title": title_id,
            "actor": actor_id,
        },
    )
    return base64.urlsafe_b64encode(data.encode()).decode()


def _decode_cursor(request):
    raw = request.query_params.get("cursor")
    if not raw:
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(raw.encode()))
        return (
            date.fromisoformat(data["day"]),
            datetime.fromisoformat(data["tie"]),
            int(data["kind"]),
            int(data["id"]),
            int(data.get("title", 0)),
            int(data.get("actor", 0)),
        )
    except (binascii.Error, ValueError, KeyError, TypeError) as error:
        raise NotFound("Invalid cursor.") from error
