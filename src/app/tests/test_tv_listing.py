"""The stored copy of each show's episode lists (TV-103, TV-106, A-47, A-62, A-63, A-64).

Date cases run with the server on America/New_York and the account calendar on
America/Los_Angeles, as production does (docs/tv-tracking-contract.md, acceptance).
"""

import math
from datetime import date, datetime, timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings

from app import tv_listing, user_calendar
from app.models import Item, MediaTypes, Sources, TVListing
from app.providers import tmdb
from app.tests.tv_fixtures import (
    FakeTVProvider,
    make_manual_show,
    make_show,
    make_user,
    patch_now,
)
from app.tv_listing import InvalidEpisode, TVDetailsUnavailable

# 9 p.m. on Oct 25 in Los Angeles is midnight on Oct 26 in New York.
PACIFIC_9PM = datetime(2026, 10, 25, 21, 0)
OCT_24 = date(2026, 10, 24)
OCT_25 = date(2026, 10, 25)
OCT_26 = date(2026, 10, 26)
OCT_27 = date(2026, 10, 27)
OCT_28 = date(2026, 10, 28)


@override_settings(TIME_ZONE="America/New_York")
class TV103AiredTests(TestCase):
    """TV-103: which listed episodes count as aired, on the user's calendar."""

    def setUp(self):
        self.fake = FakeTVProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        self.clock = patch_now(PACIFIC_9PM, tz="America/Los_Angeles").__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        self.pacific = make_user(calendar="America/Los_Angeles")
        self.server = make_user(calendar="")

    def listing(self, **show):
        return tv_listing.get_listing(make_show(self.fake, **show))

    def test_tv_103_air_date_today_on_the_users_calendar_counts(self):
        listing = self.listing(seasons={1: [OCT_24, OCT_25, OCT_26]})
        pacific_today = user_calendar.today(self.pacific)
        server_today = user_calendar.today(self.server)
        self.assertEqual((pacific_today, server_today), (OCT_25, OCT_26))

        self.assertTrue(listing.is_aired(1, 1, today=pacific_today))
        self.assertTrue(listing.is_aired(1, 2, today=pacific_today))
        self.assertFalse(listing.is_aired(1, 3, today=pacific_today))
        # The server's own calendar is already on the 26th.
        self.assertTrue(listing.is_aired(1, 3, today=server_today))

    def test_tv_103_a_viewing_makes_an_episode_aired(self):
        listing = self.listing(seasons={1: [OCT_24, OCT_28]})
        self.assertFalse(listing.is_aired(1, 2, today=OCT_25))
        self.assertTrue(listing.is_aired(1, 2, today=OCT_25, has_viewing=True))

    def test_tv_103_undated_episode_counts_when_a_later_regular_episode_aired(self):
        listing = self.listing(
            seasons={
                1: [OCT_24, None, OCT_25, None],
                2: [None, None],
                3: [OCT_28],
            },
        )
        self.assertTrue(listing.is_aired(1, 2, today=OCT_25))
        self.assertFalse(listing.is_aired(1, 4, today=OCT_25))
        self.assertFalse(listing.is_aired(2, 1, today=OCT_25))
        # A later season's aired episode makes the earlier undated ones aired.
        self.assertTrue(listing.is_aired(1, 4, today=OCT_28))
        self.assertTrue(listing.is_aired(2, 2, today=OCT_28))

    def test_tv_103_undated_special_counts_only_after_a_later_special(self):
        listing = self.listing(seasons={0: [None, OCT_24, None], 1: [OCT_24]})
        self.assertTrue(listing.is_aired(0, 1, today=OCT_25))
        self.assertFalse(listing.is_aired(0, 3, today=OCT_25))

    def test_tv_103_a_tmdb_show_with_no_dates_yet_has_aired_nothing(self):
        """TMDB gives air dates, so a show without any is still to come (TV-005, TV-300).

        Only a source without air dates counts every listed episode (A-63, manual).
        """
        listing = self.listing(seasons={1: [None, None, None]})
        self.assertTrue(listing.any_air_dates)
        self.assertFalse(listing.is_aired(1, 3, today=OCT_25))
        self.assertEqual(listing.premiere(today=OCT_25), (1, None))

    def test_tv_103_seasons_not_numbered_from_1_and_gaps_count_listed_episodes(self):
        listing = self.listing(seasons={2: [(1, OCT_24), (3, OCT_24), (7, OCT_24)]})
        self.assertEqual(listing.regular_seasons(), [2])
        self.assertEqual(
            [episode.episode_number for episode in listing.episodes[2]],
            [1, 3, 7],
        )
        self.assertFalse(listing.is_listed(2, 2))
        self.assertTrue(listing.is_listed(2, 7))

    def test_tv_103_unlisted_episodes_never_count_as_aired(self):
        listing = self.listing(seasons={1: [OCT_24]})
        self.assertFalse(listing.is_aired(1, 2, today=OCT_25, has_viewing=True))


