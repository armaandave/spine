"""AC-3 for anime: each history row says what the save was, which nothing can recover.

`day:<date>`, `log:<id>` or `mark`. Old code ignores the reason. The phone can't save
anime progress, so these reach anime through the API, the web form and imports.
"""

import io
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.utils import timezone

from api.services import tracking as tracking_service
from app.forms import AnimeForm
from app.models import Anime, DiaryEntry, Item, MediaTypes, Sources, Status
from app.services import create_diary_entry, update_diary_entry
from app.tests.tv_fixtures import make_user, patch_now
from integrations.imports import anilist, helpers, kitsu, mal, simkl, yamtrack
from integrations.imports.mal_export.importer import MALExportImporter

MOCK_DATA = Path(__file__).resolve().parent.parent.parent / "integrations" / "tests" / "mock_data"
NEW_YORK = "America/New_York"
TOKYO = "Asia/Tokyo"
LOS_ANGELES = "America/Los_Angeles"
NOW = datetime(2026, 10, 25, 20, 5, tzinfo=ZoneInfo(NEW_YORK))
TODAY = "day:2026-10-25"
ANIME_METADATA = {
    "title": "Exported",
    "image": "https://example.com/poster.jpg",
    "max_progress": 12,
    "details": {"episodes": 12},
}


class AnimeBase(TestCase):
    """A New York owner at a pinned time, with a provider that answers 12 episodes."""

    def setUp(self):
        for patcher in (
            mock.patch("app.models.Item.fetch_releases"),
            mock.patch("app.providers.services.get_media_metadata", return_value={"max_progress": 12}),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        clock = patch_now(NOW)
        clock.__enter__()
        self.addCleanup(clock.__exit__, None, None, None)
        self.user = make_user(calendar=NEW_YORK)
        self.counter = 0

    def item(self):
        self.counter += 1
        return Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=MediaTypes.ANIME.value,
            media_id=str(self.counter),
            title=f"Anime {self.counter}",
            image="https://example.com/poster.jpg",
        )

    def reasons(self, row):
        """Return each history row's reason, oldest first."""
        rows = Anime.history.filter(id=row.pk).order_by("history_date", "history_id")
        return list(rows.values_list("history_change_reason", flat=True))

    def tracked(self, status=Status.IN_PROGRESS.value, progress=0):
        return Anime.objects.create(user=self.user, item=self.item(), status=status, progress=progress)


