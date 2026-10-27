"""TV Items get dates and lengths without extra provider calls (stats plan 1.2, 1.3).

The stored listing keeps each episode's length as a 5th row field, and old 4-field
rows still load. Season and episode Items a tick or the API creates take their dates
and lengths from the listing; genres stay on the show. Paths holding the show's or a
season's full record store it. No TV rule reads the length.
"""

from datetime import date, datetime
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from api.services import tracking
from api.services.item_metadata import CURRENT_METADATA_VERSION
from app import tv_listing, tv_tracking
from app.models import Item, ItemFilterFacet, MediaTypes, Sources, TVListing
from app.tests.tv_fixtures import FakeEpisode, FakeTVProvider, make_show, make_user, patch_now
from events.calendar.tv import process_tv_seasons

S1E1 = date(2020, 1, 6)
S1E2 = date(2020, 1, 13)
S1E3 = date(2020, 1, 20)


@override_settings(TIME_ZONE="America/New_York")
class TVItemMetadataTestCase(TestCase):
    """A fake TMDB with one show whose season 1 episodes have different lengths."""

    def setUp(self):
        super().setUp()
        self.fake = FakeTVProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        self.clock = patch_now(datetime(2026, 10, 25, 21, 0), tz="America/Los_Angeles").__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        for target in ("app.models.Item.fetch_releases", "app.tv_tracking._queue_external_ratings"):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.user = make_user(calendar="America/Los_Angeles")
        self.show = make_show(
            self.fake,
            seasons={
                1: [
                    FakeEpisode(1, S1E1, "Pilot", runtime=50),
                    FakeEpisode(2, S1E2, "Two", runtime=40),
                    FakeEpisode(3, S1E3, "Three", runtime=None),
                ],
            },
        )


class ListingRuntimeTests(TVItemMetadataTestCase):
    """The listing's 5th field: each episode's length in minutes."""

    def test_rows_store_each_episodes_length(self):
        listing = tv_listing.get_listing(self.show)

        rows = TVListing.objects.get(item=self.show).seasons["1"]["episodes"]
        self.assertEqual([row[4] for row in rows], [50, 40, None])
        self.assertEqual([episode.runtime for episode in listing.episodes[1]], [50, 40, None])
        self.assertEqual(listing.season_runtime(1), 45)

    def test_text_lengths_from_processed_episodes_are_read_as_minutes(self):
        tv_listing.update_from_metadata(
            self.show,
            {"episodes": [{"episode_number": 1, "air_date": "2020-01-06", "title": "Pilot", "runtime": "1h 5m"}]},
            season_number=1,
        )

        row = TVListing.objects.get(item=self.show).seasons["1"]["episodes"][0]
        self.assertEqual(row[4], 65)

    def test_old_four_and_three_field_rows_still_load(self):
        TVListing.objects.create(
            item=self.show,
            season_numbers=[1],
            seasons={
                "1": {
                    "air_date": "2020-01-06",
                    "fetched_at": timezone.now().isoformat(),
                    "episodes": [[1, "2020-01-06", "Pilot", "https://example.com/1.jpg"], [2, "2020-01-13", "Two"]],
                },
            },
            next_episode_to_air=None,
            returning=False,
            fetched_at=timezone.now(),
        )

        listing = tv_listing.stored_listing(self.show)

        first, second = listing.episodes[1]
        self.assertEqual((first.image, first.runtime), ("https://example.com/1.jpg", None))
        self.assertEqual((second.image, second.runtime), (None, None))
        self.assertIsNone(listing.season_runtime(1))
        self.assertEqual(listing.item_fields(MediaTypes.EPISODE.value, 1, 1), {
            "release_date": S1E1,
            "release_year": 2020,
        })


class TickCreatedItemTests(TVItemMetadataTestCase):
    """Items a write creates take dates and lengths from the listing, not genres."""

    def test_a_tick_creates_dated_episode_and_season_items_with_lengths(self):
        ItemFilterFacet.objects.create(item=self.show, facet_type=ItemFilterFacet.FacetType.GENRE, value="Drama")

        tv_tracking.tick(self.user, self.show, 1, 2)

        family = Item.objects.filter(media_id=self.show.media_id, source=self.show.source)
        episode = family.get(media_type=MediaTypes.EPISODE.value, episode_number=2)
        season = family.get(media_type=MediaTypes.SEASON.value, season_number=1)
        self.assertEqual((episode.release_date, episode.release_year, episode.runtime_minutes), (S1E2, 2020, 40))
        self.assertEqual((season.release_date, season.runtime_minutes), (S1E1, 45))
        self.assertFalse(ItemFilterFacet.objects.filter(item__in=[episode, season]).exists())
        # The listing's copy isn't the provider's full record, so nothing is stamped.
        self.assertEqual((episode.metadata_version, season.metadata_version), (0, 0))

    def test_an_episode_without_a_length_gets_its_date_only(self):
        tv_tracking.tick(self.user, self.show, 1, 3)

        episode = Item.objects.get(media_type=MediaTypes.EPISODE.value, media_id=self.show.media_id)
        self.assertEqual((episode.release_date, episode.runtime_minutes), (S1E3, None))

    def test_the_api_creates_season_and_episode_items_from_the_stored_listing(self):
        tv_listing.get_listing(self.show)

        episode = tracking.tv_family_item(self.show, MediaTypes.EPISODE.value, 1, 1)
        season = tracking.tv_family_item(self.show, MediaTypes.SEASON.value, 1)

        self.assertEqual((episode.title, episode.release_date, episode.runtime_minutes), ("Pilot", S1E1, 50))
        self.assertEqual((season.release_date, season.runtime_minutes), (S1E1, 45))


class TVRecordStoreTests(TVItemMetadataTestCase):
    """Paths that fetched the show's or a season's full record store it."""

    def test_show_item_for_stores_the_show_record(self):
        self.show.delete()
        season = Item.objects.create(
            media_id=self.show.media_id,
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title="Test Show",
            image="",
        )

        show = tv_listing.show_item_for(season)

        show.refresh_from_db()
        self.assertEqual(show.release_year, 2020)
        self.assertEqual(show.metadata_version, CURRENT_METADATA_VERSION[MediaTypes.TV.value])

    def test_the_calendar_stores_each_seasons_record(self):
        process_tv_seasons(self.show, [1], [])

        season = Item.objects.get(media_type=MediaTypes.SEASON.value, media_id=self.show.media_id)
        self.assertEqual((season.release_year, season.runtime_minutes), (2020, 45))
        self.assertEqual(season.metadata_version, CURRENT_METADATA_VERSION[MediaTypes.SEASON.value])