@override_settings(TIME_ZONE="America/New_York")
class TV103StoredCopyTests(TestCase):
    """TV-103: the stored copy, provider failures and unlisted seasons."""

    def setUp(self):
        self.fake = FakeTVProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        self.clock = patch_now(datetime(2026, 10, 25, 12, 0))
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        self.show = make_show(self.fake, seasons={1: 10, 2: 8})

    def test_tv_103_keeps_a_copy_of_each_listed_season(self):
        tv_listing.get_listing(self.show)
        stored = TVListing.objects.get(item=self.show)
        self.assertEqual(stored.season_numbers, [1, 2])
        self.assertEqual(len(stored.seasons["1"]["episodes"]), 10)
        self.assertEqual(stored.seasons["1"]["episodes"][0][:3], [1, "2020-01-06", "Episode 1"])
        self.assertEqual(tv_listing.stored_listing(self.show).episodes.keys(), {1, 2})

    def test_tv_103_uses_the_stored_copy_when_the_provider_fails(self):
        tv_listing.get_listing(self.show)
        self.fake.fail()
        listing = tv_listing.get_listing(self.show, max_age=timedelta(0))
        self.assertEqual(len(listing.episodes[2]), 8)
        self.assertTrue(listing.is_aired(2, 8, today=OCT_25))

    def test_tv_103_unavailable_without_a_stored_copy(self):
        self.fake.fail()
        with self.assertRaises(TVDetailsUnavailable) as raised:
            tv_listing.get_listing(self.show)
        self.assertEqual(raised.exception.message, "TV details are unavailable right now")
        self.assertFalse(TVListing.objects.filter(item=self.show).exists())

    def test_tv_103_unavailable_when_a_needed_season_has_no_copy(self):
        tv_listing.get_listing(self.show, seasons=[1])
        self.fake.fail(kinds=("tv_with_seasons",))
        with self.assertRaises(TVDetailsUnavailable) as raised:
            tv_listing.get_listing(self.show, seasons=[2])
        self.assertEqual(raised.exception.season_numbers, (2,))
        # A season the copy has is still served.
        self.assertEqual(len(tv_listing.get_listing(self.show, seasons=[1]).episodes[1]), 10)

    def test_tv_103_writes_need_no_network_once_stored(self):
        tv_listing.get_listing(self.show)
        self.fake.reset_calls()
        tv_listing.get_listing(self.show)
        tv_listing.stored_listing(self.show)
        self.assertEqual(self.fake.count(), 0)

    def test_tv_103_asks_the_provider_only_for_listed_seasons(self):
        listing = tv_listing.get_listing(self.show, seasons=[1, 7])
        self.assertNotIn(7, listing.episodes)
        self.assertTrue(all(7 not in seasons for seasons in self.fake.season_requests()))

    def test_a62_a_season_the_provider_stops_listing_counts_toward_nothing(self):
        tv_listing.get_listing(self.show)
        self.fake.show(self.show.media_id).remove_season(2)
        listing = tv_listing.get_listing(self.show, max_age=timedelta(0))
        self.assertEqual(listing.season_numbers, (1,))
        self.assertFalse(listing.is_listed(2, 1))
        self.assertFalse(listing.is_aired(2, 1, today=OCT_25, has_viewing=True))
        self.assertEqual(TVListing.objects.get(item=self.show).season_numbers, [1])

    def test_stored_listing_never_calls_the_provider(self):
        self.assertIsNone(tv_listing.stored_listing(self.show))
        self.assertEqual(self.fake.count(), 0)

    def test_allow_fetch_false_reads_only_the_copy(self):
        with self.assertRaises(TVDetailsUnavailable):
            tv_listing.get_listing(self.show, allow_fetch=False)
        self.assertEqual(self.fake.count(), 0)