@override_settings(TIME_ZONE=NEW_YORK)
class SaveReasonTests(AnimeBase):
    """What a plain save says it was."""

    def test_a_typed_number_counts_today(self):
        row = self.tracked()
        row.progress = 3
        row.save()
        self.assertEqual(self.reasons(row), [TODAY, TODAY])

    def test_web_plus_one_counts_today(self):
        row = self.tracked(progress=2)
        row.increase_progress()
        self.assertEqual(self.reasons(row)[-1], TODAY)

    def test_the_owners_calendar_decides_today(self):
        tokyo = make_user(calendar="Asia/Tokyo")
        with patch_now(datetime(2026, 12, 31, 20, 0, tzinfo=ZoneInfo(NEW_YORK))):
            row = Anime.objects.create(user=tokyo, item=self.item(), status=Status.IN_PROGRESS.value)
        self.assertEqual(self.reasons(row), ["day:2027-01-01"])

    def test_a_new_row_above_zero_counts_today(self):
        self.assertEqual(self.reasons(self.tracked(progress=3)), [TODAY])
        self.assertEqual(self.reasons(self.tracked(status=Status.PAUSED.value, progress=3)), [TODAY])

    def test_a_new_in_progress_row_is_the_start(self):
        self.assertEqual(self.reasons(self.tracked()), [TODAY])

    def test_a_new_row_that_opens_anywhere_else_at_zero_is_a_mark(self):
        for status in (Status.PLANNING.value, Status.PAUSED.value, Status.DROPPED.value):
            with self.subTest(status):
                self.assertEqual(self.reasons(self.tracked(status=status)), ["mark"])

    def test_a_status_move_counts_today(self):
        row = self.tracked(progress=3)
        row.status = Status.PAUSED.value
        row.save()
        self.assertEqual(self.reasons(row)[-1], TODAY)

    def test_typing_the_last_episode_of_a_live_read_stays_a_typed_number(self):
        row = self.tracked(progress=11)
        row.progress = 12
        row.save()
        row.refresh_from_db()
        self.assertEqual(row.status, Status.COMPLETED.value)
        self.assertEqual(self.reasons(row)[-1], TODAY)

    def test_setting_completed_is_a_mark(self):
        row = self.tracked(progress=3)
        row.status = Status.COMPLETED.value
        row.save()
        self.assertEqual(self.reasons(row)[-1], "mark")

    def test_a_new_completed_row_is_a_mark(self):
        row = Anime.objects.create(user=self.user, item=self.item(), status=Status.COMPLETED.value)
        self.assertEqual(self.reasons(row), ["mark"])

    def test_the_eye_through_the_api_is_a_mark(self):
        row = self.tracked()
        tracking_service.consume_media(
            self.user,
            source=row.item.source,
            media_type=row.item.media_type,
            media_id=row.item.media_id,
        )
        self.assertEqual(self.reasons(row)[-1], "mark")

    def test_the_eye_is_a_mark_whatever_date_it_carries(self):
        """A phone seconds ahead, a tap arriving after midnight, and an old date."""
        for sent in (NOW + timedelta(days=1), NOW - timedelta(days=1), NOW - timedelta(days=90)):
            for status, progress in ((Status.PLANNING.value, 0), (Status.COMPLETED.value, 5)):
                with self.subTest(sent=sent, status=status):
                    row = self.tracked(status=status, progress=progress)
                    before = len(self.reasons(row))
                    tracking_service.consume_media(
                        self.user,
                        source=row.item.source,
                        media_type=row.item.media_type,
                        media_id=row.item.media_id,
                        consumed_at=sent,
                    )
                    self.assertEqual(set(self.reasons(row)[before:]), {"mark"})

    def test_a_status_set_to_completed_through_the_api_is_a_mark(self):
        row = self.tracked(progress=3)
        tracking_service.set_status(
            self.user,
            source=row.item.source,
            media_type=row.item.media_type,
            media_id=row.item.media_id,
            status=Status.COMPLETED.value,
        )
        self.assertEqual(self.reasons(row)[-1], "mark")

    def test_anything_else_is_a_mark(self):
        row = self.tracked(progress=3)
        row.notes = "Good"
        row.save()
        self.assertEqual(self.reasons(row)[-1], "mark")


@override_settings(TIME_ZONE=NEW_YORK)
class WebFormTests(AnimeBase):
    """The browser's own posts: the form fills today's date by itself on Completed."""

    def post(self, instance, **data):
        item = instance.item
        form = AnimeForm(
            {
                "media_type": item.media_type,
                "source": item.source,
                "media_id": item.media_id,
                "status": Status.COMPLETED.value,
                "progress": 12,
                **data,
            },
            instance=instance,
        )
        self.assertTrue(form.is_valid(), form.errors)
        return form.save()

    def test_a_new_row_sent_with_todays_date_is_a_mark(self):
        row = self.post(Anime(user=self.user, item=self.item()), end_date="2026-10-25")
        self.assertEqual(self.reasons(row), ["mark"])

    def test_a_new_row_sent_with_an_earlier_date_counts_on_that_date(self):
        row = self.post(Anime(user=self.user, item=self.item()), end_date="2026-03-01")
        self.assertEqual(self.reasons(row), ["day:2026-03-01"])

    def test_an_edit_to_completed_sent_with_todays_date_is_a_mark(self):
        row = self.tracked(progress=3)
        self.post(row, end_date="2026-10-25")
        self.assertEqual(self.reasons(row)[-1], "mark")

    def test_an_edit_to_completed_sent_with_an_earlier_date_counts_on_that_date(self):
        row = self.tracked(progress=3)
        self.post(row, end_date="2026-03-01")
        self.assertEqual(self.reasons(row)[-1], "day:2026-03-01")


