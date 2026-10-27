"""Episode rows are viewings (docs/tv-tracking-contract.md, TV-100 to TV-102, TV-206).

Saving or deleting an Episode works nothing out by itself; the model hook in
app.tv_signals works the show out on commit from the stored episode lists, so these
tests run the writes inside captureOnCommitCallbacks(execute=True).

Rewritten for the TV tracking contract: the old tests pinned the model cascades
(the finale completing a season, the next season auto-starting, completion toasts),
which TV-200, TV-202, TV-205 and TV-207 replace. Each test names its law.
"""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from app import tv_listing, tv_tracking
from app.models import (
    TV,
    Episode,
    Item,
    MediaTypes,
    Season,
    Sources,
    Status,
    UserMessage,
)
from app.tests.tv_fixtures import FakeTVProvider


class EpisodeHookTestCase(TestCase):
    """A fake TMDB show and a user whose show rows are created through the models."""

    media_id = "123"

    def setUp(self):
        super().setUp()
        self.fake = FakeTVProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        patcher = patch("app.models.Item.fetch_releases")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.provider = self.fake.add_show(self.media_id, title="Test Show", seasons={})
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)
        self.tv_item = Item.objects.create(
            media_id=self.media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test Show",
            image="http://example.com/image.jpg",
        )
        self.tv = TV.objects.create(item=self.tv_item, user=self.user, status=Status.PLANNING.value)
        self.season = Season.objects.create(
            item=self.season_item(1),
            user=self.user,
            related_tv=self.tv,
            status=Status.PLANNING.value,
        )

    def listing(self, seasons, **show):
        for number, spec in seasons.items():
            self.provider.set_season(number, spec)
        for name, value in show.items():
            setattr(self.provider, name, value)
        tv_listing.get_listing(self.tv_item, max_age=timedelta(0))

    def season_item(self, number):
        item, _ = Item.objects.get_or_create(
            media_id=self.media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=number,
            defaults={"title": "Test Show", "image": "http://example.com/image.jpg"},
        )
        return item

    def episode_item(self, season_number, episode_number):
        item, _ = Item.objects.get_or_create(
            media_id=self.media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=season_number,
            episode_number=episode_number,
            defaults={"title": "Test Episode", "image": "http://example.com/image.jpg"},
        )
        return item

    def watch(self, episode_number, end_date=None, season=None):
        with self.captureOnCommitCallbacks(execute=True):
            return Episode.objects.create(
                item=self.episode_item((season or self.season).item.season_number, episode_number),
                related_season=season or self.season,
                end_date=end_date or timezone.now(),
            )

    def refresh(self):
        self.season.refresh_from_db()
        self.tv.refresh_from_db()


class EpisodeModel(EpisodeHookTestCase):
    """Saving viewings through the model."""

    media_id = "1668"

    def test_episode_save(self):
        """TV-200: watching every listed episode finishes the season (through the hook)."""
        self.listing({1: [date(2023, 6, number) for number in range(1, 25)]})
        for number in range(1, 25):
            self.watch(number, datetime(2023, 6, number, 0, 0, tzinfo=UTC))
        self.refresh()
        self.assertEqual(self.season.status, Status.COMPLETED.value)
        self.assertEqual(self.tv.status, Status.COMPLETED.value)


