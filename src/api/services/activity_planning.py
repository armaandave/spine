"""The Planning row: a person's day of adds (docs/activity-contract.md, AC-8).

A Planning add is a `social.ProgressChange` of kind `planning`, written by the request
that adds the title (app.planning_adds). The calculation (api.services.activity_rows)
never reads one: its rows feed Stats and active days, and a Planning row adds nothing
to any number. So this module builds the rows apart, and the feed and a profile's list
merge them in at the last step, only for a request that asks with ``include=planning``.
Installed builds would print an unknown row as "shared activity", so a request without
it returns exactly what it did before.

One row per person per day: "added 3 shows to Planning". Its poster and name are the
day's earliest add, so the row doesn't change its face as more are added. It has no
likes and no comments.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

from django.apps import apps
from django.contrib.auth import get_user_model
from django.http import Http404
from django.shortcuts import get_object_or_404
from rest_framework.response import Response

from api.permissions import can_view_user_profile
from api.serializers.common import (
    artwork_from_item,
    media_summary_from_item,
    prime_collection_items,
    user_summary,
)
from api.services import activity_list
from app import exposure
from app.curation import prime_curated_artwork
from social.models import ProgressChange

INCLUDE = "planning"
# A Planning row's address is a hybrid address with no Item (activity.row_address): no
# Item has id 0, so it can't be a progress day's, and it stays the same as adds pile up.
DAY_BITS = 20
ADDRESS_SHIFT = 44


def asked(request):
    """Whether the request opted in to Planning rows: only builds that ask get them."""
    values = request.query_params.getlist("include")
    return INCLUDE in {part.strip() for value in values for part in value.split(",")}


def address(actor_id, day):
    """Return the id that opens a person's day of Planning adds."""
    return (actor_id << ADDRESS_SHIFT) | day.toordinal()


def decode(planning_id):
    """Return (person id, day) a Planning address names, or raise a 404."""
    mask = (1 << DAY_BITS) - 1
    if planning_id >> DAY_BITS & ((1 << (ADDRESS_SHIFT - DAY_BITS)) - 1):
        raise Http404
    try:
        day = date.fromordinal(planning_id & mask)
    except ValueError as error:
        raise Http404 from error
    return planning_id >> ADDRESS_SHIFT, day


# How many names a row carries: more than any row can show on one line.
TITLE_NAMES = 20
# How many posters a row fans out, as a series card does.
FAN_POSTERS = 3


@dataclass
class PlanningRow:
    """A person's day of Planning adds, shaped for the list's sort and cursor."""

    actor_id: int
    day: date
    last_at: datetime
    titles: list = field(default_factory=list)
    media_type: str | None = None

    # What a progress row has, so the shared sort reads it: never a log, no one title.
    log_id = None
    title_id = 0
    bucket = None

    @property
    def count(self):
        return len(self.titles)

    @property
    def address(self):
        return address(self.actor_id, self.day)

    @property
    def sort_key(self):
        return (self.day, self.last_at, 0, self.address)

    @property
    def face(self):
        """The day's earliest add: the Item the row shows."""
        return self.titles[0]


def rows_for(people, *, media_type=None):
    """Return these people's days of Planning adds, newest first.

    Only titles the server exposes and the person still tracks count, and a title added
    twice in a day counts once. With a `media_type`, only that type's titles count.
    """
    people = list(people)
    if not people:
        return []
    allowed = set(exposure.primary_media_types())
    if media_type:
        allowed &= {media_type}
    changes = list(
        ProgressChange.objects.filter(
            actor__in=people,
            current_progress__kind=ProgressChange.PLANNING,
            saved_on__isnull=False,
            item__media_type__in=allowed,
        )
        .select_related("item")
        .order_by("created_at", "id"),
    )
    tracked = _tracked({(change.actor_id, change.item_id) for change in changes}, changes)
    days = defaultdict(dict)
    stamps = {}
    for change in changes:
        if (change.actor_id, change.item_id) not in tracked:
            continue
        key = (change.actor_id, change.saved_on)
        days[key].setdefault(change.item_id, change.item)
        stamps[key] = max(stamps.get(key, change.created_at), change.created_at)
    rows = []
    for (actor_id, day), items in days.items():
        types = {item.media_type for item in items.values()}
        rows.append(
            PlanningRow(
                actor_id,
                day,
                stamps[actor_id, day],
                list(items.values()),
                next(iter(types)) if len(types) == 1 else None,
            ),
        )
    return sorted(rows, key=activity_list.row_order, reverse=True)


