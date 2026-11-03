"""MC-400: imports keep one tracking record per series (manga-comics-tracking-contract.md).

Every importer runs against its source's shape with no provider. No imported number is
activity (SW-500), and a source's volumes are never read (MC-001).
"""

import io
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import app.forms  # noqa: F401 - the Yamtrack importer reads app.forms
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from app import series_tracking
from app.models import Comic, DiaryEntry, Item, Manga, MangaAttempt, Sources, Status
from integrations.imports import anilist, kitsu, mal, yamtrack
from integrations.imports.mal_export.importer import MALExportImporter
from social.models import Activity, ProgressChange

READ = Status.COMPLETED.value
READING = Status.IN_PROGRESS.value
FINISHED = date(2020, 5, 1)


def mal_entry(media_id, title, status, *, chapters=0, total=0, reread=0, rereading=False, finish=None, start=None, score=0, volumes=0):
    list_status = {
        "status": status,
        "score": score,
        "num_chapters_read": chapters,
        "num_volumes_read": volumes,
        "num_times_reread": reread,
        "is_rereading": rereading,
        "comments": "",
        "updated_at": "2021-06-01T10:00:00+00:00",
    }
    if finish:
        list_status["finish_date"] = finish
    if start:
        list_status["start_date"] = start
    return {
        "node": {
            "id": media_id,
            "title": title,
            "main_picture": {"large": "https://example.com/cover.jpg"},
            "num_chapters": total,
            "genres": [],
        },
        "list_status": list_status,
    }


class SeriesImportBase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="reader", password="strong-password-123")

    def row(self, title):
        return Manga.objects.get(user=self.user, item__title=title)

    def assert_no_activity(self):
        self.assertFalse(ProgressChange.objects.filter(actor=self.user).exists())
        self.assertFalse(Activity.objects.filter(actor=self.user).exists())


