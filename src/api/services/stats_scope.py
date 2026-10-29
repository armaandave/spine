"""The scope every stats v2 section reads: one owner, one viewer, one range, one tab.

docs/stats-contract.md, STAT-001 to STAT-006 and STAT-100 to STAT-109. Sections
(api.services.stats_v2_*) never parse parameters or decide visibility themselves; they
read these querysets and expressions, aggregate in SQL and format with the helpers at
the bottom of this module.

What a scope holds:

- who: ``owner``, ``viewer``, ``audience`` (owner, follower or public), ``is_owner``;
- when: ``zone`` (the owner's calendar), ``today``, ``start_date``/``end_date``
  (inclusive, both None in All Time), ``is_all_time``, ``calendar_year``,
  ``top_tag``, ``includes_today``;
- which tab: ``media_type`` (None on Global), ``item_types`` (the Item media types
  the tab counts: TV also counts season and episode rows), ``primary_types``;
- logs: ``all_entries`` (visible logs of the tab, every day) and ``entries`` (those
  in the range), ``log_day()`` and ``range_q()``;
- viewings: ``all_viewings`` (TV viewings of the tab that aren't hidden),
  ``viewings`` (in the range: dated ones in a dated range, every one in All Time),
  ``dated_viewings`` (those with a day), ``viewing_day()``;
- titles: ``title_id()`` (seasons and episodes fold to their show, STAT-102),
  ``titles()`` (STAT-105), ``events()`` (STAT-106), ``first_seen_q()`` (a title's
  first dated day falls in the range, STAT-213, STAT-214);
- posters: ``media_summaries()`` (STAT-304).
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from functools import cached_property, reduce
from operator import or_

from django.apps import apps
from django.db.models import (
    Case,
    CharField,
    DateField,
    Exists,
    F,
    FloatField,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
    When,
)
from django.db.models.fields.json import KT
from django.db.models.functions import Cast, Coalesce, TruncDate

from api.serializers.common import media_summary_from_item, prime_collection_items
from api.services import activity as activity_service
from api.services import filters as filter_service
from api.services import stats as stats_service
from app import exposure, user_calendar
from app.models import DiaryEntry, Episode, Item, MediaTypes, Status
from social.models import Follow, FollowStatus, ProgressChange

AUDIENCE_OWNER = "owner"
AUDIENCE_FOLLOWER = "follower"
AUDIENCE_PUBLIC = "public"

TV_ITEM_TYPES = (
    MediaTypes.TV.value,
    MediaTypes.SEASON.value,
    MediaTypes.EPISODE.value,
)
SHOW_PART_TYPES = (MediaTypes.SEASON.value, MediaTypes.EPISODE.value)
# Tabs with a Collections section (STAT-214, STAT-218).
COLLECTION_TABS = (
    MediaTypes.MOVIE.value,
    MediaTypes.GAME.value,
    MediaTypes.BOOK.value,
    MediaTypes.ANIME.value,
)
# Types with an original language (STAT-212).
LANGUAGE_TYPES = (MediaTypes.MOVIE.value, MediaTypes.TV.value)
# How each type marks a title consumed with no date (STAT-105).
DIRECT_CONSUMPTION_TYPES = (MediaTypes.MOVIE.value, MediaTypes.MUSIC.value)
COMPLETED_MANUALLY_TYPES = (MediaTypes.BOOK.value, MediaTypes.GAME.value)
COMPLETED_STATUS_TYPES = (
    MediaTypes.TV.value,
    MediaTypes.ANIME.value,
    MediaTypes.MANGA.value,
    MediaTypes.COMIC.value,
)


def build_scope(owner, viewer, params, *, request=None):
    """Validate the request's range and tab and return the scope (STAT-300, STAT-311).

    Raises rest_framework's ValidationError, so the view answers 400 with
    ``{key: [message]}``. The caller has already applied the profile gate.
    """
    stats_range = stats_service.parse_stats_range(params, owner=owner)
    media_type = stats_service.parse_stats_media_type(params.get("media_type"))
    return StatsScope(
        owner=owner,
        viewer=viewer,
        request=request,
        start_date=stats_range.start_date,
        end_date=stats_range.end_date,
        media_type=media_type,
    )


def audience_for(owner, viewer):
    """Return owner, follower (an accepted follow) or public."""
    if viewer is not None and getattr(viewer, "is_authenticated", False) and viewer.pk == owner.pk:
        return AUDIENCE_OWNER
    if (
        viewer is not None
        and getattr(viewer, "is_authenticated", False)
        and Follow.objects.filter(
            from_user=viewer,
            to_user=owner,
            status=FollowStatus.ACCEPTED,
        ).exists()
    ):
        return AUDIENCE_FOLLOWER
    return AUDIENCE_PUBLIC


class StatsScope:
    """One owner, one viewer, one range and one tab (STAT-001)."""

    def __init__(
        self,
        *,
        owner,
        viewer,
        request=None,
        start_date=None,
        end_date=None,
        media_type=None,
        audience=None,
        summaries=None,
    ):
        """Hold the validated request; querysets are built on first use."""
        self.owner = owner
        self.viewer = viewer
        self.request = request
        self.start_date = start_date
        self.end_date = end_date
        self.media_type = media_type
        if audience is not None:
            self.__dict__["audience"] = audience
        # Serialized media summaries by Item ID, shared with sibling scopes.
        self._summaries = {} if summaries is None else summaries

    # Who

    @cached_property
    def audience(self):
        return audience_for(self.owner, self.viewer)

    @property
    def is_owner(self):
        return self.audience == AUDIENCE_OWNER

    # When

    @cached_property
    def zone(self):
        """The owner's calendar (STAT-005)."""
        return user_calendar.calendar_tz(self.owner)

    @property
    def timezone_name(self):
        return self.zone.key

    @cached_property
    def today(self):
        return user_calendar.now().astimezone(self.zone).date()

    @property
    def is_all_time(self):
        return self.start_date is None and self.end_date is None

    @property
    def calendar_year(self):
        """The year of an exact 1 January to 31 December range, else None."""
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.start_date.year == self.end_date.year
            and (self.start_date.month, self.start_date.day) == (1, 1)
            and (self.end_date.month, self.end_date.day) == (12, 31)
        ):
            return self.start_date.year
        return None

    @property
    def top_tag(self):
        """The wanted Top tag (STAT-206): top{YEAR}, topalltime, or None."""
        if self.is_all_time:
            return "topalltime"
        if self.calendar_year is not None:
            return f"top{self.calendar_year}"
        return None

    @property
    def includes_today(self):
        """Whether today is in the range, so current streaks apply (STAT-200)."""
        if self.is_all_time:
            return True
        return self.start_date <= self.today <= self.end_date

    @property
    def week_starts_on(self):
        return getattr(self.owner, "week_start_day", None) or "monday"

    def range_payload(self):
        return {
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "timezone": self.timezone_name,
            "is_all_time": self.is_all_time,
        }

    # Which tab

    @cached_property
    def primary_types(self):
        """The exposed primary types this scope covers, in primary order."""
        exposed = exposure.primary_media_types()
        if self.media_type is None:
            return exposed
        return [self.media_type] if self.media_type in exposed else []

    @cached_property
    def item_types(self):
        """Return the Item media types counted: TV also counts seasons and episodes."""
        types = []
        for media_type in self.primary_types:
            types.extend(TV_ITEM_TYPES if media_type == MediaTypes.TV.value else (media_type,))
        return types

    def tab_q(self, item_path="item__"):
        """Match rows whose Item is in this scope's types (and exposed)."""
        return Q(**{f"{item_path}media_type__in": self.item_types})

    @property
    def counts_tv(self):
        return MediaTypes.TV.value in self.primary_types

    @property
    def has_collections(self):
        return self.media_type in COLLECTION_TABS

    @property
    def language_types(self):
        """The types Languages counts here; empty when the tab has no Languages."""
        if self.media_type is None:
            return [media_type for media_type in self.primary_types if media_type in LANGUAGE_TYPES]
        return [self.media_type] if self.media_type in LANGUAGE_TYPES else []

    @property
    def has_languages(self):
        return bool(self.language_types)

    def global_scope(self):
        """Return this scope on the Global tab: same owner, viewer and range."""
        if self.media_type is None:
            return self
        return self._sibling(media_type=None)

    def with_media_type(self, media_type):
        """Return this scope on another tab, sharing the audience and poster cache."""
        return self._sibling(media_type=media_type)

    def all_time(self):
        """Return this scope over All Time, for history (STAT-301's ``years.first``)."""
        if self.is_all_time:
            return self
        return self._sibling(start_date=None, end_date=None)

    def _sibling(self, **changes):
        values = {
            "owner": self.owner,
            "viewer": self.viewer,
            "request": self.request,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "media_type": self.media_type,
            "audience": self.__dict__.get("audience"),
            "summaries": self._summaries,
        }
        values.update(changes)
        return StatsScope(**values)

    # Logs

    @cached_property
    def visible_entries(self):
        """Every log the viewer may see, of any type (visible_diary_entries)."""
        return stats_service.visible_diary_entries(user=self.owner, viewer=self.viewer)

    @cached_property
    def all_entries(self):
        """Visible logs of this tab's types, every day."""
        return self.visible_entries.filter(self.tab_q()).order_by()

    @cached_property
    def entries(self):
        """Visible logs of this tab with a log day in the range (STAT-101, STAT-103)."""
        if self.is_all_time:
            return self.all_entries
        # The range is matched once, in a subquery: inline, a correlated subquery
        # (Highest rated, release ages) re-ran the per-row log-day test for every
        # log of the range on each outer row.
        return self.all_entries.filter(id__in=self.all_entries.filter(self.range_q()).values("id"))

    def log_day(self, field="consumed_at"):
        """Return a log's day, decided per row (STAT-100)."""
        return filter_service.log_day_expression(self.owner, field)

    def range_q(self, field="consumed_at"):
        """Match logs whose log day is in the range; empty in All Time (STAT-101)."""
        return filter_service.log_day_range_q(self.owner, self.start_date, self.end_date, field)

    # Viewings

    @cached_property
    def all_viewings(self):
        """TV viewings that aren't hidden (STAT-003, STAT-104), fills included.

        Empty on tabs other than Global and TV.
        """
        if not self.counts_tv:
            return Episode.objects.none()
        viewings = Episode.objects.filter(related_season__user=self.owner).order_by()
        if not self.is_owner:
            viewings = viewings.filter(
                Q(diary_entry__isnull=True) | Q(diary_entry_id__in=self.visible_entries.values("id")),
            )
        return viewings

    @cached_property
    def viewings(self):
        """Viewings in the range: dated ones in a dated range, every one in All Time."""
        if self.is_all_time:
            return self.all_viewings
        return self.all_viewings.filter(self.viewing_range_q())

    @cached_property
    def dated_viewings(self):
        """Viewings in the range that have a day; a mark's fills never do (STAT-104)."""
        if not self.is_all_time:
            return self.viewings
        return self.viewings.filter(self.viewing_has_day_q())

    @staticmethod
    def _log_fill_q():
        """Match fills a log made: they count on that log's day (STAT-104)."""
        return Q(kind=Episode.Kind.FILL, diary_entry__isnull=False)

    @staticmethod
    def viewing_has_day_q():
        return (
            Q(watched_on__isnull=False)
            | (Q(end_date__isnull=False) & ~Q(kind=Episode.Kind.FILL))
            | StatsScope._log_fill_q()
        )

    def viewing_day(self):
        """Return a viewing's day: watched_on, a non-fill's end_date, a fill's log."""
        return Coalesce(
            F("watched_on"),
            Case(
                When(
                    Q(end_date__isnull=False) & ~Q(kind=Episode.Kind.FILL),
                    then=TruncDate("end_date", tzinfo=self.zone),
                ),
                When(self._log_fill_q(), then=self.log_day("diary_entry__consumed_at")),
                default=Value(None, output_field=DateField()),
                output_field=DateField(),
            ),
            output_field=DateField(),
        )

    def viewing_range_q(self):
        """Match viewings whose day is in the range (same rule as viewing_day)."""
        if self.is_all_time:
            return Q()
        return self.viewing_days_q(self.start_date, self.end_date)

    def viewing_days_q(self, start_date=None, end_date=None):
        """Match viewings whose day (viewing_day) is on or between the dates.

        Either date may be None for an open end; with neither, every viewing matches,
        dated or not.
        """
        if start_date is None and end_date is None:
            return Q()
        watched = Q()
        moment = Q(watched_on__isnull=True)
        if start_date is not None:
            watched &= Q(watched_on__gte=start_date)
            moment &= Q(end_date__gte=datetime.combine(start_date, time.min, tzinfo=self.zone))
        if end_date is not None:
            watched &= Q(watched_on__lte=end_date)
            before = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=self.zone)
            moment &= Q(end_date__lt=before)
        log_days = filter_service.log_day_range_q(self.owner, start_date, end_date, "diary_entry__consumed_at")
        return (
            watched
            | (moment & ~Q(kind=Episode.Kind.FILL))
            | (Q(watched_on__isnull=True) & self._log_fill_q() & log_days)
        )

    # Titles

    @staticmethod
    def title_id(item_path="item"):
        """Return the title Item ID (STAT-102): seasons and episodes count as the show.

        ``item_path`` is the Item foreign key of the queried model, or None to query
        Item itself. When the show Item doesn't exist the row is its own title.
        """
        prefix = f"{item_path}__" if item_path else ""
        own_id = f"{item_path}_id" if item_path else "id"
        show = Item.objects.filter(
            media_type=MediaTypes.TV.value,
            source=OuterRef(f"{prefix}source"),
            media_id=OuterRef(f"{prefix}media_id"),
            season_number__isnull=True,
            episode_number__isnull=True,
        ).values("id")[:1]
        return Case(
            When(
                **{f"{prefix}media_type__in": SHOW_PART_TYPES},
                then=Coalesce(Subquery(show), F(own_id)),
            ),
            default=F(own_id),
        )

    def log_title_ids(self):
        """Title IDs of the visible logs in the range."""
        return self.entries.annotate(stats_title_id=self.title_id()).values("stats_title_id")

    def viewing_title_ids(self):
        """Title IDs of the shows with a dated viewing in the range."""
        return self.dated_viewings.annotate(stats_title_id=self.title_id()).values("stats_title_id")

    def dated_title_ids(self, start_date=None, end_date=None):
        """One subquery of the title IDs with a dated fact on or between the dates.

        A dated fact is a visible log of the tab, or a dated viewing that isn't hidden
        (STAT-104). Either date may be None for an open end. A mark's fills and undated
        completions have no day, so they never date a title.
        """
        logs = self.all_entries.filter(filter_service.log_day_range_q(self.owner, start_date, end_date))
        parts = [logs.annotate(stats_title_id=self.title_id()).values("stats_title_id")]
        if self.counts_tv:
            viewings = self.all_viewings.filter(self.viewing_has_day_q(), self.viewing_days_q(start_date, end_date))
            parts.append(viewings.annotate(stats_title_id=self.title_id()).values("stats_title_id"))
        return union_ids(*parts)

    def first_seen_q(self, field):
        """Match rows whose title (``field``) was first seen in this dated range.

        A title is first seen on its earliest log day, for TV the earliest of its log
        days and dated viewing days (STAT-213, STAT-214): it has a dated fact in the
        range and none before it. Undated completions never count in a range.
        """
        day_before = self.start_date - timedelta(days=1)
        return Q(**{f"{field}__in": self.dated_title_ids(self.start_date, self.end_date)}) & ~Q(
            **{f"{field}__in": self.dated_title_ids(None, day_before)},
        )

    def first_seen_after_q(self, field):
        """Match rows whose title (``field``) was first seen after this range's end."""
        day_after = self.end_date + timedelta(days=1)
        return Q(**{f"{field}__in": self.dated_title_ids(day_after, None)}) & ~Q(
            **{f"{field}__in": self.dated_title_ids(None, self.end_date)},
        )

    def undated_completion_item_ids(self):
        """Querysets of Item IDs consumed with no log at all, one per type (STAT-105).

        Only All Time counts them; in a dated range the list is empty.
        """
        if not self.is_all_time:
            return []
        querysets = []
        for media_type in self.primary_types:
            if media_type in DIRECT_CONSUMPTION_TYPES:
                rows = apps.get_model("app", media_type).objects.filter(
                    user=self.owner,
                    direct_consumption=True,
                )
            elif media_type in COMPLETED_MANUALLY_TYPES:
                rows = apps.get_model("app", media_type).objects.filter(
                    user=self.owner,
                    completed_manually=True,
                )
            elif media_type in COMPLETED_STATUS_TYPES:
                rows = latest_tracking_rows(media_type, self.owner).filter(status=Status.COMPLETED.value)
            else:
                continue
            querysets.append(rows.filter(~Exists(self._any_log_of_title(media_type))).values("item_id"))
        return querysets

    def _any_log_of_title(self, media_type):
        logs = DiaryEntry.objects.filter(user=self.owner)
        if media_type == MediaTypes.TV.value:
            return logs.filter(
                item__media_type__in=TV_ITEM_TYPES,
                item__source=OuterRef("item__source"),
                item__media_id=OuterRef("item__media_id"),
            )
        return logs.filter(item_id=OuterRef("item_id"))

    def titles_q(self):
        """Match the scope's title Items (STAT-105) on an Item queryset."""
        parts = [self.log_title_ids()]
        if self.counts_tv:
            parts.append(self.viewing_title_ids())
        parts.extend(self.undated_completion_item_ids())
        return Q(id__in=union_ids(*parts))

    @cached_property
    def titles(self):
        """The scope's titles as an Item queryset, one row per title (STAT-105)."""
        return Item.objects.filter(self.titles_q()).order_by()

    def log_events(self):
        """(day, title ID, type bucket) of the visible logs in the range."""
        return (
            self.entries.annotate(
                event_day=self.log_day(),
                event_title=self.title_id(),
                event_bucket=media_type_bucket("item__media_type"),
            )
            .values_list("event_day", "event_title", "event_bucket")
            .order_by()
        )

    def viewing_events(self):
        """(day, title ID, "tv") of the dated viewings in the range."""
        return (
            self.dated_viewings.annotate(
                event_day=self.viewing_day(),
                event_title=self.title_id(),
                event_bucket=Value(MediaTypes.TV.value, output_field=CharField()),
            )
            .values_list("event_day", "event_title", "event_bucket")
            .order_by()
        )

    def progress_events(self):
        """(day, title ID, type bucket) of each day's progress and starts (STAT-106).

        As in the activity feed (docs/activity-contract.md, AC-1, AC-2): a title's
        recorded progress changes on one local day, kept when any unit, a start
        included, moved forward on net. TV counts only its starts: its ticks already
        are events, as dated viewings on the day watched.
        """
        changes = ProgressChange.objects.filter(actor=self.owner, item__media_type__in=self.item_types).exclude(
            Q(item__media_type__in=TV_ITEM_TYPES) & ~Q(current_progress__unit=ProgressChange.START),
        )
        if not self.is_all_time:
            changes = changes.filter(
                created_at__gte=datetime.combine(self.start_date, time.min, tzinfo=self.zone),
                created_at__lt=datetime.combine(self.end_date + timedelta(days=1), time.min, tzinfo=self.zone),
            )
        units = activity_service.UNITS
        rows = (
            changes.annotate(
                event_day=TruncDate("created_at", tzinfo=self.zone),
                event_title=self.title_id(),
                event_bucket=media_type_bucket("item__media_type"),
                delta=Cast(KT("current_progress__value"), FloatField())
                - Cast(KT("previous_progress__value"), FloatField()),
            )
            .values("event_day", "event_title", "event_bucket")
            .annotate(**{f"net_{unit}": Sum("delta", filter=Q(current_progress__unit=unit)) for unit in units})
            .filter(reduce(or_, (Q(**{f"net_{unit}__gt": 0}) for unit in units)))
            .values_list("event_day", "event_title", "event_bucket")
            .order_by()
        )
        return set(rows)

    @cached_property
    def events(self):
        """Distinct (day, title ID, type bucket) events (STAT-106).

        Logs, dated viewings, and each day's progress and starts. Fills and undated
        completions are never events. Sorted by day, then title.
        """
        events = self.log_events().distinct()
        if self.counts_tv:
            events = self.log_events().union(self.viewing_events())
        return sorted(set(events) | self.progress_events())

    # Posters

    def media_summaries(self, item_ids):
        """Media summaries by Item ID, with the owner's artwork (STAT-304).

        Loads only Items not serialized yet in this request, in one batch, primed
        through prime_collection_items; no tracking state, no provider call.
        """
        wanted = [item_id for item_id in dict.fromkeys(item_ids) if item_id is not None]
        missing = [item_id for item_id in wanted if item_id not in self._summaries]
        if missing:
            items = Item.objects.in_bulk(missing)
            prime_collection_items(items.values(), self.owner)
            for item_id, item in items.items():
                self._summaries[item_id] = media_summary_from_item(
                    item,
                    request=self.request,
                    user=self.owner,
                    include_user_state=False,
                )
        return {item_id: self._summaries[item_id] for item_id in wanted if item_id in self._summaries}

    def media_summary(self, item_id):
        return self.media_summaries([item_id]).get(item_id)


