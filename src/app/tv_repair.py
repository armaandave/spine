"""The one-time TV repair (docs/tv-tracking-contract.md, TV-801 and TV-804).

The reconcile_tv command runs repair_show for every show iter_targets yields, one show
per transaction. Each show goes through the same conversion the user's first app or
web write runs (tv_tracking.work_out: legacy log conversion, the legacy rule, the
reconcile, then the legacy flag cleared), so whichever comes first, the repair or the
user's write, the result is the same. Then TV-804 fills empty show and season stars
from the user's newest rated log.

Log dates are converted inside tv_tracking.convert_legacy_logs (through work_out),
which passes calendar dates on the user's calendar, never timezone.localdate().
"""

import logging
from dataclasses import dataclass, field

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from simple_history.utils import bulk_update_with_history

from app import tv_listing, tv_tracking, user_calendar
from app.models import TV, DiaryEntry, Episode, Item, MediaTypes, Season, Status

logger = logging.getLogger(__name__)

STAR_FILL_REASON = "TV-804: stars filled from the newest rated log"
SEASON_CHOICES = (Status.PAUSED.value, Status.DROPPED.value)


@dataclass
class ShowReport:
    """What the repair changed, or would change in a dry run, for one user's show.

    Statuses are as the Library and season lists show them: None for a show that isn't
    in the Library and for a season that isn't started (TV-003, TV-205).
    """

    username: str
    title: str
    source: str
    media_id: str
    has_calendar: bool
    # The show was stored before this contract: a legacy row or unconverted logs.
    worked_out_legacy: bool = False
    newly_in_library: bool = False
    show_status: tuple | None = None  # (before, after) when it changes
    season_statuses: list = field(default_factory=list)  # (season number, before, after)
    # Season-level Paused or Dropped values the worked-out status replaced (TV-204).
    replaced_season_choices: list = field(default_factory=list)  # (number, before, after)
    # Logs whose shown day changes (A-67): {"entry_id", "label", "before", "after"}.
    shown_day_changes: list = field(default_factory=list)
    marks: list = field(default_factory=list)  # legacy rule marks, from work_out
    # TV-804: {"season_number": int | None, "rating": Decimal, "entry_id": int}.
    stars: list = field(default_factory=list)

    @property
    def changed(self):
        return bool(
            self.newly_in_library
            or self.show_status
            or self.season_statuses
            or self.replaced_season_choices
            or self.shown_day_changes
            or self.marks
            or self.stars,
        )


def iter_targets(user=None):
    """Yield (user, item) for every user and show with a TV row or a TV log.

    A TV log is a log of a tv, season or episode Item. item is the show's tv Item when
    it exists, else an Item of one of the user's logs of the show, which repair_show
    resolves. Ordered by user, then show, so --limit is repeatable.
    """
    rows = TV.objects.all()
    logs = DiaryEntry.objects.filter(item__media_type__in=tv_tracking.TV_TYPES)
    if user is not None:
        rows = rows.filter(user=user)
        logs = logs.filter(user=user)
    targets = {}
    for user_id, item_id, source, media_id in rows.values_list(
        "user_id",
        "item_id",
        "item__source",
        "item__media_id",
    ):
        targets[(user_id, source, media_id)] = item_id
    for user_id, item_id, source, media_id in logs.values_list(
        "user_id",
        "item_id",
        "item__source",
        "item__media_id",
    ).distinct():
        targets.setdefault((user_id, source, media_id), item_id)
    if not targets:
        return
    shows = {
        (source, media_id): item_id
        for item_id, source, media_id in Item.objects.filter(
            media_type=MediaTypes.TV.value,
            media_id__in={media_id for _, _, media_id in targets},
        ).values_list("id", "source", "media_id")
    }
    item_ids = {shows.get((source, media_id), item_id) for (_, source, media_id), item_id in targets.items()}
    items = Item.objects.in_bulk(item_ids)
    users = get_user_model().objects.in_bulk({user_id for user_id, _, _ in targets})
    for key in sorted(targets):
        user_id, source, media_id = key
        yield users[user_id], items[shows.get((source, media_id), targets[key])]


def repair_show(user, show_item, *, apply):
    """Bring one user's show under the contract (TV-801), then apply TV-804.

    show_item may be any tv, season or episode Item of the show. The listing is fetched
    and the episode Items are created before the transaction (TV-705). A dry run
    (apply False) rolls the transaction back after the report is collected; only the
    stored listing, a cache, persists.
    """
    show_item = tv_listing.show_item_for(show_item)
    report = ShowReport(
        username=user.get_username(),
        title=show_item.title,
        source=show_item.source,
        media_id=show_item.media_id,
        has_calendar=user_calendar.zone_named(user.calendar_timezone) is not None,
    )
    listing = tv_listing.get_listing(show_item)
    if apply:
        tv_tracking.prepare_items(user, show_item, listing)
    with transaction.atomic():
        _repair_locked(user, show_item, listing, report)
        if not apply:
            transaction.set_rollback(True)
    return report


