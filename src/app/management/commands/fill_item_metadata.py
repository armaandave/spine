"""fill_item_metadata: fill tracked or logged Items under the current metadata rules.

A dry run by default, with no provider calls: it counts, by type and provider, the
pending Items, those that only need a stamp, those that failed before, and the calls
and minutes the fill would take at its caps (app.metadata_fill.CAPS). --apply fills in
this process, paced the same way (the test backend, which has no worker); --apply
--queue starts the Celery chain instead (production). Safe to run again: a filled Item
is never fetched again.
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from api.services.item_metadata import CURRENT_METADATA_VERSION
from app import metadata_fill

STATUS_TEXT = {
    "running": "a fill chunk is running in a worker",
    "queued": "a fill chunk is queued",
    None: "no fill chain is queued or running",
}


class Command(BaseCommand):
    """Fill Item metadata (years, runtimes, genres, themes, original language)."""

    help = "Fill tracked or logged Items under the current metadata rules. Dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Fill. Without it nothing is fetched or saved.")
        parser.add_argument(
            "--queue",
            action="store_true",
            help="With --apply: start the paced Celery chain instead of filling in this process.",
        )
        parser.add_argument("--limit", type=int, help="With --apply: at most this many Items.")
        parser.add_argument(
            "--type",
            action="append",
            choices=sorted(CURRENT_METADATA_VERSION),
            dest="types",
            help="Only this media type (repeatable).",
        )
        parser.add_argument("--user", help="Only Items this username tracks or logged.")

    def handle(self, *args, **options):
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
        types = options["types"]

        if options["queue"]:
            if not options["apply"]:
                msg = "--queue needs --apply."
                raise CommandError(msg)
            if limit is not None or types or user is not None:
                msg = "--queue fills everything; --limit, --type and --user work only in this process."
                raise CommandError(msg)
            self._queue()
            return
        if options["apply"]:
            self._apply(types=types, user=user, limit=limit)
            return
        self._dry_run(types=types, user=user)

    def _queue(self):
        if metadata_fill.start_chain():
            self.stdout.write("Started the fill chain. Watch it with: grep 'metadata fill' in the worker's log.")
        else:
            self.stdout.write(f"Not started: {STATUS_TEXT[metadata_fill.chain_status()]}.")

    def _apply(self, *, types, user, limit):
        self.stdout.write("Filling in this process, paced; nothing else needs to run.")
        try:
            totals = metadata_fill.fill_in_process(media_types=types, user=user, limit=limit)
        except RuntimeError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            "Done: {items} Items (filled {filled}, stamp-only {stamp_only}, not found {not_found}, "
            "unstamped {unstamped}, retry later {retry_later}), {new_series_members} new series "
            "members.".format(**{
                key: totals[key]
                for key in (
                    "items",
                    "filled",
                    "stamp_only",
                    "not_found",
                    "unstamped",
                    "retry_later",
                    "new_series_members",
                )
            }),
        )
        pending = metadata_fill.pending_counts(media_types=types, user=user)
        if pending:
            left = ", ".join(f"{media_type} {count}" for media_type, count in pending.items())
            self.stdout.write(f"Still pending (blocked or paced providers; the hourly sweep retries): {left}")

    def _dry_run(self, *, types, user):
        report = metadata_fill.estimate(media_types=types, user=user)
        self.stdout.write("Dry run: no provider calls, nothing saved. Run with --apply to fill.")
        header = ("type", "provider", "pending", "stamp-only", "failed before", "calls", "minutes")
        rows = [
            (
                row["media_type"],
                row["provider"],
                row["pending"],
                row["stamp_only"],
                row["failed_before"],
                row["calls"],
                row["minutes"],
            )
            for row in report["rows"]
        ]
        if rows:
            totals = ("total", "", *(sum(row[index] for row in rows) for index in range(2, 6)), report["minutes"])
            widths = [max(len(str(value)) for value in column) for column in zip(header, *rows, totals, strict=True)]
            for values in (header, *rows, totals):
                self.stdout.write("  ".join(str(value).ljust(width) for value, width in zip(values, widths, strict=True)))
        else:
            self.stdout.write("Nothing is pending.")
        self.stdout.write(f"Waiting for a retry (failed in the last 7 days): {report['waiting_for_retry']}")
        self.stdout.write(f"New series members, roughly: {report['new_series_members']}")
        self.stdout.write(
            f"Estimated time: about {report['minutes']} min at the fill's caps (TV is per show, "
            "8 seasons a call; music runs last at one MusicBrainz call every 3 s).",
        )
        self.stdout.write(f"Chain: {STATUS_TEXT[report['status']]}.")