class TVListingFetchTests(TestCase):
    """A-47 chunking and section 2.2.2 staleness."""

    def setUp(self):
        self.fake = FakeTVProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        self.clock = patch_now(datetime(2026, 10, 25, 12, 0))
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)

    def test_a47_fetches_at_most_eight_seasons_per_request(self):
        show = make_show(self.fake, seasons={number: 23 for number in range(1, 31)})
        listing = tv_listing.get_listing(show)

        self.assertEqual(sum(len(listing.episodes[number]) for number in range(1, 31)), 690)
        requests = self.fake.season_requests()
        self.assertEqual(len(requests), math.ceil(30 / 8))
        self.assertTrue(all(len(seasons) <= 8 for seasons in requests))
        self.assertEqual(sorted(number for seasons in requests for number in seasons), list(range(1, 31)))
        self.assertEqual(self.fake.count("tv"), 1)

        self.fake.reset_calls()
        tv_listing.get_listing(show)
        self.assertEqual(self.fake.count(), 0)

    def test_a_season_still_airing_is_refreshed_after_a_day_a_settled_one_after_a_week(self):
        show = make_show(
            self.fake,
            seasons={1: 10, 2: [date(2026, 10, 1), date(2026, 11, 1)]},
        )
        tv_listing.get_listing(show)

        self.fake.reset_calls()
        self.clock.advance(hours=23)
        tv_listing.get_listing(show)
        self.assertEqual(self.fake.count(), 0)

        self.clock.advance(hours=2)
        tv_listing.get_listing(show)
        self.assertEqual(self.fake.count("tv"), 1)
        self.assertEqual(self.fake.season_requests(), [(2,)])

        self.fake.reset_calls()
        self.clock.advance(days=6, hours=1)
        tv_listing.get_listing(show)
        self.assertEqual(self.fake.season_requests(), [(1, 2)])

    def test_a_season_that_aired_in_the_last_two_weeks_is_active(self):
        show = make_show(self.fake, seasons={1: [date(2026, 10, 10), date(2026, 10, 17)]})
        tv_listing.get_listing(show)
        self.fake.reset_calls()
        self.clock.advance(hours=25)
        tv_listing.get_listing(show)
        self.assertEqual(self.fake.season_requests(), [(1,)])

    def test_max_age_zero_forces_a_fetch(self):
        show = make_show(self.fake, seasons={1: 10, 2: 10})
        tv_listing.get_listing(show)
        self.fake.reset_calls()

        self.fake.show(show.media_id).add_episode(2, 11, date(2026, 10, 20))
        listing = tv_listing.get_listing(show, max_age=timedelta(0))

        self.assertEqual(self.fake.count("tv"), 1)
        self.assertEqual(self.fake.season_requests(), [(1, 2)])
        self.assertTrue(listing.is_listed(2, 11))

    def test_a_forced_fetch_drops_tmdbs_response_cache_first(self):
        show = make_show(self.fake, seasons={1: 3})
        tv_listing.get_listing(show)
        show_key = f"tmdb_{tmdb.DETAIL_CACHE_VERSION}_tv_{show.media_id}"
        season_key = f"tmdb_season_{show.media_id}_1"
        cache.set_many({show_key: {"stale": True}, season_key: {"stale": True}})

        tv_listing.get_listing(show, max_age=timedelta(0))

        self.assertIsNone(cache.get(show_key))
        self.assertIsNone(cache.get(season_key))


class A63ManualShowTests(TestCase):
    """A-63: manual shows are built live from their Items, never stored or unavailable."""

    def setUp(self):
        self.fake = FakeTVProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)

    def test_a63_every_listed_manual_episode_counts_as_aired(self):
        show = make_manual_show(seasons={1: 3, 2: 2})
        self.fake.fail()

        listing = tv_listing.get_listing(show, max_age=timedelta(0))

        self.assertEqual(listing.season_numbers, (1, 2))
        self.assertEqual([episode.episode_number for episode in listing.episodes[1]], [1, 2, 3])
        self.assertFalse(listing.any_air_dates)
        self.assertTrue(listing.is_aired(2, 2, today=date(2000, 1, 1)))
        self.assertEqual(listing.episode_title(1, 2), "Episode 2")
        self.assertEqual(self.fake.count(), 0)
        self.assertFalse(TVListing.objects.exists())
        self.assertEqual(tv_listing.stored_listing(show).season_numbers, (1, 2))

    def test_a63_a_new_manual_episode_is_listed_at_once(self):
        show = make_manual_show(seasons={1: 1})
        tv_listing.get_listing(show)
        Item.objects.create(
            media_id=show.media_id,
            source=Sources.MANUAL.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=2,
            title="Second",
            image=show.image,
        )
        self.assertTrue(tv_listing.get_listing(show).is_listed(1, 2))
        tv_listing.update_from_metadata(show, {"related": {"seasons": []}})
        self.assertFalse(TVListing.objects.exists())