@override_settings(TIME_ZONE=NEW_YORK)
class BrowserDateTests(AnimeBase):
    """TRACK_TIME is on by default, so the form's date is the browser's own wall clock.

    Django reads it on the server's zone, so it isn't a moment on the owner's calendar.
    """

    # (Whose browser, the moment it filled the form)
    FILLED = (
        (NEW_YORK, datetime(2026, 12, 31, 20, 0, tzinfo=ZoneInfo(NEW_YORK))),
        (TOKYO, datetime(2027, 1, 1, 15, 0, tzinfo=ZoneInfo(TOKYO))),
        (TOKYO, datetime(2027, 1, 1, 9, 0, tzinfo=ZoneInfo(TOKYO))),
        (LOS_ANGELES, datetime(2027, 1, 1, 1, 0, tzinfo=ZoneInfo(LOS_ANGELES))),
        (LOS_ANGELES, datetime(2026, 12, 31, 12, 0, tzinfo=ZoneInfo(LOS_ANGELES))),
    )

    def post(self, instance, end_date):
        item = instance.item
        form = AnimeForm(
            {
                "media_type": item.media_type,
                "source": item.source,
                "media_id": item.media_id,
                "status": Status.COMPLETED.value,
                "progress": 12,
                "end_date": end_date,
            },
            instance=instance,
        )
        self.assertTrue(form.is_valid(), form.errors)
        return form.save()

    def test_the_date_it_fills_is_a_mark_on_any_calendar(self):
        for zone, moment in self.FILLED:
            sent = moment.strftime("%Y-%m-%dT%H:%M")
            with self.subTest(zone=zone, sent=sent), patch_now(moment):
                user = make_user(calendar=zone)
                row = self.post(Anime(user=user, item=self.item()), sent)
                self.assertEqual(self.reasons(row), ["mark"], "a new row")
                row = Anime.objects.create(user=user, item=self.item(), status=Status.IN_PROGRESS.value, progress=3)
                self.post(row, sent)
                self.assertEqual(self.reasons(row)[-1], "mark", "an edit")

    def test_a_typed_earlier_date_stays_that_date_on_any_calendar(self):
        for zone, moment in self.FILLED:
            for sent in ("2026-12-20T00:30", "2026-12-20T23:30"):
                with self.subTest(zone=zone, sent=sent), patch_now(moment):
                    user = make_user(calendar=zone)
                    row = self.post(Anime(user=user, item=self.item()), sent)
                    self.assertEqual(self.reasons(row), ["day:2026-12-20"], "a new row")
                    row = Anime.objects.create(user=user, item=self.item(), status=Status.IN_PROGRESS.value, progress=3)
                    self.post(row, sent)
                    self.assertEqual(self.reasons(row)[-1], "day:2026-12-20", "an edit")

    def test_a_date_past_today_is_a_mark(self):
        """With TRACK_TIME off the form fills the UTC date: tomorrow, some evenings."""
        with patch_now(datetime(2026, 12, 31, 20, 0, tzinfo=ZoneInfo(NEW_YORK))):
            row = self.post(Anime(user=self.user, item=self.item()), "2027-01-01")
        self.assertEqual(self.reasons(row), ["mark"])

    def test_an_unchanged_date_on_a_move_to_completed_is_a_mark(self):
        row = self.tracked(progress=3)
        Anime.objects.filter(pk=row.pk).update(end_date=datetime(2026, 3, 1, 12, 0, tzinfo=ZoneInfo(NEW_YORK)))
        row = Anime.objects.get(pk=row.pk)
        self.post(row, "2026-03-01T12:00")
        self.assertEqual(self.reasons(row)[-1], "mark")


