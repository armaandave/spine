"""Undate the old eye taps (decision 49; one-time step, scripts/ios/SHIP.md).

Before the TV contract, the eye wrote a season's remaining episodes as dated viewings in
one bulk insert, so they still count in a year as if watched that day. Marking as watched
is never activity (decision 11), and Armaan said yes to moving them out of their years.
This finds them as count 11 does (plan 5.12) and turns each into a mark's fill: no day,
All Time only.

A tap is 2 or more episodes of one season of one show, with no log of their own, saved
before the TV repair (the earliest fill's moment, when the new code first wrote one), all
within 2 seconds, with nothing of another show in that window: imported plays sharing a
moment are real dates. When the account's quick watch date isn't Release Date, the group
must also share one end_date, which that writer stamped; a group that doesn't stays a
tick. On a Release Date account each episode had its own end_date, so the group must sit
within 50 milliseconds instead, as one bulk insert does: two quick taps are further apart.
A group saved within a minute after a season or show log covering it is that old
website log's own episodes, and stays: decision 43 dates it by its log.

For each tap it writes kind fill with no watched_on and no end_date, removes the show's
start row on the tap's day, works the show out again with no network so its last
watched day drops the tap, and bumps the owner's Stats. A later reconcile leaves a fill
undated, so a second run finds nothing.

A dry run by default, printing counts per account and year. --apply refuses when it finds
more episodes than --max (17, what count 11 found), so a wider match stops for a look. With
--apply the old values go to standard output as JSON lines, for the host wrapper to keep outside the container
(scripts/activity-steps-production.sh); counts go to standard error.
"""

import json
from collections import Counter, defaultdict
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Min, Q

from api.services import progress_posts, stats_cache
from app import tv_tracking, user_calendar
from app.models import TV, DiaryEntry, Episode, MediaTypes
from social.models import ProgressChange
from users.models import QuickWatchDateChoices

WINDOW = timedelta(seconds=2)
BULK = timedelta(milliseconds=50)
EXPECTED = 17
LOG_SAVE = timedelta(seconds=60)
FILL = Episode.Kind.FILL.value


def repair_moment():
    """Return when the TV repair first wrote a fill: only viewings before it can be taps."""
    return Episode.objects.filter(kind=FILL).aggregate(at=Min("created_at"))["at"]


def _covers(snapshot):
    raw = ((snapshot or {}).get("tv") or {}).get("covers") or {}
    covers = {}
    for key, numbers in raw.items():
        try:
            covers[int(key)] = {int(number) for number in numbers}
        except (TypeError, ValueError):
            continue
    return covers


def _logs(user):
    shows = {(source, media_id): item_id for item_id, source, media_id in TV.objects.filter(user=user).values_list("item_id", "item__source", "item__media_id")}
    logs = []
    for entry in DiaryEntry.objects.filter(
        user=user,
        item__media_type__in=(MediaTypes.TV.value, MediaTypes.SEASON.value),
    ).select_related("item"):
        logs.append(
            {
                "show": shows.get((entry.item.source, entry.item.media_id)),
                "season": entry.item.season_number if entry.item.media_type == MediaTypes.SEASON.value else None,
                "at": entry.created_at,
                "covers": _covers(entry.progress_snapshot),
            },
        )
    return logs


def _own_save(group, logs):
    """Return whether a season or show log covering the group saved it just before."""
    show, season = group[0].show_id, group[0].season
    episodes = {viewing.episode for viewing in group}
    for log in logs:
        if log["show"] != show or (log["season"] is not None and log["season"] != season):
            continue
        covered = log["covers"].get(season)
        if covered is None and not log["covers"]:
            covered = episodes
        if covered and episodes <= covered and timedelta(0) <= group[0].created_at - log["at"] <= LOG_SAVE:
            return True
    return False


def tap_groups(user, repair):
    """Return this account's old eye taps, each a list of its viewings."""
    if repair is None:
        return []
    viewings = list(
        Episode.objects.filter(related_season__user=user, diary_entry__isnull=True, created_at__lt=repair)
        .exclude(kind=FILL)
        .order_by("created_at", "id")
        .select_related("item", "related_season__related_tv"),
    )
    for viewing in viewings:
        viewing.season = viewing.item.season_number
        viewing.episode = viewing.item.episode_number
        viewing.show_id = viewing.related_season.related_tv.item_id
    windows = []
    for viewing in viewings:
        if windows and viewing.created_at - windows[-1][0].created_at <= WINDOW:
            windows[-1].append(viewing)
        else:
            windows.append([viewing])
    release_date = user.quick_watch_date == QuickWatchDateChoices.RELEASE_DATE
    logs = _logs(user)
    groups = []
    for window in windows:
        if len({viewing.show_id for viewing in window}) > 1:
            continue
        by_season = defaultdict(list)
        for viewing in window:
            by_season[viewing.season].append(viewing)
        for group in by_season.values():
            if len({viewing.episode for viewing in group}) < 2:
                continue
            if not release_date and len({viewing.end_date for viewing in group}) > 1:
                continue
            if release_date and group[-1].created_at - group[0].created_at > BULK:
                continue
            if _own_save(group, logs):
                continue
            groups.append(group)
    return groups