@override_settings(TIME_ZONE="America/New_York")
class TV202AiringTests(TestCase):
    """TV-202 and A-64: the next regular episode to air and the premiere."""

    def setUp(self):
        self.fake = FakeTVProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        self.clock = patch_now(datetime(2026, 10, 25, 12, 0))
        self.clock.__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)

    def test_next_airing_is_the_first_unaired_listed_regular_episode(self):
        show = make_show(
            self.fake,
            seasons={0: [OCT_28], 1: 3, 2: [OCT_24, OCT_28, None]},
            status="Returning Series",
        )
        listing = tv_listing.get_listing(show)
        self.assertTrue(listing.returning)
        self.assertEqual(listing.next_regular_airing(today=OCT_25), (2, 2, OCT_28))
        self.assertEqual(
            listing.next_regular_airing(today=OCT_25, viewed={(2, 2)}),
            (2, 3, None),
        )

    def test_a64_next_episode_named_by_the_provider_but_not_listed_yet(self):
        show = make_show(
            self.fake,
            seasons={1: [OCT_24, OCT_25], 2: [None]},
            next_episode_to_air={"season_number": 1, "episode_number": 3, "air_date": OCT_28},
        )
        listing = tv_listing.get_listing(show)
        self.assertEqual(listing.next_episode_to_air, (1, 3, OCT_28))
        self.assertFalse(listing.is_listed(1, 3))
        self.assertEqual(listing.next_regular_airing(today=OCT_25), (1, 3, OCT_28))

    def test_next_episode_to_air_from_the_older_next_episode_season_key(self):
        show = make_show(self.fake, seasons={1: 3})
        metadata = tmdb.tv(show.media_id)
        del metadata["next_episode_to_air"]
        metadata["next_episode_season"] = 2
        tv_listing.update_from_metadata(show, metadata)
        listing = tv_listing.stored_listing(show)
        self.assertEqual(listing.next_episode_to_air, (2, None, None))

    def test_caught_up_between_seasons_names_the_next_seasons_premiere(self):
        show = make_show(
            self.fake,
            seasons={1: 3, 2: []},
            season_air_dates={2: date(2027, 3, 12)},
            status="Returning Series",
        )
        listing = tv_listing.get_listing(show)
        self.assertEqual(listing.next_regular_airing(today=OCT_25), (2, None, date(2027, 3, 12)))

    def test_nothing_more_to_air(self):
        listing = tv_listing.get_listing(make_show(self.fake, seasons={1: 3}))
        self.assertIsNone(listing.next_regular_airing(today=OCT_25))
        self.assertFalse(listing.returning)

    def test_premiere_while_nothing_has_aired(self):
        show = make_show(self.fake, seasons={0: [OCT_24], 1: [date(2027, 3, 12), None]})
        listing = tv_listing.get_listing(show)
        self.assertEqual(listing.premiere(today=OCT_25), (1, date(2027, 3, 12)))
        self.assertIsNone(listing.premiere(today=date(2027, 3, 12)))

    def test_premiere_without_a_known_date(self):
        listing = tv_listing.get_listing(
            make_show(self.fake, seasons={1: []}, season_air_dates={1: None}),
        )
        self.assertEqual(listing.premiere(today=OCT_25), (1, None))