class MyAnimeListImportTests(SeriesImportBase):
    """The MyAnimeList API importer."""

    def run_import(self, entries, mode="new"):
        responses = [{"data": [], "paging": {}}, {"data": entries, "paging": {}}]
        with patch("integrations.imports.mal.app.providers.services.api_request", side_effect=responses):
            return mal.importer("reader", self.user, mode)

    # MC-400: each reread is a finished attempt; a date lands on that date; one record.
    def test_a_read_series_with_rereads_is_one_record(self):
        counts, _ = self.run_import([
            mal_entry(2, "Berserk", "completed", chapters=380, total=380, reread=2, finish="2020-05-01", start="2020-01-01", score=9),
        ])
        self.assertEqual(counts["manga"], 1)
        row = self.row("Berserk")
        self.assertEqual(Manga.objects.filter(user=self.user).count(), 1)
        self.assertEqual(row.status, READ)
        self.assertEqual(row.progress, 380)
        self.assertEqual(row.item.series_total, 380)
        attempts = list(row.attempts.order_by("created_at", "id"))
        self.assertEqual([attempt.status for attempt in attempts], [READ, READ, READ])
        self.assertEqual([attempt.end_date for attempt in attempts], [None, None, FINISHED])
        entry = DiaryEntry.objects.get(user=self.user, item=row.item)
        self.assertEqual(entry.consumed_at, datetime(2020, 5, 1, tzinfo=UTC))
        self.assertEqual(entry.rating, Decimal("9"))
        self.assertEqual(attempts[-1].completion_diary_entry, entry)
        self.assertEqual(row.current_attempt, attempts[-1])
        self.assertEqual(row.rating_source, entry)
        state = series_tracking.state_payload(row)
        self.assertEqual(state["lifetime_read_count"], 3)
        self.assertFalse(state["offers_keep_reading"])
        self.assert_no_activity()

    # MC-400: an undated finished attempt can be removed from Reading History.
    def test_an_imported_undated_read_is_manageable(self):
        self.run_import([mal_entry(2, "Berserk", "completed", chapters=380, total=380, reread=1, finish="2020-05-01")])
        row = self.row("Berserk")
        state = series_tracking.state_payload(row)
        self.assertEqual([journey["status"] for journey in state["reading_history"]], [READ])
        series_tracking.delete_journey(self.user, row.item, state["reading_history"][0]["id"])
        self.assertEqual(self.row("Berserk").attempts.count(), 1)

    # MC-400: reading with a number is an attempt at that place, with no start.
    def test_reading_with_a_number_is_an_attempt_with_no_start(self):
        self.run_import([mal_entry(13, "One Piece", "reading", chapters=1100, volumes=99)])
        row = self.row("One Piece")
        self.assertEqual(row.status, READING)
        self.assertEqual(row.progress, 1100, "Never the volumes (MC-001)")
        attempt = row.attempts.get()
        self.assertEqual(attempt.status, READING)
        self.assertEqual(attempt.place, 1100)
        self.assertIsNone(attempt.start_date)
        self.assertEqual(row.current_attempt, attempt)
        self.assert_no_activity()
        self.assertEqual(row.history.count(), 1, "The number arrived with the row")
        self.assertEqual(row.history.first().history_date, datetime(2021, 6, 1, 10, tzinfo=UTC))

    # MC-400: reading a number on an attempt then saves from there.
    def test_the_next_save_counts_only_what_is_new(self):
        self.run_import([mal_entry(13, "One Piece", "reading", chapters=1100)])
        row = self.row("One Piece")
        series_tracking.update_progress(self.user, row.item, value=1105)
        change = ProgressChange.objects.get(actor=self.user)
        self.assertEqual(change.previous_progress["value"], 1100)
        self.assertEqual(change.current_progress["value"], 1105)

    # MC-400: read with no date is undated.
    def test_read_with_no_date_is_undated(self):
        self.run_import([mal_entry(5, "Monster", "completed", chapters=162, total=162, score=8)])
        row = self.row("Monster")
        self.assertEqual(row.status, READ)
        self.assertTrue(row.completed_manually)
        self.assertEqual(row.progress, 162)
        self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=row.item).exists())
        self.assertFalse(row.attempts.exists())
        self.assertEqual(row.score, Decimal("8"))
        self.assertIsNone(row.rating_source)
        series_tracking.undo_read(self.user, row.item)
        self.assertFalse(Manga.objects.filter(user=self.user, item=row.item).exists())

    # MC-400: To Read, Paused and Dropped stay the user's choice.
    def test_to_read_paused_and_dropped_stay_the_users_choice(self):
        self.run_import([
            mal_entry(21, "Planned", "plan_to_read"),
            mal_entry(22, "Held", "on_hold", chapters=50),
            mal_entry(23, "Stopped", "dropped", chapters=20),
        ])
        planned, held, stopped = self.row("Planned"), self.row("Held"), self.row("Stopped")
        self.assertEqual((planned.status, planned.progress), (Status.PLANNING.value, 0))
        self.assertEqual((held.status, held.progress), (Status.PAUSED.value, 50))
        self.assertEqual((stopped.status, stopped.progress), (Status.DROPPED.value, 20))
        for row in (planned, held, stopped):
            self.assertFalse(row.attempts.exists())
        self.assert_no_activity()
        # Picking up continues from the place (MC-201).
        self.assertEqual(series_tracking.start(self.user, held.item).progress, 50)
        self.assertEqual(series_tracking.start(self.user, stopped.item).progress, 20)

    # MC-400: rereading with no count has finished once.
    def test_a_reread_under_way_has_a_finished_read_and_an_attempt(self):
        self.run_import([mal_entry(2, "Berserk", "completed", chapters=120, total=380, rereading=True, finish="2020-05-01")])
        row = self.row("Berserk")
        self.assertEqual(row.status, READING)
        self.assertEqual(row.progress, 120)
        finished = row.attempts.get(status=READ)
        self.assertEqual(finished.end_date, FINISHED)
        self.assertEqual(finished.place, 380)
        state = series_tracking.state_payload(row)
        self.assertTrue(state["is_rereading"])
        self.assertEqual(state["current_journey"]["progress"]["value"], 120)

    # MC-400: overwrite replaces the series and its imported logs; hand-made logs stay.
    def test_overwrite_replaces_the_series_and_keeps_hand_made_logs(self):
        self.run_import([mal_entry(2, "Berserk", "completed", chapters=380, total=380, finish="2020-05-01")])
        item = self.row("Berserk").item
        series_tracking.reread(self.user, item)
        series_tracking.update_progress(self.user, item, value=4)
        _, hand_made = series_tracking.complete(self.user, item, completion_date=timezone.localdate())
        self.run_import(
            [mal_entry(2, "Berserk", "completed", chapters=380, total=380, finish="2021-07-01")],
            mode="overwrite",
        )
        row = self.row("Berserk")
        self.assertEqual(row.status, READ)
        self.assertEqual(row.attempts.count(), 2, "The source's read, and the kept log's")
        self.assertEqual(row.attempts.get(completion_diary_entry__import_source="myanimelist").end_date, date(2021, 7, 1))
        self.assertEqual(Manga.objects.filter(user=self.user).count(), 1)
        kept = row.attempts.get(completion_diary_entry=hand_made)
        self.assertEqual((kept.status, kept.place), (READ, 380))
        state = series_tracking.state_payload(row)
        self.assertEqual(state["lifetime_read_count"], 2)
        self.assertIn(hand_made.consumed_at.date().isoformat(), state["completion_dates"])
        self.assertFalse(ProgressChange.objects.filter(actor=self.user, item=item).exists(), "Its numbers left the feed")
        self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=item, consumed_at=datetime(2020, 5, 1, tzinfo=UTC)).exists())

    # MC-400: a kept log is a read: the eye can't undo past it, and it can be deleted.
    def test_a_kept_log_stays_a_read_when_the_source_has_no_date(self):
        self.run_import([mal_entry(2, "Berserk", "completed", chapters=380, total=380, finish="2020-05-01")])
        item = self.row("Berserk").item
        DiaryEntry.objects.filter(user=self.user, item=item, import_source="myanimelist").delete()
        Manga.objects.filter(user=self.user, item=item).delete()
        _, hand_made = series_tracking.complete(self.user, item, completion_date=timezone.localdate())
        self.run_import([mal_entry(2, "Berserk", "completed", chapters=380, total=380)], mode="overwrite")
        row = self.row("Berserk")
        self.assertEqual(row.status, READ)
        self.assertEqual(row.current_attempt.completion_diary_entry, hand_made)
        self.assertFalse(row.completed_manually, "It is this read, not one more")
        self.assertEqual(series_tracking.state_payload(row)["lifetime_read_count"], 1)
        with self.assertRaises(series_tracking.HistoryExists):
            series_tracking.undo_read(self.user, item)

    # MC-400: new mode leaves a tracked series alone.
    def test_new_mode_leaves_a_tracked_series_alone(self):
        item = Item.objects.create(source=Sources.MAL.value, media_type="manga", media_id="2", title="Berserk", image="https://example.com/b.jpg")
        series_tracking.start(self.user, item)
        self.run_import([mal_entry(2, "Berserk", "completed", chapters=380, total=380, finish="2020-05-01")])
        self.assertEqual(self.row("Berserk").status, READING)
        self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=item).exists())

    # MC-000 (SW-006): on the server's zone, a date from the source stays that day.
    @override_settings(TIME_ZONE="America/New_York")
    def test_a_source_date_stays_on_its_day_in_new_york(self):
        with timezone.override("America/New_York"):
            self.run_import([mal_entry(2, "Berserk", "completed", chapters=380, total=380, finish="2020-05-01")])
        row = self.row("Berserk")
        self.assertEqual(row.attempts.get().end_date, FINISHED)
        self.assertEqual(DiaryEntry.objects.get(user=self.user).consumed_at, datetime(2020, 5, 1, tzinfo=UTC))


