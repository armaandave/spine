"""AC-2: every writer of a progress change stores the phone's date when saved.

A writer that forgets its day fails silently, so a source scan keeps the list of files
that name the model closed, and each writer has a behavior test here. Rows hold the
owner's date on a New York server, read from a Tokyo account too.
"""

import ast
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

MODEL = "ProgressChange"
RELATED = "progress_changes"
# Every file that names a progress change at all. A write can take too many shapes to
# match them all, so a file new to this list has to say what it does: one that only
# reads adds a line, and one that writes also needs its day, a behavior test here and a
# line in WRITERS.
NAMING = {
    "api/serializers/common.py",
    "api/services/activity.py",
    # Reads the planning rows, which ProgressChange.record_planning writes.
    "api/services/activity_planning.py",
    "api/services/progress_posts.py",
    # Fixes an older save's number in place and takes back the newest: it rewrites and
    # deletes rows, and creates none.
    "api/services/progress_rows.py",
    "api/services/activity_rows.py",
    "api/services/stats_scope.py",
    "api/services/tracking.py",
    "app/anime_tracking.py",
    "app/book_tracking.py",
    "app/game_tracking.py",
    "app/management/commands/compare_activity_numbers.py",
    "app/management/commands/fill_picked_up_books.py",
    "app/management/commands/fill_progress_days.py",
    "app/management/commands/flag_past_restarts.py",
    # Reads only: its dry run counts Twerp's saves.
    "app/management/commands/mark_twerp.py",
    # Reads start rows and deletes those on an old tap's day, through progress_posts.
    "app/management/commands/undate_old_eye_taps.py",
    "app/music_move.py",
    # Asks social.ProgressChange.record_planning to write an add to Planning.
    "app/planning_adds.py",
    "app/series_tracking.py",
    "app/tv_tracking.py",
    "social/models.py",
}
# Every place a progress change is written.
WRITERS = {
    "api/services/tracking.py": ["ProgressChange.objects.create"],
    "app/anime_tracking.py": ["ProgressChange.objects.create"],
    "app/book_tracking.py": ["ProgressChange.objects.create"],
    "app/game_tracking.py": ["ProgressChange.objects.create"],
    "app/series_tracking.py": ["ProgressChange.objects.create"],
    "app/tv_tracking.py": ["ProgressChange(", "ProgressChange.objects.bulk_create"],
    "social/models.py": ["cls.objects.create", "cls.objects.create", "cls.objects.create"],
}
CREATES = {"bulk_create", "create", "get_or_create", "update_or_create"}


def _says_the_model(text):
    return "progresschange" in text.lower() or RELATED in text


def names_the_model(source):
    """Return whether this source names a progress change in any way at all."""
    for node in ast.walk(ast.parse(source)):
        for field in ("id", "attr", "name", "arg"):
            name = getattr(node, field, None)
            # An import can be dotted, and an ORM lookup runs on past the name.
            if isinstance(name, str) and name.split("__")[0].split(".")[-1] in (MODEL, RELATED):
                return True
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and _says_the_model(node.value):
            return True
    return False


def writes_in(source, managers=(MODEL,)):
    """Return each write this source can be seen making, and which name their day.

    It reads the code, not its text, so a call wrapped across lines still counts. An
    alias counts as a write, because nothing after it can be seen.
    """
    writes = []
    named_day = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            writes += [f"{MODEL} as {name.asname}" for name in node.names if name.name == MODEL and name.asname]
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Name) and node.value.id == MODEL:
            writes.append(f"{MODEL} alias")
        elif isinstance(node, ast.Call):
            write = _write_called(node, managers)
            if write:
                writes.append(write)
                if any(keyword.arg == "saved_on" for keyword in node.keywords):
                    named_day.append(write)
    return writes, named_day


def _write_called(call, managers):
    function = call.func
    if isinstance(function, ast.Name):
        return f"{function.id}(" if function.id == MODEL else None
    if not isinstance(function, ast.Attribute):
        return None
    if function.attr == "get_model":
        named = any(isinstance(arg, ast.Constant) and arg.value == MODEL for arg in call.args)
        return f"{MODEL} get_model" if named else None
    if function.attr in CREATES and _goes_through_the_model(function.value, managers):
        return ast.unparse(function)
    return None


def _goes_through_the_model(receiver, managers):
    """Return whether a module path, a related manager or using() reaches the model."""
    while receiver is not None and not isinstance(receiver, ast.Name):
        if isinstance(receiver, ast.Attribute) and receiver.attr in (MODEL, RELATED):
            return True
        receiver = getattr(receiver, "value", None) or getattr(receiver, "func", None)
    return receiver is not None and receiver.id in managers


def source_files():
    """Return each source file's path and text, leaving out tests and migrations."""
    root = Path(settings.BASE_DIR)
    for path in root.rglob("*.py"):
        parts = path.relative_to(root).parts
        if "tests" in parts or "migrations" in parts or parts[0] == ".venv" or path.name.startswith("test_"):
            continue
        yield path.relative_to(root).as_posix(), path.read_text()


def writer_files():
    """Return each source file's writes."""
    for name, source in source_files():
        # social/models.py writes through its classmethods.
        yield name, writes_in(source, (MODEL, "cls") if name == "social/models.py" else (MODEL,))


