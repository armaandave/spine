"""AC-3: every writer of a progress change stores the phone's date when saved.

A writer that forgets its day fails silently, so a source scan keeps the list of writers
closed, and each one has a behavior test here. Rows hold the owner's date on a New York
server, read from a Tokyo account too.
"""

import re
from datetime import date, datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings

from api.services import tracking as tracking_service
from app import book_tracking, game_tracking, series_tracking, tv_tracking
from app.models import Anime, Item, MediaTypes, Sources, Status
from app.tests.tv_fixtures import FakeTVProvider, make_show, make_user, patch_now
from social.models import ProgressChange

NEW_YORK = ZoneInfo("America/New_York")
DEC_31 = date(2026, 12, 31)
JAN_1 = date(2027, 1, 1)
# 23:50 on Dec 31 in New York, already Jan 1 in Tokyo.
LATE = datetime(2026, 12, 31, 23, 50, tzinfo=NEW_YORK)

WRITES = re.compile(
    r"ProgressChange\(|ProgressChange\.objects\.create|ProgressChange\.objects\.bulk_create|cls\.objects\.create",
)
# Every place a progress change is written. A new one needs its day, a behavior test here
# and a line on this list.
WRITERS = {
    "api/services/tracking.py": ["ProgressChange.objects.create"],
    "app/book_tracking.py": ["ProgressChange.objects.create"],
    "app/game_tracking.py": ["ProgressChange.objects.create"],
    "app/series_tracking.py": ["ProgressChange.objects.create"],
    "app/tv_tracking.py": ["ProgressChange(", "ProgressChange.objects.bulk_create"],
    # The class statement matches too.
    "social/models.py": ["ProgressChange(", "cls.objects.create", "cls.objects.create"],
}


class WriterScanTests(SimpleTestCase):
    def test_no_writer_is_missing_from_the_list(self):
        root = Path(settings.BASE_DIR)
        found = {}
        for path in root.rglob("*.py"):
            parts = path.relative_to(root).parts
            if "tests" in parts or "migrations" in parts or parts[0] == ".venv" or path.name.startswith("test_"):
                continue
            calls = WRITES.findall(path.read_text())
            if calls:
                found[path.relative_to(root).as_posix()] = sorted(calls)
        self.assertEqual(found, {path: sorted(calls) for path, calls in WRITERS.items()})


@override_settings(TIME_ZONE="America/New_York")
class WriterDayTests(TestCase):
    def setUp(self):
        for target in ("app.models.Item.fetch_releases", "app.tv_tracking._queue_external_ratings"):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        clock = patch_now(LATE)
        clock.__enter__()
        self.addCleanup(clock.__exit__, None, None, None)
        self.new_york = make_user(calendar="America/New_York")
        self.tokyo = make_user(calendar="Asia/Tokyo")
        self.counter = 0

    def item(self, media_type, **fields):
        self.counter += 1
        return Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=media_type,
            media_id=str(self.counter),
            title=f"{media_type} {self.counter}",
            image="https://example.com/poster.jpg",
            **fields,
        )

    def days(self, user, item):
        return list(
            ProgressChange.objects.filter(actor=user, item=item).order_by("id").values_list("saved_on", flat=True),
        )

    def test_a_book_start_and_save(self):
        for user, day in ((self.new_york, DEC_31), (self.tokyo, JAN_1)):
            item = self.item(MediaTypes.BOOK.value, total_pages=400)
            book_tracking.start(user, item)
            book_tracking.update_progress(user, item, progress_type="pages", value=40)
            self.assertEqual(self.days(user, item), [day, day])

    def test_a_status_move(self):
        for user, day in ((self.new_york, DEC_31), (self.tokyo, JAN_1)):
            item = self.item(MediaTypes.BOOK.value, total_pages=400)
            book_tracking.start(user, item)
            book_tracking.pause(user, item)
            self.assertEqual(self.days(user, item), [day, day])

    def test_a_game_start_and_save(self):
        for user, day in ((self.new_york, DEC_31), (self.tokyo, JAN_1)):
            item = self.item(MediaTypes.GAME.value)
            game_tracking.start(user, item)
            game_tracking.update_progress(user, item, total_minutes=120)
            self.assertEqual(self.days(user, item), [day, day])

    def test_a_manga_and_a_comic_start_and_save(self):
        for media_type in (MediaTypes.MANGA.value, MediaTypes.COMIC.value):
            for user, day in ((self.new_york, DEC_31), (self.tokyo, JAN_1)):
                item = self.item(media_type, series_total=100)
                series_tracking.start(user, item)
                series_tracking.update_progress(user, item, value=5)
                self.assertEqual(self.days(user, item), [day, day])

    def test_an_anime_start_and_save_through_the_generic_writer(self):
        for user, day in ((self.new_york, DEC_31), (self.tokyo, JAN_1)):
            item = self.item(MediaTypes.ANIME.value)
            Anime.objects.create(user=user, item=item, status=Status.PLANNING.value)
            with mock.patch("app.providers.services.get_media_metadata", return_value={"max_progress": 12}):
                tracking_service.set_status(
                    user,
                    source=item.source,
                    media_type=item.media_type,
                    media_id=item.media_id,
                    status=Status.IN_PROGRESS.value,
                )
                tracking_service.create_or_update_tracking(
                    user,
                    source=item.source,
                    media_type=item.media_type,
                    media_id=item.media_id,
                    data={"progress": 3},
                )
            self.assertEqual(self.days(user, item), [day, day])

    def test_a_show_start_and_its_bulk_written_season_row(self):
        with FakeTVProvider() as fake:
            for user, day in ((self.new_york, DEC_31), (self.tokyo, JAN_1)):
                show = make_show(fake, title=f"Show for {user.username}", seasons={1: 3})
                tv_tracking.tick(user, show, 1, 1)
                rows = ProgressChange.objects.filter(actor=user, item__media_id=show.media_id)
                self.assertEqual(rows.count(), 2)
                self.assertEqual({row.saved_on for row in rows}, {day})