class AniListImportTests(SeriesImportBase):
    """The AniList importer."""

    # MC-000 (SW-006): a Los Angeles reader on a New York server keeps the source's date.
    @override_settings(TIME_ZONE="America/New_York")
    def test_a_finish_date_stays_that_date_on_any_readers_calendar(self):
        self.user.calendar_timezone = "America/Los_Angeles"
        self.user.save(update_fields=["calendar_timezone"])
        with timezone.override("America/New_York"):
            self.run_import([
                self.entry(2, "Berserk", "COMPLETED", progress=380, completed={"year": 2020, "month": 5, "day": 1}),
            ])
        self.assertEqual(DiaryEntry.objects.get(user=self.user).consumed_at, datetime(2020, 5, 1, tzinfo=UTC))
        self.assertEqual(self.row("Berserk").attempts.get().end_date, FINISHED)

    def entry(self, media_id, title, status, *, progress=0, repeat=0, completed=None, chapters=None):
        return {
            "media": {
                "title": {"userPreferred": title},
                "coverImage": {"large": "https://example.com/cover.jpg"},
                "idMal": media_id,
                "chapters": chapters,
                "startDate": {"year": 2001, "month": None, "day": None},
            },
            "status": status,
            "score": 7.5,
            "progress": progress,
            "startedAt": {"year": None, "month": None, "day": None},
            "completedAt": completed or {"year": None, "month": None, "day": None},
            "updatedAt": 1700000000,
            "repeat": repeat,
            "notes": None,
        }

    def run_import(self, entries):
        response = {
            "data": {
                "anime": {"lists": []},
                "manga": {"lists": [{"isCustomList": False, "entries": entries}]},
            },
        }
        with patch("integrations.imports.anilist.app.providers.services.api_request", return_value=response):
            return anilist.importer(None, self.user, "new", "reader")

    # MC-400: REPEATING, CURRENT, PLANNING.
    def test_rereads_and_statuses(self):
        counts, _ = self.run_import([
            self.entry(2, "Berserk", "COMPLETED", progress=380, repeat=1, completed={"year": 2020, "month": 5, "day": 1}, chapters=380),
            self.entry(13, "One Piece", "CURRENT", progress=1100),
            self.entry(3, "Vagabond", "REPEATING", progress=4, chapters=327),
            self.entry(4, "Monster", "PLANNING"),
        ])
        self.assertEqual(counts["manga"], 4)
        berserk = self.row("Berserk")
        self.assertEqual(berserk.attempts.count(), 2)
        self.assertEqual(berserk.score, Decimal("7.5"), "A rating off the half-star scale stays as imported")
        self.assertEqual(DiaryEntry.objects.get(user=self.user, item=berserk.item).consumed_at.date(), FINISHED)
        one_piece = self.row("One Piece")
        self.assertEqual(one_piece.attempts.get().place, 1100)
        vagabond = self.row("Vagabond")
        self.assertEqual(vagabond.status, READING)
        self.assertEqual(vagabond.progress, 4)
        self.assertEqual(vagabond.attempts.filter(status=READ).get().place, 327)
        self.assertEqual(self.row("Monster").status, Status.PLANNING.value)
        self.assertEqual(Manga.objects.filter(user=self.user).count(), 4)
        self.assert_no_activity()