class WriterScanTests(SimpleTestCase):
    """A writer that forgets its day fails silently, so the list of them is closed."""

    def test_no_file_names_the_model_off_the_list(self):
        found = {name for name, source in source_files() if names_the_model(source)}
        self.assertEqual(found, NAMING)

    def test_no_writer_is_missing_from_the_list(self):
        found = {name: sorted(writes) for name, (writes, _) in writer_files() if writes}
        self.assertEqual(found, {path: sorted(calls) for path, calls in WRITERS.items()})

    def test_every_writer_names_its_day(self):
        """A bulk_create takes rows, so its day is on the row it was built with."""
        for name, (writes, named_day) in writer_files():
            with self.subTest(name):
                self.assertEqual(sorted(named_day), sorted(write for write in writes if not write.endswith("bulk_create")))

    def test_any_new_way_of_writing_names_the_model(self):
        """A clone or type() can't be seen as a write, but what fetched the row can."""
        for source in (
            "from social.models import ProgressChange",
            "from social import models\nmodels.ProgressChange.objects.create(actor=user)",
            "user.progress_changes.create(item=item)",
            "item.progress_changes.create(actor=user)",
            "user.progress_changes.bulk_create(rows)",
            "social_models.ProgressChange.objects.create(actor=user)",
            "social.models.ProgressChange.objects.create(actor=user)",
            "ProgressChange._default_manager.create(actor=user)",
            'ProgressChange.objects.using("default").create(actor=user)',
            'apps.get_model("social.ProgressChange").objects.create(actor=user)',
            'apps.get_model("social.progresschange").objects.create(actor=user)',
            'get_model("social", "ProgressChange").objects.create(actor=user)',
            'getattr(user, "progress_changes").create(item=item)',
            "Item.objects.filter(progress_changes__actor=user)",
            "change = user.progress_changes.first()\ntype(change).objects.create(actor=user)",
            "change = user.progress_changes.first()\nchange.pk = None\nchange.save()",
        ):
            with self.subTest(source):
                self.assertTrue(names_the_model(source))

    def test_other_code_does_not_name_it(self):
        for source in (
            "Activity.objects.create(actor=user)",
            "latest_progress_change = serialize(row)",
            '"""About a day\'s progress."""',
        ):
            with self.subTest(source):
                self.assertFalse(names_the_model(source))

    def test_the_scan_sees_every_shape_of_a_write(self):
        shapes = {
            "ProgressChange(": "row = ProgressChange(actor=user)",
            "ProgressChange.objects.create": "ProgressChange.objects.create(actor=user)",
            "ProgressChange.objects.bulk_create": "ProgressChange.objects.bulk_create(rows)",
            "ProgressChange.objects.get_or_create": "ProgressChange.objects.get_or_create(actor=user)",
            "ProgressChange.objects.update_or_create": "ProgressChange.objects.update_or_create(actor=user)",
            "ProgressChange alias": "model = ProgressChange",
            "ProgressChange as Change": "from social.models import ProgressChange as Change",
            "ProgressChange get_model": 'apps.get_model("social", "ProgressChange").objects.create(actor=user)',
            "user.progress_changes.create": "user.progress_changes.create(item=item)",
            "item.progress_changes.bulk_create": "item.progress_changes.bulk_create(rows)",
            "social.models.ProgressChange.objects.create": "social.models.ProgressChange.objects.create(actor=user)",
            "ProgressChange._default_manager.create": "ProgressChange._default_manager.create(actor=user)",
            "ProgressChange.objects.using('default').create": 'ProgressChange.objects.using("default").create(actor=user)',
        }
        for write, source in shapes.items():
            with self.subTest(write):
                self.assertEqual(writes_in(source)[0], [write])
        wrapped = "(\n    ProgressChange.objects\n    .create(actor=user)\n)"
        self.assertEqual(writes_in(wrapped)[0], ["ProgressChange.objects.create"])

    def test_a_create_without_its_day_shows(self):
        self.assertEqual(writes_in("ProgressChange.objects.create(actor=user)")[1], [])
        named = "ProgressChange.objects.create(actor=user, saved_on=day)"
        self.assertEqual(writes_in(named)[1], ["ProgressChange.objects.create"])
        self.assertEqual(writes_in("user.progress_changes.create(item=item)")[1], [])

    def test_other_models_are_not_writes(self):
        self.assertEqual(writes_in("Activity.objects.create(actor=user)")[0], [])
        self.assertEqual(writes_in("ProgressChange.objects.filter(actor=user).delete()")[0], [])


@override_settings(TIME_ZONE="America/New_York")
class WriterDayTests(TestCase):
    """Each writer stores the owner's date: New York and Tokyo at 23:50 New York."""

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

    def test_an_anime_start_and_save_through_the_tracking_service(self):
        """The service hands anime to app/anime_tracking.py: Watching, then ticks."""
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

    def test_an_add_to_planning_through_each_path(self):
        """A movie through the tracking service, and a show where it chooses."""
        for user, day in ((self.new_york, DEC_31), (self.tokyo, JAN_1)):
            film = self.item(MediaTypes.MOVIE.value)
            tracking_service.create_or_update_tracking(
                user,
                source=film.source,
                media_type=film.media_type,
                media_id=film.media_id,
                data={"status": Status.PLANNING.value},
            )
            self.assertEqual(self.days(user, film), [day])
        with FakeTVProvider() as fake:
            for user, day in ((self.new_york, DEC_31), (self.tokyo, JAN_1)):
                show = make_show(fake, title=f"Planned for {user.username}", seasons={1: 3})
                tv_tracking.choose(user, show, tv_tracking.PLANNING)
                self.assertEqual(self.days(user, show), [day])