def union_ids(*querysets):
    """One subquery of every ID the one-column querysets return, for ``id__in``.

    OR'd ``id__in`` subqueries make Postgres scan every Item and test each one; one
    UNION lets it join the few IDs to the primary key.
    """
    first, *rest = [queryset.order_by() for queryset in querysets]
    return first.union(*rest) if rest else first


def latest_tracking_rows(media_type, owner):
    """Return the owner's latest tracking row per Item of one tracked type."""
    model = apps.get_model("app", media_type)
    latest = (
        model.objects.filter(user=owner, item_id=OuterRef("item_id"))
        .order_by("-created_at", "-id")
        .values("pk")[:1]
    )
    return model.objects.filter(user=owner, pk=Subquery(latest))


def media_type_bucket(field="item__media_type"):
    """Return a row's type bucket (Terms): season and episode rows belong to tv."""
    return Case(
        When(**{f"{field}__in": TV_ITEM_TYPES}, then=Value(MediaTypes.TV.value)),
        default=F(field),
        output_field=CharField(),
    )


# Formatting helpers, STAT-303.

_HUNDREDTH = Decimal("0.01")
_TENTH = Decimal("0.1")


def stars(stored):
    """Format a stored 0-10 rating as stars: halved, half up, "4.0", "3.65"."""
    if stored is None:
        return None
    value = (Decimal(str(stored)) / 2).quantize(_HUNDREDTH, rounding=ROUND_HALF_UP)
    text = f"{value:.2f}"
    return text.removesuffix("0")


def one_decimal(value):
    """Format everyone's stars or a difference: half up to one decimal, "-1.1"."""
    if value is None:
        return None
    return f"{Decimal(str(value)).quantize(_TENTH, rounding=ROUND_HALF_UP):.1f}"


def rating_bound(stored):
    """Format a stored bound with one decimal: "6.5", "10.0"."""
    return f"{Decimal(str(stored)).quantize(_TENTH, rounding=ROUND_HALF_UP):.1f}"


def day_string(value):
    """Format a date as "YYYY-MM-DD"; None stays None."""
    return value.isoformat() if value is not None else None


def month_string(value):
    """Format a day's month as "YYYY-MM"."""
    return f"{value.year:04d}-{value.month:02d}"