def _repair_locked(user, show_item, listing, report):
    tv = TV.objects.select_for_update().filter(user=user, item=show_item).first()
    logs = DiaryEntry.objects.filter(_family(show_item), user=user)
    unconverted = any(not _stamped(snapshot) for snapshot in logs.values_list("progress_snapshot", flat=True))
    if tv is None and not logs.exists():
        return
    before_show = tv.status if tv is not None else None
    before_seasons = _season_statuses(tv)
    report.worked_out_legacy = unconverted or (tv is not None and tv.legacy_pending)
    if tv is None:
        tv = tv_tracking.ensure_tv(user, show_item)
        report.newly_in_library = True
    outcome = tv_tracking.work_out(tv, listing, entry_point=tv_tracking.REPAIR)
    report.marks = list(outcome["marks"])
    report.shown_day_changes = _labelled(outcome["shown_day_changes"])
    report.stars = fill_empty_stars(user, show_item)

    tv.refresh_from_db()
    after_show = tv.status if _in_library(user, show_item, tv) else None
    if after_show != before_show:
        report.show_status = (before_show, after_show)
    if after_show is None:
        report.newly_in_library = False
    after_seasons = _season_statuses(tv)
    for number in sorted(set(before_seasons) | set(after_seasons)):
        before = before_seasons.get(number, (None, None))
        after = after_seasons.get(number, (None, None))
        if before[0] != after[0]:
            report.season_statuses.append((number, before[0], after[0]))
        if before[1] in SEASON_CHOICES and after[1] != before[1]:
            report.replaced_season_choices.append((number, before[1], after[1]))


def fill_empty_stars(user, show_item):
    """TV-804: give empty show and season stars the newest rated log of the same item.

    The newest rated log (by log date, then by when it was saved) becomes the stars'
    source (SW-108). Rows whose history shows stars that were later cleared are left
    alone: those were cleared on purpose. Writes history, so a later clear is seen as
    on purpose by the next run. Changes nothing else; no rating activity.
    """
    tv = TV.objects.filter(user=user, item=show_item).first()
    if tv is None:
        return []
    rows = [tv, *Season.objects.filter(related_tv=tv).select_related("item")]
    empty = [row for row in rows if row.score is None and row.rating_source_id is None]
    if not empty:
        return []
    cleared = {
        (TV, row_id)
        for row_id in TV.history.filter(id=tv.pk, score__isnull=False).values_list("id", flat=True)
    }
    cleared.update(
        (Season, row_id)
        for row_id in Season.history.filter(
            id__in=[row.pk for row in empty if isinstance(row, Season)],
            score__isnull=False,
        ).values_list("id", flat=True)
    )
    empty = [row for row in empty if (type(row), row.pk) not in cleared]
    newest = {}
    for item_id, entry_id, rating in (
        DiaryEntry.objects.filter(
            user=user,
            item_id__in=[row.item_id for row in empty],
            rating__isnull=False,
        )
        .order_by("-consumed_at", "-id")
        .values_list("item_id", "id", "rating")
    ):
        newest.setdefault(item_id, (entry_id, rating))
    filled = []
    for row in empty:
        if row.item_id not in newest:
            continue
        row.rating_source_id, row.score = newest[row.item_id]
        filled.append(row)
    now = user_calendar.now()
    for model in (TV, Season):
        group = [row for row in filled if isinstance(row, model)]
        if group:
            bulk_update_with_history(
                group,
                model,
                fields=["score", "rating_source"],
                default_change_reason=STAR_FILL_REASON,
                default_date=now,
            )
    return [
        {
            "season_number": row.item.season_number if isinstance(row, Season) else None,
            "rating": row.score,
            "entry_id": row.rating_source_id,
        }
        for row in filled
    ]


def _family(show_item):
    return Q(
        item__media_id=show_item.media_id,
        item__source=show_item.source,
        item__media_type__in=tv_tracking.TV_TYPES,
    )


def _stamped(snapshot):
    """Whether a TV log was saved under this contract (progress_snapshot["tv"]["v"])."""
    return isinstance(snapshot, dict) and isinstance(snapshot.get("tv"), dict) and (
        snapshot["tv"].get("v") is not None
    )


def _in_library(user, show_item, tv):
    """TV-003: a viewing, a log, a mark (its fills are viewings) or a choice."""
    return bool(
        tv.choice
        or Episode.objects.filter(related_season__related_tv=tv).exists()
        or DiaryEntry.objects.filter(_family(show_item), user=user).exists(),
    )


def _season_statuses(tv):
    """Return {season number: (shown status, stored status)} for the show's season rows.

    A season worked out as not started shows no status (TV-205); a season not yet
    worked out (is_started null) shows its stored status, as it did before.
    """
    if tv is None:
        return {}
    return {
        number: (None if is_started is False else status, status)
        for number, status, is_started in Season.objects.filter(related_tv=tv).values_list(
            "item__season_number",
            "status",
            "is_started",
        )
    }


def _labelled(changes):
    """Add each changed log's level ("the show", "Season 1", "S1 E2") to the change."""
    if not changes:
        return []
    items = {
        entry_id: (media_type, season_number, episode_number)
        for entry_id, media_type, season_number, episode_number in DiaryEntry.objects.filter(
            pk__in=[change["entry_id"] for change in changes],
        ).values_list("id", "item__media_type", "item__season_number", "item__episode_number")
    }
    labelled = []
    for change in changes:
        media_type, season_number, episode_number = items.get(change["entry_id"], (None, None, None))
        if media_type == MediaTypes.EPISODE.value:
            label = f"S{season_number} E{episode_number}"
        elif media_type == MediaTypes.SEASON.value:
            label = tv_listing.season_label(season_number)
        else:
            label = "the show"
        labelled.append({**change, "label": label})
    return labelled
