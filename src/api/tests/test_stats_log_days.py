"""A log's day in stats and diary ranges.

A UTC-midnight carrier keeps its date; a moment is read on the owner's calendar.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from app.models import DiaryEntry, Item, MediaTypes, Sources

NEW_YORK = "America/New_York"
LOS_ANGELES = "America/Los_Angeles"
AUCKLAND = "Pacific/Auckland"
OWNER_ZONES = (NEW_YORK, LOS_ANGELES, AUCKLAND)
YEAR_2025 = {"start_date": "2025-01-01", "end_date": "2025-12-31"}
YEAR_2026 = {"start_date": "2026-01-01", "end_date": "2026-12-31"}
# A calendar day stored as a UTC-midnight carrier (TV, movie and MAL-export logs).
NEW_YEARS_DAY = datetime(2026, 1, 1, tzinfo=UTC)


@override_settings(TIME_ZONE=NEW_YORK)
class LogDayRangeTests(TestCase):
    """1 Jan carriers land in their year; a real 23:30 log on 31 Dec stays in it."""

    def setUp(self):
        users = get_user_model().objects
        self.owner = users.create_user(username="owner", password="strong-password-123")
        self.viewer = users.create_user(username="viewer", password="strong-password-123")
        self.show = self._item(MediaTypes.TV.value, Sources.TMDB.value, "1399")
        self.movie = self._item(MediaTypes.MOVIE.value, Sources.TMDB.value, "550")
        self.imported_anime = self._item(MediaTypes.ANIME.value, Sources.MAL.value, "1")
        self.anime = self._item(MediaTypes.ANIME.value, Sources.MAL.value, "5114")

    def test_stats_years_follow_the_owners_calendar(self):
        for zone in OWNER_ZONES:
            with self.subTest(owner_zone=zone):
                carriers, _late = self._seed(zone)
                client = self._client(self.owner, zone)

                in_2026 = client.get("/api/v1/stats/me/summary/", YEAR_2026).data
                in_2025 = client.get("/api/v1/stats/me/summary/", YEAR_2025).data

                self.assertEqual(in_2026["overview"]["diary_entry_count"], len(carriers))
                self.assertEqual(in_2026["activity"]["days"], [{"date": "2026-01-01", "count": 3}])
                self.assertEqual(self._tv_count(in_2026), 1)
                self.assertEqual(in_2025["overview"]["diary_entry_count"], 1)
                self.assertEqual(in_2025["activity"]["days"], [{"date": "2025-12-31", "count": 1}])
                self.assertEqual(self._tv_count(in_2025), 0)

    def test_diary_ranges_follow_the_owners_calendar(self):
        for zone in OWNER_ZONES:
            with self.subTest(owner_zone=zone):
                carriers, late = self._seed(zone)
                client = self._client(self.owner, zone)

                self.assertEqual(self._diary_ids(client, "2026-01-01", "2026-12-31"), {e.id for e in carriers})
                self.assertEqual(self._diary_ids(client, "2025-01-01", "2025-12-31"), {late.id})
                self.assertEqual(self._diary_ids(client, "2026-01-01", "2026-01-01"), {e.id for e in carriers})
                self.assertEqual(self._diary_ids(client, None, "2025-12-31"), {late.id})
                self.assertEqual(self._diary_ids(client, "2026-01-01", None), {e.id for e in carriers})

    def test_other_viewers_get_the_owners_stored_calendar(self):
        # Server New York, owner's stored calendar Los Angeles, viewer header Auckland.
        # 23:30 on 31 Dec in Los Angeles is already 1 Jan in New York and Auckland.
        carriers, _late = self._seed(LOS_ANGELES)
        client = self._client(self.viewer, AUCKLAND)

        in_2026 = client.get(f"/api/v1/users/{self.owner.username}/stats/summary/", YEAR_2026).data
        in_2025 = client.get(f"/api/v1/users/{self.owner.username}/stats/summary/", YEAR_2025).data

        self.assertEqual(in_2026["overview"]["diary_entry_count"], len(carriers))
        self.assertEqual(in_2026["activity"]["days"], [{"date": "2026-01-01", "count": 3}])
        self.assertEqual(in_2025["overview"]["diary_entry_count"], 1)
        self.assertEqual(in_2025["activity"]["days"], [{"date": "2025-12-31", "count": 1}])
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.calendar_timezone, LOS_ANGELES)

    def test_the_owners_own_header_is_their_newest_calendar(self):
        # Server New York, stored Los Angeles, the owner's request sends Auckland: the
        # Los Angeles 23:30 on 31 Dec is 1 Jan on that calendar.
        carriers, late = self._seed(LOS_ANGELES)
        auckland_new_year = DiaryEntry.objects.create(
            user=self.owner,
            item=self.anime,
            consumed_at=datetime(2026, 1, 1, 0, 30, tzinfo=ZoneInfo(AUCKLAND)),
        )
        client = self._client(self.owner, AUCKLAND)

        self.assertEqual(
            self._diary_ids(client, "2026-01-01", "2026-12-31"),
            {e.id for e in carriers} | {late.id, auckland_new_year.id},
        )
        self.assertEqual(self._diary_ids(client, "2025-01-01", "2025-12-31"), set())
        stats = client.get("/api/v1/stats/me/summary/", YEAR_2026).data
        self.assertEqual(stats["overview"]["diary_entry_count"], len(carriers) + 2)

    def _seed(self, owner_zone):
        DiaryEntry.objects.all().delete()
        self.owner.calendar_timezone = owner_zone
        self.owner.save(update_fields=["calendar_timezone"])
        carriers = [
            DiaryEntry.objects.create(user=self.owner, item=self.show, consumed_at=NEW_YEARS_DAY),
            DiaryEntry.objects.create(user=self.owner, item=self.movie, consumed_at=NEW_YEARS_DAY),
            DiaryEntry.objects.create(
                user=self.owner,
                item=self.imported_anime,
                consumed_at=NEW_YEARS_DAY,
                import_source="mal_export",
                import_source_id="1",
            ),
        ]
        late = DiaryEntry.objects.create(
            user=self.owner,
            item=self.anime,
            consumed_at=datetime(2025, 12, 31, 23, 30, tzinfo=ZoneInfo(owner_zone)),
        )
        return carriers, late

    @staticmethod
    def _client(user, zone):
        client = APIClient()
        client.force_authenticate(user)
        client.credentials(HTTP_X_SPINE_TIMEZONE=zone)
        return client

    def _diary_ids(self, client, watched_from, watched_to):
        params = {"page_size": 50}
        if watched_from:
            params["watched_from"] = watched_from
        if watched_to:
            params["watched_to"] = watched_to
        response = client.get("/api/v1/diary/", params)
        self.assertEqual(response.status_code, 200, response.data)
        return {entry["id"] for entry in response.data["results"]}

    @staticmethod
    def _tv_count(payload):
        return next(
            entry["diary_entry_count"]
            for entry in payload["media_types"]
            if entry["media_type"] == MediaTypes.TV.value
        )

    @staticmethod
    def _item(media_type, source, media_id):
        return Item.objects.create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            title=f"{media_type} {media_id}",
            image=settings.IMG_NONE,
        )
