from datetime import UTC, date, datetime, timedelta
from pathlib import Path
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

mock_path = Path(__file__).resolve().parent.parent / "mock_data"


class TVModel(TestCase):
    """Test the @properties and custom save of the TV model."""

    def setUp(self):
        """Create a user and a season with episodes."""
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        item_tv = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Friends",
            image="http://example.com/image.jpg",
        )
        self.tv = TV.objects.create(
            item=item_tv,
            user=self.user,
            status=Status.PLANNING.value,
        )

        item_season1 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
        )

        season1 = Season.objects.create(
            item=item_season1,
            user=self.user,
            related_tv=self.tv,
            status=Status.IN_PROGRESS.value,
        )

        item_ep1 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        item_ep2 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=2,
        )

        item_season2 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=2,
        )

        season2 = Season.objects.create(
            item=item_season2,
            related_tv=self.tv,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        item_ep3 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=2,
            episode_number=1,
        )

        item_ep4 = Item.objects.create(
            media_id="1668",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="Friends",
            image="http://example.com/image.jpg",
            season_number=2,
            episode_number=2,
        )
        Episode.objects.bulk_create(
            [
                Episode(
                    item=item_ep1,
                    related_season=season1,
                    end_date=datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
                ),
                Episode(
                    item=item_ep2,
                    related_season=season1,
                    end_date=datetime(2023, 6, 2, 0, 0, tzinfo=UTC),
                ),
                Episode(
                    item=item_ep3,
                    related_season=season2,
                    end_date=datetime(2023, 6, 4, 0, 0, tzinfo=UTC),
                ),
                Episode(
                    item=item_ep4,
                    related_season=season2,
                    end_date=datetime(2023, 6, 5, 0, 0, tzinfo=UTC),
                ),
            ],
        )

    def test_tv_progress(self):
        """Test the progress property of the Season model."""
        self.assertEqual(self.tv.progress, 4)

    def test_tv_start_date(self):
        """Test the start_date property of the Season model."""
        self.assertEqual(
            self.tv.start_date,
            datetime(2023, 6, 1, 0, 0, tzinfo=UTC),
        )

    def test_tv_end_date(self):
        """Test the end_date property of the Season model."""
        self.assertEqual(
            self.tv.end_date,
            datetime(2023, 6, 5, 0, 0, tzinfo=UTC),
        )

    def test_tv_save(self):
        """TV-207: a show saved as Completed means mark the show watched (model hook).

        The hook marks every aired regular episode watched, so both seasons finish;
        the show's two ticked episodes per season keep their viewings.
        """
        with FakeTVProvider() as fake:
            fake.add_show(
                "1668",
                title="Friends",
                seasons={
                    1: [date(2023, 6, 1), date(2023, 6, 2), date(2023, 6, 3)],
                    2: [date(2023, 6, 4), date(2023, 6, 5)],
                },
            )
            tv_listing.get_listing(self.tv.item)
            self.tv.status = Status.COMPLETED.value
            with patch("app.models.Item.fetch_releases"), self.captureOnCommitCallbacks(execute=True):
                self.tv.save(update_fields=["status"])

        self.assertEqual(
            self.tv.seasons.filter(status=Status.COMPLETED.value).count(),
            2,
        )
        self.tv.refresh_from_db()
        self.assertEqual(self.tv.status, Status.COMPLETED.value)
        fill = Episode.objects.get(item__season_number=1, item__episode_number=3)
        self.assertEqual((fill.kind, fill.end_date), (Episode.Kind.FILL, None))


