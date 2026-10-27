"""repair_season_posters: give season Items the show's poster saved on them their own back.

Marking a show watched, a season eye, a tick or season stars created the season Items
they needed with the show's poster, and pages show the poster saved on a season Item,
so those seasons showed the show's poster. This finds TMDB season Items whose saved
poster is their show's, asks TMDB once per show for its season posters, and sets each
season's own poster (or no poster, when TMDB has none). A season whose TMDB poster is
the show's own keeps it.

A dry run by default. --apply saves and needs --record, a JSON file of every change
(item id, old and new image); --undo with that file puts the old images back where the
new one is still saved. Only Item.image changes; viewer and admin picks win anyway.
"""

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from app.models import Item, MediaTypes, Sources
from app.providers import services


class Command(BaseCommand):
    """Put each affected season Item's own poster back."""

    help = "Give season Items that saved the show's poster their own poster. Dry run unless --apply."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Save the changes.")
        parser.add_argument("--record", help="With --apply: write every change to this JSON file.")
        parser.add_argument("--undo", help="Put back the old images listed in this --record file.")

    def handle(self, *args, **options):
        if options["undo"]:
            self._undo(Path(options["undo"]))
            return
        apply = options["apply"]
        record = options["record"]
        if apply and not record:
            msg = "--apply needs --record so the change can be undone."
            raise CommandError(msg)
        if record and Path(record).exists():
            msg = f"{record} already exists; pick a new file."
            raise CommandError(msg)

        changes = []
        for show, seasons in self._candidates():
            try:
                metadata = services.get_media_metadata(MediaTypes.TV.value, show.media_id, show.source)
            except services.ProviderAPIError as error:
                self.stderr.write(f"{show.title} ({show.media_id}): TMDB failed, skipped: {error}")
                continue
            posters = {
                season.get("season_number"): season.get("image") or settings.IMG_NONE
                for season in (metadata.get("related") or {}).get("seasons") or []
            }
            for season in seasons:
                if season.season_number not in posters:
                    self.stdout.write(f"{show.title} S{season.season_number}: not listed by TMDB, kept")
                    continue
                new = posters[season.season_number]
                if new == season.image:
                    continue
                changes.append({"id": season.id, "old": season.image, "new": new})
                self.stdout.write(f"{show.title} S{season.season_number} (item {season.id}): {season.image} -> {new}")

        if not apply:
            self.stdout.write(f"Dry run: {len(changes)} season poster(s) would change. Run with --apply --record FILE.")
            return
        Path(record).write_text(json.dumps(changes, indent=2))
        for change in changes:
            Item.objects.filter(pk=change["id"], image=change["old"]).update(image=change["new"])
        self.stdout.write(f"Changed {len(changes)} season poster(s); undo with --undo {record}.")

    def _candidates(self):
        """Yield (show Item, [season Items whose saved poster is the show's])."""
        shows = {
            (show.media_id, show.source): show
            for show in Item.objects.filter(media_type=MediaTypes.TV.value, source=Sources.TMDB.value)
        }
        by_show = {}
        for season in Item.objects.filter(
            media_type=MediaTypes.SEASON.value,
            source=Sources.TMDB.value,
        ).order_by("media_id", "season_number"):
            show = shows.get((season.media_id, season.source))
            if show is not None and show.image and season.image == show.image:
                by_show.setdefault(show, []).append(season)
        yield from by_show.items()

    def _undo(self, path):
        changes = json.loads(path.read_text())
        restored = sum(
            Item.objects.filter(pk=change["id"], image=change["new"]).update(image=change["old"])
            for change in changes
        )
        self.stdout.write(f"Put back {restored} of {len(changes)} season poster(s).")
