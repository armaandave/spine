"""Your Tracking: your latest log's review and rewatch (``viewer_latest_log``)."""

from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase

from api.services.media import viewer_latest_log
from app.models import DiaryEntry, Item, MediaTypes, Sources


class ViewerLatestLogTests(TestCase):
    """The media page's own extras about your latest log."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="reader")
        self.item = Item.objects.create(
            source=Sources.TMDB.value, media_type=MediaTypes.MOVIE.value, media_id="1233413", title="Sinners"
        )

    def test_the_latest_log_gives_its_review_and_rewatch(self):
        DiaryEntry.objects.create(
            user=self.user, item=self.item, consumed_at=datetime(2025, 4, 19, tzinfo=UTC), review="First time."
        )
        DiaryEntry.objects.create(
            user=self.user,
            item=self.item,
            consumed_at=datetime(2026, 10, 31, tzinfo=UTC),
            review="  Even better the second time.  ",
            review_title="Again",
            contains_spoilers=True,
            is_rewatch=True,
        )

        self.assertEqual(
            viewer_latest_log(self.user, self.item.id),
            {
                "diary_review": "Even better the second time.",
                "diary_review_title": "Again",
                "diary_contains_spoilers": True,
                "diary_is_rewatch": True,
            },
        )

    def test_no_log_or_no_words_gives_nothing(self):
        empty = {
            "diary_review": None,
            "diary_review_title": None,
            "diary_contains_spoilers": False,
            "diary_is_rewatch": False,
        }
        self.assertEqual(viewer_latest_log(self.user, self.item.id), empty)

        DiaryEntry.objects.create(user=self.user, item=self.item, consumed_at=datetime(2026, 1, 1, tzinfo=UTC))
        self.assertEqual(viewer_latest_log(self.user, self.item.id), empty)
