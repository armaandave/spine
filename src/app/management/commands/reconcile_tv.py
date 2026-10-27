"""reconcile_tv: bring existing TV data under the TV tracking contract (TV-801, TV-804).

A dry run by default: each show is worked out in its own transaction, the report is
collected, and the transaction is rolled back. --apply saves. Safe to run again: a
show already worked out reports nothing. Run it on the test backend first, and on
production only when Armaan asks (docs/tv-tracking-contract.md, TV-801).
"""

import logging
from itertools import islice

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from app import tv_repair
from app.tv_listing import season_label

logger = logging.getLogger(__name__)

NOT_IN_LIBRARY = "not in the Library"
NOT_STARTED = "not started"


class Command(BaseCommand):
    """Work out every tracked or logged TV show under the contract."""

    help = (
        "Bring existing TV data under the TV tracking contract (TV-801, TV-804). "
        "Dry run unless --apply."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Save the changes. Without it nothing is saved.",
        )
        parser.add_argument("--user", help="Only this username's shows.")
        parser.add_argument("--limit", type=int, help="At most this many shows.")

    def handle(self, *args, **options):
        apply = options["apply"]
        verbose = options["verbosity"] >= 2
        user = None
        if options["user"]:
            try:
                user = get_user_model().objects.get(username=options["user"])
            except get_user_model().DoesNotExist as error:
                msg = f"No user named {options['user']!r}."
                raise CommandError(msg) from error
        limit = options["limit"]
        if limit is not None and limit < 1:
            msg = "--limit must be 1 or more."
            raise CommandError(msg)

        if apply:
            self.stdout.write("Applying changes.")
        else:
            self.stdout.write("Dry run: nothing is saved. Run with --apply to save these changes.")
        self.stdout.write(f"Server timezone: {settings.TIME_ZONE}")

        reports = []
        errors = []
        for target_user, item in islice(tv_repair.iter_targets(user), limit):
            try:
                report = tv_repair.repair_show(target_user, item, apply=apply)
            except Exception as error:
                message = str(error)
                logger.exception("reconcile_tv could not repair %s for %s", item, target_user)
                errors.append((target_user.get_username(), item.title, message))
                self.stderr.write(f"{target_user.get_username()} · {item.title}: could not repair: {message}")
                continue
            reports.append(report)
            if report.changed or verbose:
                self._write_show(report)

        self._write_summary(reports, errors)
        if errors:
            msg = f"{len(errors)} show(s) could not be repaired; run the command again to retry them."
            raise CommandError(msg)

    def _write_show(self, report):
        write = self.stdout.write
        write("")
        write(f"{report.username} · {report.title} ({report.source} {report.media_id})")
        if report.newly_in_library:
            write("  Newly in the Library")
        if report.show_status:
            before, after = report.show_status
            write(f"  Status: {before or NOT_IN_LIBRARY} → {after or NOT_IN_LIBRARY}")
        replaced = {number for number, _, _ in report.replaced_season_choices}
        for number, before, after in report.season_statuses:
            line = f"  {season_label(number)}: {before or NOT_STARTED} → {after or NOT_STARTED}"
            if number in replaced:
                line += f" (season-level {before} replaced)"
            write(line)
        for number, before, after in report.replaced_season_choices:
            if not any(number == changed for changed, _, _ in report.season_statuses):
                write(f"  {season_label(number)}: stored {before} → {after} (season-level {before} replaced)")
        for mark in report.marks:
            level = "the show" if mark["season_number"] is None else season_label(mark["season_number"])
            write(f"  Marked watched (legacy rule): {level}, {mark['count']} episodes")
        for change in report.shown_day_changes:
            write(
                f"  Log {change['entry_id']} ({change['label']}): "
                f"shown day {change['before']} → {change['after']}",
            )
        for star in report.stars:
            level = "the show" if star["season_number"] is None else season_label(star["season_number"])
            write(f"  Stars filled: {level} {star['rating']} from log {star['entry_id']}")

    def _write_summary(self, reports, errors):
        write = self.stdout.write
        changed = [report for report in reports if report.changed]
        write("")
        write(f"Shows checked: {len(reports) + len(errors)}")
        write(f"Shows changed: {len(changed)}")
        write(f"Newly in the Library: {sum(report.newly_in_library for report in reports)}")
        status_changes = sum(bool(report.show_status) + len(report.season_statuses) for report in reports)
        write(f"Status changes: {status_changes}")
        replaced = sum(len(report.replaced_season_choices) for report in reports)
        write(f"Season-level Paused or Dropped replaced: {replaced}")
        write(f"Logs whose shown day changes: {sum(len(report.shown_day_changes) for report in reports)}")
        write(f"Legacy marks: {sum(len(report.marks) for report in reports)}")
        write(f"Stars filled: {sum(len(report.stars) for report in reports)}")
        write(f"Errors: {len(errors)}")
        no_calendar = sorted(
            {
                report.username
                for report in reports
                if report.worked_out_legacy and not report.has_calendar
            },
        )
        write(
            f"Users with no stored calendar (their logs convert on {settings.TIME_ZONE}): "
            f"{', '.join(no_calendar) or 'none'}",
        )

