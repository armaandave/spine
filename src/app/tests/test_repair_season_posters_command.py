"""repair_season_posters: season Items that saved the show's poster get their own back."""

import json
import tempfile
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import TestCase

from app.models import Item, MediaTypes, Sources
from app.tests.tv_fixtures import FakeTVProvider, make_show

SHOW_POSTER = "https://image.tmdb.org/t/p/original/show-picked.jpg"


class RepairSeasonPostersTests(TestCase):
    def setUp(self):
        self.fake = FakeTVProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        self.show = make_show(self.fake, seasons={1: 2, 2: 2, 3: 2}, image=SHOW_POSTER)
        self.fake.show(self.show.media_id).season_posters = {1: "/s1.jpg", 2: "/s2.jpg"}
        self.seasons = {
            number: Item.objects.create(
                media_id=self.show.media_id,
                source=Sources.TMDB.value,
                media_type=MediaTypes.SEASON.value,
                season_number=number,
                title=self.show.title,
                image=image,
            )
            for number, image in (
                (1, SHOW_POSTER),
                (2, "https://image.tmdb.org/t/p/w500/s2-old.jpg"),
                (3, SHOW_POSTER),
            )
        }
        self.record = Path(tempfile.mkdtemp()) / "changes.json"

    def images(self):
        return {number: Item.objects.get(pk=item.pk).image for number, item in self.seasons.items()}

    def run_command(self, *args):
        out = StringIO()
        call_command("repair_season_posters", *args, stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_dry_run_changes_nothing(self):
        before = self.images()

        output = self.run_command()

        self.assertIn("2 season poster(s) would change", output)
        self.assertEqual(self.images(), before)

    def test_apply_sets_each_affected_season_its_own_poster_and_undo_puts_it_back(self):
        before = self.images()

        self.run_command("--apply", "--record", str(self.record))

        self.assertEqual(
            self.images(),
            {
                1: "https://image.tmdb.org/t/p/w500/s1.jpg",
                # Its saved poster isn't the show's, so it's left alone.
                2: "https://image.tmdb.org/t/p/w500/s2-old.jpg",
                # TMDB has no poster for it.
                3: settings.IMG_NONE,
            },
        )
        self.assertEqual(len(json.loads(self.record.read_text())), 2)

        self.run_command("--undo", str(self.record))

        self.assertEqual(self.images(), before)

    def test_apply_needs_a_record(self):
        with self.assertRaises(CommandError):
            self.run_command("--apply")
