"""AC-2: a save can be put on the day it happened, for books, manga and comics.

The date picker sends `progressed_on` with a save (games already did). The save's row
keeps that day, so Stats and the Activity list read it there. Rules shared with games
(`single_weight.sent_day`): today is never refused, a day ahead caps at today, a day
before the start is refused (BK-306), and so is a day before the previous save.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from django.test import override_settings

from api.services import activity_rows
from api.tests.test_activity_new_year import NEW_YORK, TOKYO, NewYearBase
from app import book_tracking, series_tracking
from app.models import Book, MediaTypes
from social.models import ProgressChange

NEW_YORK_TZ = ZoneInfo(NEW_YORK)
# After midnight on New Year's: the picker is for saves that belong to last year.
JAN_2 = datetime(2027, 1, 2, 12, 0, tzinfo=NEW_YORK_TZ)
DEC_1 = date(2026, 12, 1)
DEC_20 = date(2026, 12, 20)
DEC_27 = date(2026, 12, 27)


class DatedSaveBase(NewYearBase):
    """An owner on January 2nd with a read that began on December 1st."""

    def setUp(self):
        super().setUp()
        self.clock.set(JAN_2)

    def started(self, media_type, **fields):
        item = self.item(media_type, **fields)
        if media_type == MediaTypes.BOOK.value:
            book_tracking.start(self.user, item, start_date=DEC_1)
        else:
            series_tracking.start(self.user, item, start_date=DEC_1)
        return item

    def save(self, item, value, day=None, *, expected=200, client=None):
        body = {"value": value}
        if item.media_type == MediaTypes.BOOK.value:
            body["progress_type"] = "pages"
        if day is not None:
            body["progressed_on"] = str(day)
        return self.post(client or self.phone(), item, "progress/", expected=expected, **body)

    def year(self, item, year, client=None):
        response = (client or self.phone()).get(
            "/api/v1/stats/me/summary/",
            {
                "schema": "2",
                "start_date": f"{year}-01-01",
                "end_date": f"{year}-12-31",
                "media_type": item.media_type,
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.json()["time"]["rows"]
        return next((row["value"] for row in rows if row["media_type"] == item.media_type), 0)

    def days(self, item):
        rows = activity_rows.rows_for([self.user], self.user, titles=[item.pk], piles=False).rows
        return {row.day: row.told for row in rows if not row.log_id and row.told}

    def stamps(self, item):
        changes = ProgressChange.objects.filter(item=item, current_progress__unit__in=("page", "chapter", "issue"))
        return list(changes.order_by("id").values_list("saved_on", flat=True))


@override_settings(TIME_ZONE=NEW_YORK)
class DatedSaveTests(DatedSaveBase):
    """A save sent in 2027, dated December 20th 2026, lands in 2026."""

    TYPES = (
        (MediaTypes.BOOK.value, {"total_pages": 400}),
        (MediaTypes.MANGA.value, {"series_total": 100}),
        (MediaTypes.COMIC.value, {"series_total": 100}),
    )

    def test_the_save_lands_in_the_year_it_was_dated(self):
        for media_type, fields in self.TYPES:
            with self.subTest(media_type):
                item = self.started(media_type, **fields)
                self.save(item, 12, DEC_20)
                self.assertEqual(self.stamps(item), [DEC_20])
                self.assertEqual(self.days(item), {DEC_20: 12})
                self.assertEqual((self.year(item, 2026), self.year(item, 2027)), (12, 0))

    def test_no_date_is_today_as_before(self):
        for media_type, fields in self.TYPES:
            with self.subTest(media_type):
                item = self.started(media_type, **fields)
                self.save(item, 12)
                self.assertEqual(self.stamps(item), [date(2027, 1, 2)])
                self.assertEqual((self.year(item, 2026), self.year(item, 2027)), (0, 12))

    def test_today_sent_is_today_and_a_day_ahead_caps_at_today(self):
        for media_type, fields in self.TYPES:
            with self.subTest(media_type):
                item = self.started(media_type, **fields)
                self.save(item, 10, date(2027, 1, 2))
                self.save(item, 11, date(2027, 1, 3))
                self.assertEqual(self.stamps(item), [date(2027, 1, 2)] * 2)

    def test_a_day_further_ahead_is_refused_and_saves_nothing(self):
        for media_type, fields in self.TYPES:
            with self.subTest(media_type):
                item = self.started(media_type, **fields)
                self.save(item, 10, date(2027, 1, 4), expected=400)
                self.assertEqual(self.stamps(item), [])

    def test_a_day_before_the_start_is_refused(self):
        for media_type, fields in self.TYPES:
            with self.subTest(media_type):
                item = self.started(media_type, **fields)
                self.save(item, 10, date(2026, 11, 30), expected=400)
                self.assertEqual(self.stamps(item), [])
                # The start's own day is fine.
                self.save(item, 10, DEC_1)
                self.assertEqual(self.stamps(item), [DEC_1])

    def test_a_day_before_the_previous_save_is_refused(self):
        for media_type, fields in self.TYPES:
            with self.subTest(media_type):
                item = self.started(media_type, **fields)
                self.save(item, 20, DEC_27)
                self.save(item, 25, DEC_20, expected=400)
                self.assertEqual(self.stamps(item), [DEC_27])
                # The same day, and today, are still fine.
                self.save(item, 25, DEC_27)
                self.save(item, 30)
                self.assertEqual(self.stamps(item), [DEC_27, DEC_27, date(2027, 1, 2)])

    def test_the_journey_payload_carries_the_latest_progress_day(self):
        book = self.started(MediaTypes.BOOK.value, total_pages=400)
        manga = self.started(MediaTypes.MANGA.value, series_total=100)
        self.assertIsNone(book_tracking.journey_payload(Book.objects.get(item=book).current_session)["progressed_on"])
        self.save(book, 20, DEC_27)
        self.save(manga, 20, DEC_27)
        session = Book.objects.get(item=book).current_session
        self.assertEqual(book_tracking.journey_payload(session)["progressed_on"], "2026-12-27")
        row = series_tracking.Manga.objects.get(item=manga)
        payload = series_tracking._journey_payload(row, row.current_attempt, [])
        self.assertEqual(payload["progressed_on"], "2026-12-27")

    def test_a_tokyo_account_keeps_the_day_it_sent(self):
        from app.tests.tv_fixtures import make_user

        tokyo = make_user(calendar=TOKYO)
        item = self.item(MediaTypes.BOOK.value, total_pages=400)
        book_tracking.start(tokyo, item, start_date=DEC_1)
        self.clock.set(datetime(2026, 12, 31, 20, 0, tzinfo=NEW_YORK_TZ))
        client = self.phone(zone=TOKYO, user=tokyo)
        self.save(item, 40, DEC_20, client=client)
        self.assertEqual(self.stamps(item), [DEC_20])
        self.clock.set(JAN_2)
        self.assertEqual(self.year(item, 2026, client), 40)
        self.assertEqual(self.year(item, 2027, client), 0)

    def test_the_picker_after_a_year_ago_start(self):
        """The sheet's case: started Nov 1 2025, 100 on Nov 2, 120 on Dec 20 2025."""
        self.clock.set(datetime(2026, 10, 25, 12, 0, tzinfo=NEW_YORK_TZ))
        item = self.item(MediaTypes.BOOK.value, total_pages=400)
        book_tracking.start(self.user, item, start_date=date(2025, 11, 1))
        self.save(item, 100, date(2025, 11, 2))
        self.save(item, 120, date(2025, 12, 20))
        self.assertEqual(self.days(item), {date(2025, 11, 2): 100, date(2025, 12, 20): 20})
        self.assertEqual((self.year(item, 2025), self.year(item, 2026)), (120, 0))