class KitsuImportTests(SeriesImportBase):
    """The Kitsu importer."""

    # MC-400: one record however many times it was reread.
    def test_rereads_are_finished_attempts(self):
        importer = kitsu.KitsuImporter("1", self.user, "new")
        metadata = {
            "id": "100",
            "attributes": {"canonicalTitle": "Berserk", "chapterCount": 380, "posterImage": {"medium": "https://example.com/b.jpg"}},
            "relationships": {"mappings": {"data": [{"id": "m1"}]}},
        }
        lookup = {"100": metadata}
        mappings = {"m1": {"attributes": {"externalSite": "myanimelist/manga", "externalId": "2"}}}
        entry = {
            "attributes": {
                "status": "completed",
                "progress": 380,
                "reconsumeCount": 2,
                "reconsuming": False,
                "ratingTwenty": 16,
                "startedAt": None,
                "finishedAt": "2020-05-01T12:00:00.000Z",
                "notes": None,
                "updatedAt": "2021-06-01T10:00:00.000Z",
            },
            "relationships": {"manga": {"data": {"id": "100"}}},
        }
        importer._process_entry(entry, "manga", lookup, mappings)
        from integrations.imports import helpers

        helpers.bulk_create_media(importer.bulk_media, self.user, source="kitsu")
        self.assertEqual(helpers.imported_counts(importer.bulk_media)["manga"], 1)
        row = self.row("Berserk")
        self.assertEqual(row.attempts.count(), 3)
        self.assertEqual(row.item.series_total, 380)
        self.assertEqual(DiaryEntry.objects.filter(user=self.user, item=row.item).count(), 1)
        self.assert_no_activity()


