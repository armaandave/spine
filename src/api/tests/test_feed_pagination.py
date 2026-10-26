"""Activity pages in a total order, even through logs sharing a log date.

Logs of the same day (a backfill, or half-star media dated by calendar day) tie on
their date and can tie on when they were written. Postgres returns tied rows in any
order, so without a tiebreaker a cursor page can repeat or skip them.
"""

from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from app.models import DiaryEntry, Item, MediaTypes, Sources


class ActivityCursorTests(TestCase):
    """Tied logs page newest id first, with nothing repeated or skipped."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="feeder", password="strong-password-123")
        self.item = Item.objects.create(
            source=Sources.MUSICBRAINZ.value,
            media_type=MediaTypes.MUSIC.value,
            media_id="feed-pagination",
            title="Feed Pagination",
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _log(self, consumed_at):
        return DiaryEntry.objects.create(user=self.user, item=self.item, consumed_at=consumed_at).id

    def test_tied_log_dates_page_newest_id_first_without_gaps(self):
        day = datetime(2019, 5, 4, tzinfo=UTC)
        tied = [self._log(day) for _ in range(5)]
        DiaryEntry.objects.filter(id__in=tied).update(created_at=datetime(2026, 1, 1, tzinfo=UTC))
        newer = self._log(datetime(2026, 1, 1, tzinfo=UTC))
        older = self._log(datetime(2010, 1, 1, tzinfo=UTC))

        seen = []
        url = "/api/v1/users/feeder/activity/?page_size=2"
        while url:
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK)
            seen += [activity["id"] for activity in response.data["results"]]
            url = response.data["next_cursor"]

        self.assertEqual(seen, [newer, *sorted(tied, reverse=True), older])
