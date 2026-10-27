"""Move existing music albums from MusicBrainz to Apple Music, and back.

See "Moving existing albums" in docs/music/apple-music-catalog.md. On production it
runs only through scripts/music-catalog-production.sh, which backs the database up
before any subcommand that changes data.
"""

import os
import stat

from django.core.management.base import BaseCommand, CommandError

from app import music_move


class Command(BaseCommand):
    """Run one step of the music data move and print its counts."""

    help = "Move music albums from MusicBrainz to Apple Music: check, map, report, link, apply, revert"

    def add_arguments(self, parser):
        steps = parser.add_subparsers(dest="step", required=True)
        check = steps.add_parser("check", help="Test the Apple Music key and count what needs attention")
        check.add_argument("--repair", action="store_true", help="Join strays' rows to the albums that moved")
        map_step = steps.add_parser("map", help="Match MusicBrainz albums to Apple Music albums (link rows only)")
        map_step.add_argument("--limit", type=int, default=None, help="Look at this many albums at most")
        report = steps.add_parser("report", help="Count every album; --csv writes each one's row")
        report.add_argument(
            "--csv",
            dest="csv_path",
            default=None,
            help="Write the CSV here (made readable by you only), or - for standard output",
        )
        link = steps.add_parser("link", help="Record a manual pick")
        link.add_argument("--mbid", required=True, help="MusicBrainz release group ID")
        link.add_argument("--apple", required=True, help="Apple Music album ID")
        apply_step = steps.add_parser("apply", help="Move matched albums")
        apply_step.add_argument("--limit", type=int, default=None, help="Move this many albums at most")
        revert = steps.add_parser("revert", help="Undo moves")
        which = revert.add_mutually_exclusive_group(required=True)
        which.add_argument("--mbid", default=None, help="Undo one album's move")
        which.add_argument("--all", dest="everything", action="store_true", help="Undo every move")

    def handle(self, *args, **options):  # noqa: ARG002
        step = options["step"]
        counts_out = self.stdout
        try:
            if step == "check":
                counts = music_move.check(repair_strays=options["repair"])
            elif step == "map":
                counts = music_move.map_albums(limit=options["limit"])
            elif step == "report":
                counts, counts_out = self._report(options["csv_path"])
            elif step == "link":
                counts = music_move.link(options["mbid"], options["apple"])
            elif step == "apply":
                counts = music_move.apply(limit=options["limit"])
            else:
                counts = music_move.revert(release_group_mbid=options["mbid"], everything=options["everything"])
        except music_move.MoveError as error:
            raise CommandError(str(error)) from error
        for name, value in counts.items():
            counts_out.write(f"{name}: {value}")

    def _report(self, csv_path):
        """Return the report's counts, and where to print them: off the CSV's stream."""
        if csv_path is None:
            return music_move.report(), self.stdout
        if csv_path == "-":
            return music_move.report(self.stdout._out), self.stderr
        descriptor = os.open(csv_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
        os.chmod(csv_path, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            counts = music_move.report(stream)
        self.stdout.write(f"report written: {csv_path}")
        return counts, self.stdout

