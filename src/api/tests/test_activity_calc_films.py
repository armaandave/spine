"""The one calculation on films and albums: each log on its date, and the marker once.

Decision 38: over a mark, the first log by log day then id decides. Rewatch on, the mark
stands for an earlier watch and All Time holds both; off, the log dates that same watch.
The marker has no date, so it counts whether it came before or after the log. Writes go
through the phone's real requests, and numbers through the Stats endpoint.
"""

from datetime import date, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from api.services.activity_rows import list_rows, rows_for
from app import single_weight
from app.models import DiaryEntry, Item, MediaTypes, Sources
from app.tests.tv_fixtures import patch_now

MOVIE = MediaTypes.MOVIE.value
MUSIC = MediaTypes.MUSIC.value
NEW_YORK = "America/New_York"
YEAR = {"start_date": "2026-01-01", "end_date": "2026-12-31"}
ALL_TIME = {"start_date": "all", "end_date": "all"}


@override_settings(TIME_ZONE=NEW_YORK)
class FilmCalcBase(TestCase):
    """A 2-hour film and a 1-hour album, through the phone's requests."""

    media_type = MOVIE
    minutes = 120

    def setUp(self):
        super().setUp()
        self.clock = patch_now(datetime(2026, 3, 1, 12), tz=NEW_YORK).__enter__()  # noqa: DTZ001
        self.addCleanup(self.clock.__exit__, None, None, None)
        self.user = get_user_model().objects.create_user(username="owner", calendar_timezone=NEW_YORK)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.item = Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=self.media_type,
            media_id=f"film-{uuid4()}",
            title="Title",
            image="https://example.com/x.jpg",
            runtime_minutes=self.minutes,
        )

    def numbers(self):
        """Return (this year, All Time), as the Stats endpoint prints them."""
        values = []
        for params in (YEAR, ALL_TIME):
            response = self.client.get(
                "/api/v1/stats/me/summary/",
                {"schema": "2", "media_type": self.media_type, **params},
            )
            self.assertEqual(response.status_code, 200, response.content)
            rows = [row for row in response.json()["time"]["rows"] if row["media_type"] == self.media_type]
            values.append(rows[0]["value"] if rows else 0)
            if rows:
                self.assertIs(type(rows[0]["value"]), int)
                self.assertFalse(rows[0]["approximate"])
        return tuple(values)

    def watches(self, hours_this_year, hours_all_time):
        self.assertEqual(self.numbers(), (hours_this_year * self.minutes, hours_all_time * self.minutes))

    def eye(self):
        self.clock.advance(minutes=1)
        single_weight.mark_consumed(self.user, self.item)

    def log(self, *, rewatch, day=date(2026, 3, 1)):
        self.clock.advance(minutes=1)
        body = {
            "ref": {"source": "manual", "media_type": self.media_type, "media_id": self.item.media_id},
            "consumed_at": day.isoformat(),
            "is_rewatch": rewatch,
        }
        response = self.client.post("/api/v1/diary/", body, format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["id"]

    def flip(self, entry_id, *, on):
        response = self.client.patch(f"/api/v1/diary/{entry_id}/", {"is_rewatch": on}, format="json")
        self.assertEqual(response.status_code, 200, response.content)

    def delete(self, entry_id):
        response = self.client.delete(f"/api/v1/diary/{entry_id}/")
        self.assertEqual(response.status_code, 204, response.content)


class FilmTests(FilmCalcBase):
    """Decision 38 on a film."""

    def test_the_eye_alone_is_all_time_only(self):
        self.eye()
        self.watches(0, 1)
        self.assertEqual(rows_for([self.user], self.user, types=[self.media_type]).rows, [], "A mark makes no row")

    def test_eye_then_a_first_log_on_off_and_deleted(self):
        self.eye()
        entry = self.log(rewatch=True)
        self.watches(1, 2)
        self.flip(entry, on=False)
        self.watches(1, 1)
        self.flip(entry, on=True)
        self.watches(1, 2)
        self.delete(entry)
        self.watches(0, 1)

    def test_eye_first_log_off_second_on_is_two_watches_not_three(self):
        self.eye()
        first = self.log(rewatch=False)
        self.log(rewatch=True)
        self.watches(2, 2)
        self.delete(first)
        self.watches(1, 2)

    def test_first_goes_by_log_date_not_save_order(self):
        self.eye()
        self.log(rewatch=False)
        self.log(rewatch=True, day=date(2025, 12, 1))
        self.assertEqual(self.numbers()[1], 3 * self.minutes)

    def test_a_log_off_then_an_eye_stays_one_watch(self):
        self.log(rewatch=False)
        self.eye()
        self.watches(1, 1)

    def test_a_log_on_with_no_mark_then_an_eye_adds_a_watch(self):
        self.log(rewatch=True)
        self.watches(1, 1)
        self.eye()
        self.watches(1, 2)

    def test_an_import_with_no_rewatch_value_dates_the_marked_watch(self):
        """Trakt sends no Rewatch: its first play over a mark saves off, so it dates that watch."""
        self.eye()
        rows = [
            {"source_id": "a", "consumed_at": date(2026, 2, 1), "is_rewatch": None},
            {"source_id": "b", "consumed_at": date(2026, 2, 20), "is_rewatch": None},
        ]
        single_weight.import_logs(self.user, self.item, rows[:1], source="trakt")
        self.assertEqual(list(DiaryEntry.objects.values_list("is_rewatch", flat=True)), [False])
        self.watches(1, 1)
        single_weight.import_logs(self.user, self.item, rows, source="trakt")
        self.assertEqual(
            list(DiaryEntry.objects.order_by("consumed_at").values_list("is_rewatch", flat=True)),
            [False, True],
            "A later play in the same history is still a rewatch",
        )
        self.watches(2, 2)

    def test_the_log_row_carries_the_amount_and_its_note_counts_watches(self):
        self.eye()
        self.log(rewatch=True)
        rows = list_rows(rows_for([self.user], self.user, types=[self.media_type]).rows)
        self.assertEqual([(int(row.amount), row.log_id is not None) for row in rows], [(self.minutes, True)])
        response = self.client.get("/api/v1/stats/me/summary/", {"schema": "2", "media_type": self.media_type, **ALL_TIME})
        row = response.json()["time"]["rows"][0]
        self.assertEqual((row["known"], row["total"]), (2, 2))

    def test_no_runtime_is_a_watch_with_no_length(self):
        Item.objects.filter(pk=self.item.pk).update(runtime_minutes=0)
        self.eye()
        self.log(rewatch=True)
        response = self.client.get("/api/v1/stats/me/summary/", {"schema": "2", "media_type": self.media_type, **ALL_TIME})
        row = response.json()["time"]["rows"][0]
        self.assertEqual((row["value"], row["known"], row["total"]), (0, 0, 2))


class AlbumTests(FilmTests):
    """Albums follow films (SW-010, SW-503)."""

    media_type = MUSIC
    minutes = 60


@override_settings(TIME_ZONE=NEW_YORK)
class FilmNewYearTests(FilmCalcBase):
    """A film's hours land in the year its Logs count does, read from Tokyo."""

    def test_the_hours_year_is_the_logs_year(self):
        DiaryEntry.objects.create(
            user=self.user,
            item=self.item,
            consumed_at=datetime(2025, 12, 31, 23, 30, tzinfo=ZoneInfo(NEW_YORK)),
        )
        DiaryEntry.objects.create(user=self.user, item=self.item, consumed_at=datetime(2026, 1, 1, tzinfo=ZoneInfo("UTC")))
        self.client.credentials(HTTP_X_SPINE_TIMEZONE="Asia/Tokyo")
        for year in (2025, 2026):
            response = self.client.get(
                "/api/v1/stats/me/summary/",
                {"schema": "2", "media_type": self.media_type, "start_date": f"{year}-01-01", "end_date": f"{year}-12-31"},
            )
            data = response.json()
            minutes = sum(row["value"] for row in data["time"]["rows"])
            with self.subTest(year=year):
                self.assertEqual(minutes, data["header"]["logs"] * self.minutes)