@override_settings(TIME_ZONE="America/New_York")
class TV106ValidateWriteTests(TestCase):
    """TV-106: writes naming unlisted or unaired episodes are rejected."""

    def setUp(self):
        self.fake = FakeTVProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)
        self.clock = patch_now(PACIFIC_9PM, tz="America/Los_Angeles").__enter__()
        self.addCleanup(self.clock.__exit__, None, None, None)
        self.user = make_user(calendar="America/Los_Angeles")
        self.today = user_calendar.today(self.user)
        self.show = make_show(
            self.fake,
            seasons={0: [OCT_24], 1: [OCT_24, OCT_26, OCT_27, OCT_28, None], 2: 3},
        )
        self.listing = tv_listing.get_listing(self.show, seasons=[0, 1])

    def validate(self, season_number, episode_number, kind="tick"):
        tv_listing.validate_write(
            self.listing,
            season_number,
            episode_number,
            today=self.today,
            kind=kind,
        )

    def test_tv_106_rejects_an_episode_the_provider_doesnt_list(self):
        with self.assertRaisesMessage(InvalidEpisode, "This episode isn't listed for Season 1."):
            self.validate(1, 6)
        with self.assertRaisesMessage(InvalidEpisode, "This episode isn't listed for Specials."):
            self.validate(0, 2, kind="episode_log")
        with self.assertRaisesMessage(InvalidEpisode, "This episode isn't listed for Season 9."):
            self.validate(9, 1, kind="fill")

    def test_tv_106_accepts_an_episode_airing_up_to_a_day_ahead(self):
        self.assertEqual(self.today, OCT_25)
        self.validate(1, 1)
        self.validate(1, 2)
        self.validate(1, 2, kind="episode_log")
        with self.assertRaisesMessage(InvalidEpisode, "This episode hasn't aired yet."):
            self.validate(1, 3)
        with self.assertRaisesMessage(InvalidEpisode, "This episode hasn't aired yet."):
            self.validate(1, 4, kind="episode_log")

    def test_tv_106_rejects_an_undated_episode_unless_it_counts_as_aired(self):
        with self.assertRaisesMessage(InvalidEpisode, "This episode hasn't aired yet."):
            self.validate(1, 5)

    def test_tv_106_fills_only_aired_episodes(self):
        self.validate(1, 1, kind="fill")
        with self.assertRaisesMessage(InvalidEpisode, "This episode hasn't aired yet."):
            self.validate(1, 2, kind="fill")

    def test_tv_103_a_listed_season_without_a_copy_is_unavailable(self):
        with self.assertRaises(TVDetailsUnavailable):
            self.validate(2, 1)


class ShowItemForTests(TestCase):
    """show_item_for finds or creates the show's tv Item."""

    def setUp(self):
        self.fake = FakeTVProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)

    def test_returns_the_shows_item_for_a_season_or_episode(self):
        show = make_show(self.fake, seasons={1: 2})
        season = Item.objects.create(
            media_id=show.media_id,
            source=show.source,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title=show.title,
            image=show.image,
        )
        self.assertEqual(tv_listing.show_item_for(show), show)
        self.assertEqual(tv_listing.show_item_for(season), show)

    def test_creates_a_missing_show_item_from_the_provider(self):
        self.fake.add_show("4242", title="Created Show", seasons={1: 2})
        episode = Item.objects.create(
            media_id="4242",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=2,
            title="Created Show",
            image="https://example.com/still.jpg",
        )
        show = tv_listing.show_item_for(episode)
        self.assertEqual(
            (show.media_type, show.title, show.season_number),
            (MediaTypes.TV.value, "Created Show", None),
        )

    def test_unavailable_when_the_provider_fails(self):
        self.fake.add_show("4343", seasons={1: 1})
        self.fake.fail()
        season = Item.objects.create(
            media_id="4343",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            season_number=1,
            title="Show",
            image="https://example.com/poster.jpg",
        )
        with self.assertRaises(TVDetailsUnavailable):
            tv_listing.show_item_for(season)


class UpdateFromMetadataTests(TestCase):
    """update_from_metadata reuses provider fetches made elsewhere."""

    def setUp(self):
        self.fake = FakeTVProvider().__enter__()
        self.addCleanup(self.fake.__exit__, None, None, None)

    def test_a_fetch_made_elsewhere_fills_the_copy(self):
        show = make_show(self.fake, seasons={1: 3, 2: 2})
        tv_listing.update_from_metadata(show, tmdb.tv_with_seasons(show.media_id, [1, 2]))
        self.fake.reset_calls()

        listing = tv_listing.get_listing(show)

        self.assertEqual(self.fake.count(), 0)
        self.assertEqual(listing.episode_title(2, 2), "Episode 2")

    def test_one_seasons_metadata(self):
        show = make_show(self.fake, seasons={1: 3})
        tv_listing.update_from_metadata(show, tmdb.tv(show.media_id))
        season = tmdb.tv_with_seasons(show.media_id, [1])["season/1"]
        tv_listing.update_from_metadata(show, season, season_number=1)
        self.assertEqual(len(tv_listing.stored_listing(show).episodes[1]), 3)

    def test_bad_metadata_is_logged_not_raised(self):
        show = make_show(self.fake, seasons={1: 3})
        with self.assertLogs("app.tv_listing", level="ERROR"):
            tv_listing.update_from_metadata(show, {"season/x": {"episodes": []}})