def _day(viewing, user):
    if viewing.watched_on is not None:
        return viewing.watched_on
    return user_calendar.day_of(viewing.end_date, user) if viewing.end_date else None


def _minutes(viewing):
    for value in (
        viewing.item.runtime_minutes,
        viewing.related_season.item.runtime_minutes,
        viewing.related_season.related_tv.item.runtime_minutes,
    ):
        if value:
            return value
    return 0


class Command(BaseCommand):
    help = "Turn the old eye taps into marks with no date, on every account (dry run unless --apply)"

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Change the rows.")
        parser.add_argument(
            "--max",
            type=int,
            default=EXPECTED,
            help=f"Refuse --apply above this many episodes (default {EXPECTED}).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        repair = repair_moment()
        self.stderr.write(f"old eye taps: the TV repair's first fill was saved {repair}")
        found = 0
        taps = []
        for user in get_user_model().objects.order_by("pk"):
            groups = tap_groups(user, repair)
            if not groups:
                continue
            taps.append((user, groups))
            years = Counter()
            minutes = Counter()
            for group in groups:
                for viewing in group:
                    day = _day(viewing, user)
                    years[day.year if day else "no day"] += 1
                    minutes[day.year if day else "no day"] += _minutes(viewing)
            counted = ", ".join(f"{year}: {years[year]} = {minutes[year]} min" for year in sorted(years, key=str))
            episodes = sum(len(group) for group in groups)
            found += episodes
            self.stderr.write(f"old eye taps: user {user.pk}: {len(groups)} taps, {episodes} episodes ({counted})")
        self.stderr.write(f"old eye taps: {found} episodes in all (expected at most {EXPECTED})")
        if not options["apply"]:
            self.stderr.write("dry run: nothing changed")
            return
        if found > options["max"]:
            raise CommandError(f"old eye taps: {found} episodes is more than {options['max']}; nothing changed")
        for user, groups in taps:
            self._undate(user, groups)

    def _undate(self, user, groups):
        shows = set()
        for group in groups:
            viewing_ids = [viewing.pk for viewing in group]
            for viewing in group:
                old = {"viewing": viewing.pk, "kind": viewing.kind, "watched_on": viewing.watched_on, "end_date": viewing.end_date}
                self.stdout.write(json.dumps(old, cls=DjangoJSONEncoder))
            Episode.objects.filter(pk__in=viewing_ids).update(kind=FILL, watched_on=None, end_date=None)
            show_id = group[0].show_id
            days = {_day(viewing, user) for viewing in group} | {user_calendar.day_of(viewing.created_at, user) for viewing in group}
            starts = [
                change
                for change in ProgressChange.objects.filter(actor=user, item_id=show_id).filter(
                    Q(current_progress__kind=ProgressChange.START) | Q(current_progress__unit=ProgressChange.START),
                )
                if (change.saved_on or user_calendar.day_of(change.created_at, user)) in days
            ]
            for change in starts:
                self.stdout.write(
                    json.dumps(
                        {"start_row": change.pk, "item": change.item_id, "saved_on": change.saved_on, "current": change.current_progress},
                        cls=DjangoJSONEncoder,
                    ),
                )
            progress_posts.delete_changes(user.pk, ProgressChange.objects.filter(pk__in=[change.pk for change in starts]))
            shows.add(group[0].related_season.related_tv_id)
        for tv_id in sorted(shows):
            try:
                tv_tracking._lock_and_reconcile(tv_id, entry_point=tv_tracking.REPAIR)  # noqa: SLF001
            except tv_tracking.TVDetailsUnavailable:
                self.stderr.write(f"old eye taps: show row {tv_id} has no stored listing, so its state waits for its next write")
        stats_cache.bump_user(user.pk)
        self.stderr.write(f"old eye taps: user {user.pk}: undated")