class YamtrackImportTests(SeriesImportBase):
    """The Yamtrack CSV importer."""

    HEADER = "media_id,source,media_type,title,image,season_number,episode_number,score,progress,status,start_date,end_date,notes,progressed_at\n"

    def run_import(self, *rows):
        file = io.BytesIO((self.HEADER + "".join(rows)).encode())
        return yamtrack.importer(file, self.user, "new")

    # MC-400: an old export's row per reread becomes one record. Rows come newest first, as
    # the exporter wrote them.
    def test_an_old_exports_row_per_reread_is_one_record(self):
        counts, warnings = self.run_import(
            '2,mal,manga,Berserk,https://example.com/b.jpg,,,9,380,Completed,2020-01-01,2020-05-01,Again,2020-05-01T12:00:00Z\n',
            '2,mal,manga,Berserk,https://example.com/b.jpg,,,8,380,Completed,2018-01-01,2018-06-01,,2018-06-01T12:00:00Z\n',
            '13,mal,manga,One Piece,https://example.com/o.jpg,,,,1100,In progress,2021-01-01,,,2021-01-01T12:00:00Z\n',
            '46568,comicvine,comic,Saga,https://example.com/s.jpg,,,,12,Paused,,,,2021-01-01T12:00:00Z\n',
        )
        self.assertEqual(warnings, "")
        self.assertEqual(counts, {"manga": 2, "comic": 1})
        row = self.row("Berserk")
        self.assertEqual(Manga.objects.filter(user=self.user, item__title="Berserk").count(), 1)
        self.assertEqual(row.notes, "Again")
        self.assertEqual(sorted(attempt.end_date for attempt in row.attempts.all()), [date(2018, 6, 1), FINISHED])
        ratings = DiaryEntry.objects.filter(user=self.user, item=row.item).order_by("consumed_at").values_list("rating", flat=True)
        self.assertEqual(list(ratings), [Decimal("8.0"), Decimal("9.0")])
        logs = DiaryEntry.objects.filter(user=self.user, item=row.item).order_by("consumed_at")
        self.assertEqual([log.is_rewatch for log in logs], [False, True])
        self.assertEqual(row.score, Decimal("9.0"))
        self.assertEqual(self.row("One Piece").attempts.get().start_date, date(2021, 1, 1))
        saga = Comic.objects.get(user=self.user)
        self.assertEqual((saga.status, saga.progress), (Status.PAUSED.value, 12))
        self.assertFalse(saga.attempts.exists())
        self.assert_no_activity()


    # MC-400: the live read is the newest row, whichever order the file has it in.
    def test_the_newest_row_is_the_read_under_way(self):
        self.run_import(
            '2,mal,manga,Berserk,https://example.com/b.jpg,,,,120,In progress,2021-01-01,,,2021-03-01T12:00:00Z\n',
            '2,mal,manga,Berserk,https://example.com/b.jpg,,,8,380,Completed,2018-01-01,2018-06-01,,2018-06-01T12:00:00Z\n',
        )
        row = self.row("Berserk")
        self.assertEqual((row.status, row.progress), (READING, 120))
        self.assertEqual(row.current_attempt.place, 120)
        self.assertEqual(row.attempts.filter(status=READ).count(), 1)

    # MC-000 (SW-006): a date-only cell stays that date on a reader's own calendar.
    @override_settings(TIME_ZONE="America/New_York")
    def test_a_finish_date_stays_that_date_on_any_readers_calendar(self):
        self.user.calendar_timezone = "America/Los_Angeles"
        self.user.save(update_fields=["calendar_timezone"])
        with timezone.override("America/New_York"):
            self.run_import(
                '2,mal,manga,Berserk,https://example.com/b.jpg,,,8,380,Completed,2020-01-01,2020-05-01,,2020-05-01T12:00:00Z\n',
            )
        self.assertEqual(DiaryEntry.objects.get(user=self.user).consumed_at, datetime(2020, 5, 1, tzinfo=UTC))