@override_settings(TIME_ZONE=NEW_YORK)
class LogWriteTests(AnimeBase):
    """create_diary_entry writes two rows on a tracked title; both name the log."""

    def log(self, item):
        return create_diary_entry(self.user, item, consumed_at=timezone.now(), auto_mark_consumed=True)

    def test_every_row_of_a_logs_write_names_it(self):
        row = self.tracked(progress=3)
        before = len(self.reasons(row))
        entry = self.log(row.item)
        written = self.reasons(row)[before:]
        self.assertEqual(len(written), 2, "mark_consumed's rise and the end date's save")
        self.assertEqual(set(written), {f"log:{entry.pk}"})

    def test_a_log_that_creates_the_row_names_it(self):
        item = self.item()
        entry = self.log(item)
        self.assertEqual(self.reasons(Anime.objects.get(item=item)), [f"log:{entry.pk}"])

    def test_a_logs_date_edit_names_it(self):
        row = self.tracked(progress=3)
        entry = self.log(row.item)
        update_diary_entry(entry, {"consumed_at": timezone.now() - timedelta(days=1)})
        self.assertEqual(self.reasons(row)[-1], f"log:{entry.pk}")

    def test_a_later_save_is_its_own_again(self):
        row = self.tracked(progress=3)
        self.log(row.item)
        row = Anime.objects.get(pk=row.pk)
        row.progress = 4
        row.save()
        self.assertEqual(self.reasons(row)[-1], TODAY)

    def test_a_log_with_auto_mark_off_writes_no_row(self):
        row = self.tracked(progress=3)
        before = self.reasons(row)
        create_diary_entry(self.user, row.item, consumed_at=timezone.now(), auto_mark_consumed=False)
        self.assertEqual(self.reasons(row), before)
        self.assertEqual(DiaryEntry.objects.filter(item=row.item).count(), 1)