@override_settings(TIME_ZONE=NEW_YORK)
class DatedBeforeALogTests(DatedSaveBase):
    """A save dated before a log changes what the log added (decision 10)."""

    def finish(self, item, day):
        complete = book_tracking.complete if item.media_type == MediaTypes.BOOK.value else series_tracking.complete
        complete(self.user, item, completion_date=day)

    def log_rows(self, item):
        rows = activity_rows.rows_for([self.user], self.user, titles=[item.pk], piles=False).rows
        return sorted((row.day, row.told) for row in rows if row.log_id or row.told)

    def test_a_book(self):
        today = self.started(MediaTypes.BOOK.value, total_pages=400)
        self.save(today, 100)
        self.finish(today, DEC_20)
        # Saved today, the 100 is after the log's date: everything moves to the log.
        self.assertEqual(self.log_rows(today), [(DEC_20, 400)])
        dated = self.started(MediaTypes.BOOK.value, total_pages=400)
        self.save(dated, 100, DEC_1)
        self.finish(dated, DEC_20)
        self.assertEqual(self.log_rows(dated), [(DEC_1, 100), (DEC_20, 300)])

    def test_a_manga(self):
        today = self.started(MediaTypes.MANGA.value, series_total=100)
        self.save(today, 10)
        self.finish(today, DEC_20)
        self.assertEqual(self.log_rows(today), [(DEC_20, 100)])
        dated = self.started(MediaTypes.MANGA.value, series_total=100)
        self.save(dated, 10, DEC_1)
        self.finish(dated, DEC_20)
        self.assertEqual(self.log_rows(dated), [(DEC_1, 10), (DEC_20, 90)])