class TVStatusTests(TestCase):
    """A show status written through the model means its action (TV-207).

    Rewritten for the TV tracking contract: the old tests pinned the TV.save cascades
    (Completed creating every season with dated episodes, Dropped dropping seasons,
    In progress starting an empty next season), which TV-207, TV-205, TV-204 and
    TV-301 replace. The model hook in app.tv_signals applies the action on commit.
    """

    def setUp(self):
        """Create test data."""
        self.fake = FakeTVProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        patcher = patch("app.models.Item.fetch_releases")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.provider = self.fake.add_show("123", title="Test Show", seasons={})
        self.credentials = {"username": "test", "password": "12345"}
        self.user = get_user_model().objects.create_user(**self.credentials)

        self.tv_item = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test Show",
            image="http://example.com/image.jpg",
        )

        self.tv = TV.objects.create(
            item=self.tv_item,
            user=self.user,
            status=Status.PLANNING.value,
        )

        self.season1_item = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test Show",
            image="http://example.com/image.jpg",
            season_number=1,
        )

        self.season1 = Season.objects.create(
            item=self.season1_item,
            user=self.user,
            related_tv=self.tv,
            status=Status.IN_PROGRESS.value,
        )

        self.season2_item = Item.objects.create(
            media_id="123",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test Show",
            image="http://example.com/image.jpg",
            season_number=2,
        )

        self.season2 = Season.objects.create(
            item=self.season2_item,
            user=self.user,
            related_tv=self.tv,
            status=Status.PLANNING.value,
        )

    def listing(self, seasons):
        for number, spec in seasons.items():
            self.provider.set_season(number, spec)
        tv_listing.get_listing(self.tv_item, max_age=timedelta(0))

    def write_status(self, status):
        self.tv.status = status
        with self.captureOnCommitCallbacks(execute=True):
            self.tv.save()
        self.tv.refresh_from_db()

    def test_completed_status_creates_all_seasons(self):
        """TV-207, TV-300: Completed marks every aired regular episode watched, undated."""
        self.listing({number: [date(2020, 1, 1) + timedelta(weeks=week) for week in range(10)] for number in (1, 2, 3)})

        self.write_status(Status.COMPLETED.value)

        self.assertEqual(self.tv.seasons.count(), 3)
        self.assertEqual(
            self.tv.seasons.filter(status=Status.COMPLETED.value).count(),
            3,
        )
        for season in self.tv.seasons.all():
            self.assertEqual(season.episodes.count(), 10)
            self.assertFalse(season.episodes.exclude(kind=Episode.Kind.FILL).exists())
            self.assertFalse(season.episodes.filter(end_date__isnull=False).exists())
        self.assertEqual(self.tv.status, Status.COMPLETED.value)

    def test_completed_status_skips_unaired_episodes_and_future_seasons(self):
        """TV-207, TV-103: Completed marks only aired episodes; the show is caught up.

        Season 2 still airing keeps the show Watching and caught up (TV-202); Season 3,
        with no air date and nothing later aired, gets no tracking (TV-205), and no
        automatic messages are written.
        """
        future = timezone.localdate() + timedelta(days=400)
        self.listing(
            {
                1: [date(2020, 1, 1)],
                2: [date(2020, 1, 1), future],
                3: [None],
            },
        )

        self.write_status(Status.COMPLETED.value)

        season1 = self.tv.seasons.get(item__season_number=1)
        season2 = self.tv.seasons.get(item__season_number=2)
        self.assertEqual(self.tv.status, Status.IN_PROGRESS.value)
        self.assertTrue(self.tv.caught_up)
        self.assertEqual(season1.status, Status.COMPLETED.value)
        self.assertEqual(season2.status, Status.IN_PROGRESS.value)
        self.assertEqual(season2.episodes.count(), 1)
        self.assertFalse(self.tv.seasons.filter(item__season_number=3).exists())
        self.assertFalse(UserMessage.objects.filter(user=self.user).exists())

    def test_dropped_status_marks_in_progress_seasons_dropped(self):
        """TV-207, TV-204: Dropped is the show's choice; only started seasons show it.

        Pausing or dropping never changes a viewing, and seasons that hold nothing
        keep their stored values (TV-205).
        """
        self.listing({1: 3, 2: 3, 3: 3})
        tv_tracking.tick(self.user, self.tv_item, 1, 1)
        self.tv.refresh_from_db()

        self.write_status(Status.DROPPED.value)

        self.assertEqual((self.tv.status, self.tv.choice), ("Dropped", "Dropped"))
        statuses = dict(self.tv.seasons.values_list("item__season_number", "status"))
        self.assertEqual(statuses, {1: Status.DROPPED.value, 2: Status.PLANNING.value})
        self.assertEqual(Episode.objects.count(), 1)

    def test_in_progress_status_activates_next_season(self):
        """TV-207, TV-205: In progress means choose Watching; no empty season starts."""
        self.listing({1: [date(2020, 1, 1)], 2: [date(2020, 1, 1)]})
        tv_tracking.tick(self.user, self.tv_item, 1, 1)
        tv_tracking.choose(self.user, self.tv_item, "Paused")
        self.tv.refresh_from_db()

        self.write_status(Status.IN_PROGRESS.value)

        season2 = Season.objects.get(pk=self.season2.pk)
        self.assertEqual(season2.status, Status.PLANNING.value)
        self.assertFalse(season2.is_started)
        self.assertEqual(self.tv.status, Status.IN_PROGRESS.value)
        self.assertEqual((self.tv.up_next_season, self.tv.up_next_episode), (2, 1))

    def test_in_progress_status_creates_new_season_if_needed(self):
        """TV-205: choosing Watching creates no season tracking."""
        self.listing({1: [date(2020, 1, 1)], 2: [date(2020, 1, 1)], 3: [date(2020, 1, 1)]})

        self.write_status(Status.IN_PROGRESS.value)

        self.assertFalse(self.tv.seasons.filter(item__season_number=3).exists())
        self.assertEqual((self.tv.status, self.tv.choice), ("In progress", "In progress"))
        self.assertEqual((self.tv.up_next_season, self.tv.up_next_episode), (1, 1))

    def test_in_progress_status_noop_if_already_has_in_progress_season(self):
        """Test IN_PROGRESS status change does nothing if season already in progress."""
        original_season1_status = self.season1.status

        self.tv.status = Status.IN_PROGRESS.value
        self.tv.save()

        season1 = Season.objects.get(pk=self.season1.pk)
        self.assertEqual(season1.status, original_season1_status)
