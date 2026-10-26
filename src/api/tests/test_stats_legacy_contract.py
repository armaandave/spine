"""Pins the legacy stats payload installed builds read: its keys and wire scales."""

from datetime import UTC, datetime
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from app.models import DiaryEntry, Item, MediaTypes, Sources

LEGACY_KEYS = {
    "start_date",
    "end_date",
    "media_count",
    "media_type_distribution",
    "score_distribution",
    "status_distribution",
    "top_rated",
    "schema_version",
    "range",
    "overview",
    "media_types",
    "activity",
    "rating_distribution",
    "diary_top_rated",
    "most_logged",
    "most_logged_total",
    "release_years",
    "top_genres",
    "top_languages",
    "metadata_coverage",
    "list_progress",
    "series_progress",
}


class LegacyStatsContractTests(TestCase):
    """The payload without a schema keeps its shape and each value's scale."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="legacy", password="strong-password-123")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        # Both stored out of 10: the movie at 9 (4.5 stars), the season at 5 (2.5).
        self.movie = self._item(MediaTypes.MOVIE.value, "550")
        self.season = self._item(MediaTypes.SEASON.value, "1399", season_number=2)
        DiaryEntry.objects.create(
            user=self.user,
            item=self.movie,
            consumed_at=datetime(2026, 1, 10, tzinfo=UTC),
            rating=Decimal("9.0"),
        )
        DiaryEntry.objects.create(
            user=self.user,
            item=self.season,
            consumed_at=datetime(2026, 1, 11, tzinfo=UTC),
            rating=Decimal("5.0"),
        )

    def test_legacy_keys_are_unchanged(self):
        payload = self._payload()

        self.assertEqual(set(payload), LEGACY_KEYS)
        self.assertEqual(payload["schema_version"], 1)

    def test_diary_top_rated_ranks_stored_values_with_the_legacy_wire_scale(self):
        top_rated = self._payload()["diary_top_rated"]

        # Ranked on stored values (9 beats 5); the wire keeps each type's own scale:
        # a movie in stars, a TV rating as stored.
        self.assertEqual(
            [(row["media"]["ref"]["media_type"], row["rating"]) for row in top_rated],
            [(MediaTypes.MOVIE.value, "4.5"), (MediaTypes.SEASON.value, "5.0")],
        )

    def test_overview_average_rating_is_in_stars(self):
        overview = self._payload()["overview"]

        # (9 + 5) / 2 out of 10 is 3.5 stars.
        self.assertEqual(overview["average_rating"], "3.5")
        self.assertEqual(overview["rated_count"], 2)

    def _payload(self):
        response = self.client.get(
            "/api/v1/stats/me/summary/",
            {"start_date": "2026-01-01", "end_date": "2026-01-31"},
        )
        self.assertEqual(response.status_code, 200)
        return response.data

    @staticmethod
    def _item(media_type, media_id, **fields):
        return Item.objects.create(
            media_id=media_id,
            source=Sources.TMDB.value,
            media_type=media_type,
            title=f"{media_type} {media_id}",
            image=settings.IMG_NONE,
            **fields,
        )