class MALExportImportTests(SeriesImportBase):
    """The MyAnimeList XML export importer."""

    TEMPLATE = """<?xml version="1.0" encoding="UTF-8" ?>
    <myanimelist>{entries}</myanimelist>"""

    def entry(self, media_id, title, status, *, chapters=0, total=0, times=0, rereading="NO", rereading_chap=0, finish="0000-00-00", volumes=0):
        return f"""<manga>
            <manga_mangadb_id>{media_id}</manga_mangadb_id><manga_title><![CDATA[{title}]]></manga_title>
            <manga_volumes>0</manga_volumes><manga_chapters>{total}</manga_chapters>
            <my_read_volumes>{volumes}</my_read_volumes><my_read_chapters>{chapters}</my_read_chapters>
            <my_start_date>0000-00-00</my_start_date><my_finish_date>{finish}</my_finish_date>
            <my_score>8</my_score><my_status>{status}</my_status><my_comments><![CDATA[]]></my_comments>
            <my_times_read>{times}</my_times_read><my_tags><![CDATA[seinen]]></my_tags>
            <my_rereading>{rereading}</my_rereading><my_rereading_chap>{rereading_chap}</my_rereading_chap>
        </manga>"""

    def run_import(self, *entries, mode="new"):
        payload = self.TEMPLATE.format(entries="".join(entries)).encode()
        importer = MALExportImporter(io.BytesIO(payload), self.user, mode, metadata_fetcher=lambda *_: {"image": "https://example.com/c.jpg"})
        return importer.import_data()

    # MC-400: times read become finished attempts; the finish date holds the log and its tags.
    def test_times_read_and_a_finish_date(self):
        counts, warnings = self.run_import(
            self.entry(2, "Berserk", "Completed", chapters=380, total=380, times=2, finish="2020-05-01"),
            self.entry(13, "One Piece", "Reading", chapters=1100, volumes=99),
        )
        self.assertIsNone(warnings)
        self.assertEqual(counts["manga"], 2)
        self.assertEqual(counts["diary"], 1)
        row = self.row("Berserk")
        self.assertEqual(Manga.objects.filter(user=self.user, item=row.item).count(), 1)
        self.assertEqual(row.attempts.count(), 3)
        entry = DiaryEntry.objects.get(user=self.user, item=row.item)
        self.assertEqual(entry.import_source_id, "manga:2:finish")
        self.assertEqual([tag.name for tag in entry.tags.all()], ["seinen"])
        self.assertEqual(row.item.series_total, 380)
        reading = self.row("One Piece")
        self.assertEqual(reading.progress, 1100, "Never the volumes (MC-001)")
        self.assertEqual(reading.attempts.get().place, 1100)
        self.assert_no_activity()

    # MC-400: a reread under way.
    def test_a_reread_under_way(self):
        self.run_import(self.entry(2, "Berserk", "Completed", chapters=380, total=380, rereading="YES", rereading_chap=40, finish="2020-05-01"))
        row = self.row("Berserk")
        self.assertEqual(row.status, READING)
        self.assertEqual(row.progress, 40)
        self.assertEqual(row.attempts.get(status=READ).end_date, FINISHED)

    # MC-400: importing twice in overwrite mode keeps one record and one log.
    def test_overwriting_twice_keeps_one_record_and_one_log(self):
        for _ in range(2):
            self.run_import(self.entry(2, "Berserk", "Completed", chapters=380, total=380, finish="2020-05-01"), mode="overwrite")
        row = self.row("Berserk")
        self.assertEqual(MangaAttempt.objects.filter(tracking=row).count(), 1)
        self.assertEqual(DiaryEntry.objects.filter(user=self.user, item=row.item).count(), 1)
        self.assertTrue(row.attempts.get().completion_diary_entry_id)

    # MC-400: overwriting drops the old attempts' numbers from the feed, and links a kept log.
    def test_overwriting_drops_old_progress_and_links_a_kept_log(self):
        self.run_import(self.entry(2, "Berserk", "Completed", chapters=380, total=380, finish="2020-05-01"))
        item = self.row("Berserk").item
        series_tracking.reread(self.user, item)
        series_tracking.update_progress(self.user, item, value=4)
        _, hand_made = series_tracking.complete(self.user, item, completion_date=timezone.localdate())
        self.assertTrue(ProgressChange.objects.filter(actor=self.user, item=item).exists())
        self.run_import(self.entry(2, "Berserk", "Completed", chapters=380, total=380, finish="2020-05-01"), mode="overwrite")
        row = self.row("Berserk")
        self.assertFalse(ProgressChange.objects.filter(actor=self.user, item=item).exists())
        self.assertEqual(row.attempts.get(completion_diary_entry=hand_made).status, READ)
        self.assertEqual(series_tracking.state_payload(row)["lifetime_read_count"], 2)

    # MC-400: a future finish date isn't a real read's date.
    def test_a_future_finish_date_is_undated(self):
        future = (timezone.localdate() + timedelta(days=30)).isoformat()
        counts, _ = self.run_import(self.entry(2, "Berserk", "Completed", chapters=380, total=380, finish=future))
        self.assertNotIn("diary", counts)
        self.assertTrue(self.row("Berserk").completed_manually)