@override_settings(TIME_ZONE=NEW_YORK)
class ImportTests(TestCase):
    """Only a Completed row's end date dates a number; written-at moments never do."""

    def setUp(self):
        self.user = make_user(calendar=NEW_YORK)
        patcher = mock.patch("app.models.Item.fetch_releases")
        patcher.start()
        self.addCleanup(patcher.stop)

    def reason_of(self, row):
        rows = Anime.history.filter(id=row.pk).order_by("-history_date", "-history_id")
        return rows.values_list("history_change_reason", flat=True).first()

    def assert_each_row_says_what_it_is(self):
        rows = Anime.objects.filter(user=self.user)
        self.assertTrue(rows.exists())
        for row in rows:
            finished = row.status == Status.COMPLETED.value and row.end_date
            expected = f"day:{row.sent_end_day()}" if finished else "mark"
            self.assertEqual(self.reason_of(row), expected, row.item.title)

    def test_yamtrack_with_a_dated_completed_row_and_a_stamped_moment(self):
        csv = (
            '"media_id","source","media_type","title","image","season_number","episode_number","score",'
            '"progress","status","start_date","end_date","notes","progressed_at"\n'
            '"227","mal","anime","FLCL","https://example.com/a.jpg","","","","2","In progress",'
            '"2024-02-09","","","2024-02-09T10:00:00Z"\n'
            '"228","mal","anime","Marked","https://example.com/b.jpg","","","","12","Completed",'
            '"","","","2024-03-09T10:00:00Z"\n'
            '"229","mal","anime","Dated","https://example.com/c.jpg","","","","12","Completed",'
            '"","2024-04-09","","2024-05-09T10:00:00Z"\n'
        )
        with mock.patch("app.providers.services.get_media_metadata", return_value={"max_progress": 12}):
            yamtrack.importer(io.BytesIO(csv.encode()), self.user, "new")
        reasons = {row.item.title: self.reason_of(row) for row in Anime.objects.filter(user=self.user)}
        self.assertEqual(reasons, {"FLCL": "mark", "Marked": "mark", "Dated": "day:2024-04-09"})

    @mock.patch("requests.Session.get")
    def test_myanimelist(self, request):
        answers = [
            mock.MagicMock(**{"json.return_value": json.loads((MOCK_DATA / name).read_text())})
            for name in ("import_mal_anime.json", "import_mal_manga.json")
        ]
        request.side_effect = answers
        mal.importer("someone", self.user, "new")
        self.assert_each_row_says_what_it_is()

    @mock.patch("requests.Session.post")
    def test_anilist(self, request):
        request.return_value.json.return_value = json.loads((MOCK_DATA / "import_anilist.json").read_text())
        anilist.importer(None, self.user, "new", "someone")
        self.assert_each_row_says_what_it_is()

    @mock.patch("app.providers.services.api_request")
    def test_kitsu(self, request):
        request.side_effect = [
            json.loads((MOCK_DATA / "import_kitsu_anime.json").read_text()),
            json.loads((MOCK_DATA / "import_kitsu_manga.json").read_text()),
        ]
        kitsu.importer("123", self.user, "new")
        self.assert_each_row_says_what_it_is()

    def test_the_mal_export(self):
        with (
            mock.patch("app.providers.mal.anime", return_value=ANIME_METADATA),
            (MOCK_DATA / "import_mal_export_anime.xml").open("rb") as file,
        ):
            MALExportImporter(file, self.user, "new").import_data()
        self.assert_each_row_says_what_it_is()
        self.assertTrue(
            Anime.objects.filter(user=self.user, status=Status.COMPLETED.value, end_date__isnull=False).exists(),
        )

    def test_simkl(self):
        def row(mal_id, title, status, **fields):
            show = {"title": title, "ids": {"mal": mal_id}}
            return {"show": show, "status": status, "user_rating": None, "watched_episodes_count": 12, "memo": {}, **fields}

        rows = [
            row(1, "Watching", "watching", last_watched_at="2024-02-09T10:00:00Z"),
            row(2, "Marked", "completed"),
            # 22:00 on Apr 9 on the owner's calendar.
            row(3, "Dated", "completed", last_watched_at="2024-04-10T02:00:00Z"),
        ]
        metadata = {**ANIME_METADATA, "title": "Simkl"}
        with (
            mock.patch("app.providers.mal.anime", return_value=metadata),
            mock.patch("integrations.imports.simkl.SimklImporter._get_user_list", return_value={"anime": rows}),
        ):
            simkl.SimklImporter(helpers.encrypt("token"), self.user, "new").import_data()
        reasons = {row.item.media_id: self.reason_of(row) for row in Anime.objects.filter(user=self.user)}
        self.assertEqual(reasons, {"1": "mark", "2": "mark", "3": "day:2024-04-09"})

    def test_overwrite_mode(self):
        item = Item.objects.create(
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            media_id="229",
            title="Dated",
            image="https://example.com/c.jpg",
        )
        with mock.patch("app.providers.services.get_media_metadata", return_value={"max_progress": 12}):
            Anime.objects.create(user=self.user, item=item, status=Status.IN_PROGRESS.value, progress=3)
            csv = (
                '"media_id","source","media_type","title","image","season_number","episode_number","score",'
                '"progress","status","start_date","end_date","notes","progressed_at"\n'
                '"228","mal","anime","Marked","https://example.com/b.jpg","","","","12","Completed",'
                '"","","","2024-03-09T10:00:00Z"\n'
                '"229","mal","anime","Dated","https://example.com/c.jpg","","","","12","Completed",'
                '"","2024-04-09","","2024-05-09T10:00:00Z"\n'
            )
            yamtrack.importer(io.BytesIO(csv.encode()), self.user, "overwrite")
        reasons = {row.item.title: self.reason_of(row) for row in Anime.objects.filter(user=self.user)}
        self.assertEqual(reasons, {"Marked": "mark", "Dated": "day:2024-04-09"})

    def test_the_bulk_update_path(self):
        rows = []
        with mock.patch("app.providers.services.get_media_metadata", return_value={"max_progress": 12}):
            for number in (1, 2):
                item = Item.objects.create(
                    source=Sources.MANUAL.value,
                    media_type=MediaTypes.ANIME.value,
                    media_id=str(number),
                    title=f"Anime {number}",
                    image="https://example.com/poster.jpg",
                )
                rows.append(Anime.objects.create(user=self.user, item=item, status=Status.IN_PROGRESS.value))
        dated, marked = rows
        dated.status = marked.status = Status.COMPLETED.value
        dated.end_date = datetime(2026, 3, 1, tzinfo=UTC)
        fields = {MediaTypes.ANIME.value: ["status", "end_date"]}
        helpers.bulk_update_media({MediaTypes.ANIME.value: rows}, fields, self.user)
        self.assertEqual(self.reason_of(dated), "day:2026-03-01")
        self.assertEqual(self.reason_of(marked), "mark")

    def test_a_date_stays_its_calendar_day_on_any_zone(self):
        row = Anime(
            user=self.user,
            status=Status.COMPLETED.value,
            end_date=datetime(2026, 3, 1, tzinfo=UTC),
        )
        self.assertEqual(row.sent_end_day(), date(2026, 3, 1))
        row.end_date = datetime(2026, 3, 1, tzinfo=ZoneInfo(NEW_YORK))
        self.assertEqual(row.sent_end_day(), date(2026, 3, 1))