def _tracked(pairs, changes):
    """Return which (person, Item id) pairs the person still tracks.

    A removed title's Planning rows are deleted with it (app.progress_posts); this is
    the backstop, one query per type.
    """
    items = {change.item_id: change.item for change in changes}
    by_type = defaultdict(set)
    for _actor_id, item_id in pairs:
        by_type[items[item_id].media_type].add(item_id)
    held = set()
    for media_type, item_ids in by_type.items():
        model = apps.get_model("app", media_type)
        held |= set(model.objects.filter(item_id__in=item_ids).values_list("user_id", "item_id"))
    return pairs & held


def words(row):
    """Say a day's adds: "added 3 shows to Planning", "added 6 titles to Planning"."""
    if row.media_type:
        singular, plural = activity_list.NOUNS.get(row.media_type, ("title", "titles"))
    else:
        singular, plural = "title", "titles"
    return f"added {activity_list.counted(row.count, singular, plural)} to Planning"


def payload(row, actor, request):
    """Return a Planning row in the shape of the feed's items."""
    return {
        "id": -row.address,
        "type": "planning_added",
        "created_at": row.last_at,
        "consumed_at": row.day.isoformat(),
        "actor": user_summary(actor, request=request),
        "media": media_summary_from_item(row.face, request=request),
        "person": None,
        "object": {
            "type": "planning",
            "id": row.address,
            "summary": words(row),
            "label": None,
            "count": row.count,
            "media_type": row.media_type,
            # Names under the count, oldest add first; the app shows what fits.
            "titles": [item.title for item in row.titles[:TITLE_NAMES]],
            "posters": [
                url
                for item in row.titles[:FAN_POSTERS]
                if (url := artwork_from_item(item, request=request)["poster_url"])
            ],
        },
        "viewer": {"can_view": True, "has_liked": False},
        "like_count": 0,
        "comment_count": 0,
    }


def payloads(rows, request):
    """Return the items of these Planning rows, with their people loaded once."""
    actors = get_user_model().objects.in_bulk({row.actor_id for row in rows})
    prime_curated_artwork([item for row in rows for item in row.titles[:FAN_POSTERS]])
    return [payload(row, actors[row.actor_id], request) for row in rows]


def day_response(request, planning_id):
    """Return the titles a person added to Planning on a day, for the row's own screen.

    The viewer needs the same access as to the person's progress: anyone who can see
    the profile. `media_type` narrows it to the type the row counted.
    """
    actor_id, day = decode(planning_id)
    owner = get_object_or_404(get_user_model(), pk=actor_id)
    if not can_view_user_profile(request.user, owner):
        raise Http404
    media_type = request.query_params.get("media_type") or None
    if media_type:
        exposure.require_media_type(media_type)
    shown = [row for row in rows_for([owner], media_type=media_type) if row.day == day]
    if not shown:
        raise Http404
    row = shown[0]
    items = row.titles
    prime_collection_items(items, request.user)
    return Response(
        {
            "actor": user_summary(owner, request=request),
            "consumed_at": day.isoformat(),
            "summary": words(row),
            "count": row.count,
            "results": [
                media_summary_from_item(item, request=request, user=request.user, include_user_state=False)
                for item in items
            ],
        },
    )