class EpisodeStatusTests(EpisodeHookTestCase):
    """How viewings saved through the model work out the season and show."""

    def test_first_episode_sets_season_in_progress(self):
        """TV-100: a viewing starts the season and show; they are Watching."""
        self.listing({1: [date(2020, 1, 1), date(2020, 1, 8)]})
        viewing = self.watch(1)
        self.refresh()
        self.assertEqual(self.season.status, Status.IN_PROGRESS.value)
        self.assertEqual(self.tv.status, Status.IN_PROGRESS.value)
        viewing.refresh_from_db()
        self.assertEqual(viewing.kind, Episode.Kind.TICK)
        self.assertIsNotNone(viewing.watched_on)

    def test_last_episode_sets_season_completed(self):
        """TV-200: the season's only episode watched finishes it, and the show."""
        self.listing({1: [date(2020, 1, 1)]})
        self.watch(1)
        self.refresh()
        self.assertEqual(self.season.status, Status.COMPLETED.value)
        self.assertEqual(self.tv.status, Status.COMPLETED.value)

    def test_only_the_finale_never_finishes_a_season(self):
        """TV-200 (A-11): watching only the finale leaves the season in progress."""
        self.listing({1: [date(2020, 1, 1), date(2020, 1, 8), date(2020, 1, 15)]})
        self.watch(3)
        self.refresh()
        self.assertEqual(self.season.status, Status.IN_PROGRESS.value)
        self.assertEqual((self.season.watched_count, self.season.aired_count), (1, 3))

    @patch("app.models.providers.services.get_media_metadata")
    def test_middle_episode_does_not_change_status(self, mock_get_metadata):
        """Test middle episode doesn't change season/TV status."""
        mock_metadata = {
            "season/1": {
                "episodes": [
                    {"episode_number": 1},
                    {"episode_number": 2},
                    {"episode_number": 3},
                ],
            },
            "related": {
                "seasons": [{"season_number": 1}, {"season_number": 2}],
            },
        }
        mock_get_metadata.return_value = mock_metadata

        Episode.objects.create(
            item=self.episode_item(1, 1),
            related_season=self.season,
            end_date=timezone.now(),
        )

        with patch("app.models.bulk_update_with_history") as mock_bulk_update:
            Episode.objects.create(
                item=self.episode_item(1, 2),
                related_season=self.season,
                end_date=timezone.now(),
            )

            mock_bulk_update.assert_not_called()

    def test_last_season_completes_tv_show(self):
        """TV-201: the last season finished finishes the show; no automatic toast."""
        self.listing({1: [date(2020, 1, 1)]})
        self.watch(1)
        self.refresh()
        self.assertEqual(self.tv.status, Status.COMPLETED.value)
        self.assertFalse(UserMessage.objects.filter(user=self.user).exists())

    def test_non_last_season_starts_next_season(self):
        """TV-205: finishing Season 1 creates and starts nothing for Season 2.

        Up next moves to S2 E1 and the show stays Watching; an existing empty Season 2
        row stays as stored and counts as not started.
        """
        next_season = Season.objects.create(
            item=self.season_item(2),
            user=self.user,
            related_tv=self.tv,
            status=Status.PLANNING.value,
        )
        self.listing({1: [date(2020, 1, 1)], 2: [date(2021, 1, 1)]})
        self.watch(1)
        next_season.refresh_from_db()
        self.refresh()
        self.assertEqual(next_season.status, Status.PLANNING.value)
        self.assertFalse(next_season.is_started)
        self.assertEqual(self.tv.status, Status.IN_PROGRESS.value)
        self.assertEqual((self.tv.up_next_season, self.tv.up_next_episode), (2, 1))
        self.assertFalse(UserMessage.objects.filter(user=self.user).exists())

    def test_non_last_season_with_future_next_season_keeps_planning(self):
        """TV-202: with Season 2 not aired yet, the show is Completed and caught up.

        The empty Season 2 row stays as stored (TV-205).
        """
        next_season = Season.objects.create(
            item=self.season_item(2),
            user=self.user,
            related_tv=self.tv,
            status=Status.PLANNING.value,
        )
        future = timezone.localdate() + timedelta(days=60)
        self.listing({1: [date(2020, 1, 1)], 2: [future]})
        self.watch(1)
        next_season.refresh_from_db()
        self.refresh()
        self.assertEqual(next_season.status, Status.PLANNING.value)
        self.assertEqual(self.tv.status, Status.COMPLETED.value)
        self.assertTrue(self.tv.caught_up)
        self.assertEqual((self.tv.next_air_season, self.tv.next_air_date), (2, future))

    def test_rewatch_last_episode_does_not_duplicate_completion_messages(self):
        """TV-101: a second viewing saved directly is kept and counted once; no toasts."""
        self.listing({1: [date(2020, 1, 1)]})
        first_watch_time = timezone.now()
        self.watch(1, first_watch_time)
        self.watch(1, first_watch_time + timedelta(days=1))
        self.refresh()
        self.assertEqual(Episode.objects.filter(related_season=self.season).count(), 2)
        self.assertEqual(self.season.watched_count, 1)
        self.assertEqual(self.season.status, Status.COMPLETED.value)
        self.assertEqual(self.tv.status, Status.COMPLETED.value)
        self.assertFalse(UserMessage.objects.filter(user=self.user).exists())

    def test_deleting_a_viewing_reopens_the_season_and_show(self):
        """TV-102: removing the finale's viewing reopens a finished season and show."""
        self.listing({1: [date(2020, 1, 1), date(2020, 1, 8)]})
        self.watch(1)
        finale = self.watch(2)
        self.refresh()
        self.assertEqual(self.tv.status, Status.COMPLETED.value)
        with self.captureOnCommitCallbacks(execute=True):
            finale.delete()
        self.refresh()
        self.assertEqual(self.season.status, Status.IN_PROGRESS.value)
        self.assertEqual(self.tv.status, Status.IN_PROGRESS.value)

    def test_a_newly_aired_episode_reopens_a_finished_show(self):
        """TV-206: an aired episode added to a finished season reopens it."""
        self.listing({1: [date(2020, 1, 1)]})
        self.watch(1, timezone.now() - timedelta(days=10))
        self.refresh()
        self.assertEqual(self.tv.status, Status.COMPLETED.value)
        self.provider.add_episode(1, 2, timezone.localdate() - timedelta(days=1))
        tv_tracking.refresh_after_calendar(self.tv_item)
        self.refresh()
        self.assertEqual(self.season.status, Status.IN_PROGRESS.value)
        self.assertEqual(self.tv.status, Status.IN_PROGRESS.value)
        self.assertEqual((self.tv.up_next_season, self.tv.up_next_episode), (1, 2))
        self.assertTrue(self.tv.up_next_is_new)
