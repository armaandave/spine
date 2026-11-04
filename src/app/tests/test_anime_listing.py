"""The stored copy of a title's numbers and rows (AN-100, AN-101), and the job (AN-202)."""

import copy
from datetime import UTC, datetime, timedelta
from unittest import mock

import requests
from django.test import SimpleTestCase

from app import anime_listing, anime_tracking
from app.anime_listing import AnimeDetailsUnavailable
from app.models import AnimeListing, Item
from app.providers import anilist, services
from app.tests.anime_fixtures import AnimeTestCase, FakeAnimeProvider
from app.tests.tv_fixtures import ProviderDown
from events.calendar import anime as calendar_anime
from events.calendar.selectors import filter_items_to_fetch
from events.models import Event

ATTACK_ON_TITAN = {
    "data": {
        "Page": {
            "pageInfo": {"hasNextPage": False},
            "media": [
                {
                    "idMal": 16498,
                    "nextAiringEpisode": None,
                    "airingSchedule": {"nodes": []},
                    "streamingEpisodes": [
                        {
                            "title": "Episode 2 - That Day - The Fall of Zhiganshina (2)",
                            "thumbnail": "https://img1.ak.crunchyroll.com/2.jpg",
                        },
                        {
                            "title": "Episode 1 - To You, 2,000 Years in the Future",
                            "thumbnail": "https://img1.ak.crunchyroll.com/1.jpg",
                        },
                        {"title": "Special Recap", "thumbnail": "https://img1.ak.crunchyroll.com/x.jpg"},
                    ],
                },
            ],
        },
    },
}
ONE_PIECE = {
    "data": {
        "Page": {
            "pageInfo": {"hasNextPage": False},
            "media": [
                {
                    "idMal": 21,
                    "nextAiringEpisode": {"episode": 1181, "airingAt": 1798985760},
                    "airingSchedule": {
                        "nodes": [
                            {"episode": 1181, "airingAt": 1798985760},
                            {"episode": 1182, "airingAt": 1799590560},
                        ],
                    },
                    "streamingEpisodes": [
                        {"title": "Episode 130 - Scent of Danger!", "thumbnail": "https://example.com/130.jpg"},
                        {"title": "Episode 129 - It All Started", "thumbnail": "https://example.com/129.jpg"},
                        {"title": "Episode 129 - It All Started", "thumbnail": "https://example.com/other-site.jpg"},
                    ],
                },
            ],
        },
    },
}


class AniListParsingTests(SimpleTestCase):
    """The shapes AniList really answers with, recorded 2026-11-03 for MAL 16498 and 21."""

    def test_an_101_the_episode_number_is_parsed_out_of_the_title(self):
        for value, expected in (
            ("Episode 130 - Scent of Danger!", (130, "Scent of Danger!")),
            ("Episode 5", (5, "")),
            ("Ep. 12: Finale", (12, "Finale")),
            ("episode 7 – Title", (7, "Title")),
            ("Recap", (None, "")),
            (None, (None, "")),
        ):
            self.assertEqual(anilist.parse_episode_title(value), expected, value)

    def test_an_101_order_isnt_guaranteed_and_a_second_site_doesnt_replace_the_first(self):
        with mock.patch.object(services, "api_request", return_value=copy.deepcopy(ONE_PIECE)):
            result = anilist.anime_episodes(["21"])["21"]
        self.assertEqual(result["next"], {"episode": 1181, "airing_at": 1798985760})
        self.assertEqual(result["schedule"], {1181: 1798985760, 1182: 1799590560})
        self.assertEqual(sorted(result["episodes"]), [129, 130])
        self.assertEqual(result["episodes"][129]["image"], "https://example.com/129.jpg")

    def test_an_101_a_finished_title_has_no_schedule(self):
        with mock.patch.object(services, "api_request", return_value=copy.deepcopy(ATTACK_ON_TITAN)):
            result = anilist.anime_episodes(["16498"])["16498"]
        self.assertIsNone(result["next"])
        self.assertEqual(result["schedule"], {})
        self.assertEqual(result["episodes"][1]["title"], "To You, 2,000 Years in the Future")

    def test_an_101_one_request_per_25_titles(self):
        empty = {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": []}}}
        with mock.patch.object(services, "api_request", return_value=empty) as request:
            self.assertEqual(anilist.anime_episodes([str(number) for number in range(1, 51)]), {})
        self.assertEqual(request.call_count, 2)
        self.assertEqual(len(request.call_args.kwargs["params"]["variables"]["malIds"]), 25)


class ListingTests(AnimeTestCase):
    """AN-100, AN-101: the copy, a first open and backing off."""

    def test_an_101_a_first_open_tries_once_with_a_short_timeout_then_reads_the_copy(self):
        item = self.title(total=2)
        with mock.patch.object(anilist, "anime_episodes", wraps=anilist.anime_episodes) as episodes:
            anime_listing.get_listing(item)
        self.assertEqual(episodes.call_args.kwargs["timeout"], anime_listing.FIRST_OPEN_TIMEOUT)
        self.assertFalse(episodes.call_args.kwargs["retry_rate_limits"])
        self.fake.calls.clear()
        anime_listing.get_listing(item)
        self.state(item)
        self.rows(item)
        self.assertEqual(self.fake.calls, [])

    def test_an_101_a_failure_stores_the_numbers_and_backs_off(self):
        self.fake.fail("anilist")
        item = self.title(total=2)
        listing = anime_listing.get_listing(item)
        self.assertEqual(listing.total, 2)
        stored = AnimeListing.objects.get(item=item)
        self.assertIsNone(stored.rows_fetched_at)
        self.assertEqual(stored.retry_after, self.clock.moment + anime_listing.FAILURE_BACKOFF)
        self.fake.calls.clear()
        self.fake.recover()
        anime_listing.get_listing(item)
        self.assertEqual(self.fake.calls, [])
        self.clock.advance(minutes=16)
        anime_listing.get_listing(item)
        self.assertEqual(self.fake.calls, ["anilist"])
        self.assertIsNotNone(AnimeListing.objects.get(item=item).rows_fetched_at)

    def test_an_101_a_page_answers_when_every_provider_is_down(self):
        item = self.title()
        self.fake.fail()
        self.assertEqual(self.rows(item)["episodes"], [{"n": 1, "a": 1}])
        state = self.state(item)
        self.assertIsNone(state["aired_count"])

    def test_an_100_with_no_copy_and_mal_down_get_listing_says_unavailable(self):
        item = self.title()
        self.fake.fail("mal")
        with self.assertRaises(AnimeDetailsUnavailable):
            anime_listing.get_listing(item)

    def test_an_100_the_copy_keeps_total_next_episode_and_rows(self):
        moment = self.clock.moment
        item = self.title(
            total=12,
            status="Airing",
            format="TV",
            start_date="2026-10-01",
            next_episode=4,
            next_air_at=moment + timedelta(days=1),
            schedule={4: moment + timedelta(days=1), 5: moment + timedelta(days=8)},
            episodes={1: ("One", "https://example.com/1.jpg")},
        )
        anime_listing.get_listing(item)
        stored = AnimeListing.objects.get(item=item)
        self.assertEqual((stored.total, stored.provider_status, stored.next_episode), (12, "Airing", 4))
        self.assertEqual(stored.rows[0], [1, "One", "https://example.com/1.jpg", None])
        self.assertEqual(stored.rows[1][0], 4)
        self.assertEqual(stored.premiere_date.isoformat(), "2026-10-01")

    def test_an_100_a_partial_start_date_isnt_a_premiere(self):
        item = self.title(status="Upcoming", start_date="2027-03")
        self.assertEqual(self.state(item)["eye"]["message"], "Nothing has aired yet.")

    def test_a_manual_title_has_no_numbers_and_is_never_unavailable(self):
        item = Item.objects.create(source="manual", media_type="anime", media_id="manual-1", title="Mine", image="https://example.com/p.jpg")
        self.fake.fail()
        self.tick(item, 3)
        self.assertEqual(self.row(item).progress, 1)
        self.assertFalse(AnimeListing.objects.filter(item=item).exists())

    def test_an_100_page_metadata_updates_the_numbers_with_no_call(self):
        item = self.title(total=2)
        anime_listing.get_listing(item)
        self.fake.calls.clear()
        anime_listing.update_from_metadata(item, {"max_progress": 5, "details": {"status": "Airing"}})
        stored = AnimeListing.objects.get(item=item)
        self.assertEqual((stored.total, stored.provider_status), (5, "Airing"))
        self.assertEqual(self.fake.calls, [])

    def test_an_100_a_usual_page_view_writes_nothing(self):
        item = self.title(total=2)
        anime_listing.get_listing(item)
        stored = AnimeListing.objects.get(item=item)
        metadata = {"max_progress": 2, "details": {"status": "Finished"}}
        with self.assertNumQueries(1):
            anime_listing.update_from_metadata(item, metadata)
        self.assertEqual(AnimeListing.objects.get(item=item).fetched_at, stored.fetched_at)


class CalendarJobTests(AnimeTestCase):
    """AN-202: the calendar job refreshes the copy and reopens titles."""

    def setUp(self):
        super().setUp()
        schedule = {"data": {"Page": {"pageInfo": {"hasNextPage": False}, "media": []}}}
        patcher = mock.patch.object(services, "api_request", return_value=schedule)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_202_the_calendar_job_reopens_a_caught_up_title(self):
        title = self.fake.add(total=12, status="Airing", next_episode=3, next_air_at=self.clock.moment + timedelta(days=2))
        item = self.title(title=title)
        self.tick(item, 1, 2)
        self.assertTrue(self.row(item).caught_up)
        self.clock.advance(days=3)
        title.next_episode = 4
        title.next_air_at = self.clock.moment + timedelta(days=4)
        with mock.patch.object(calendar_anime, "process_other"):
            calendar_anime.process_anime_bulk([item], [])
        row = self.row(item)
        self.assertEqual((row.caught_up, row.up_next_episode, row.aired_count), (False, 3, 3))

    def test_an_202_a_failing_refresh_never_stops_the_events(self):
        item = self.title(total=2)
        self.fake.fail()
        with mock.patch.object(calendar_anime, "process_other") as other:
            calendar_anime.process_anime_bulk([item], [])
        other.assert_called_once()

    def test_an_202_refreshing_asks_anilist_once_for_many_titles(self):
        items = [self.title(total=2) for _ in range(3)]
        self.fake.calls.clear()
        anime_tracking.refresh_after_calendar(items)
        self.assertEqual(self.fake.count("anilist"), 1)

    def test_an_202_the_job_looks_again_at_a_stored_copy_once_a_week(self):
        item = self.title(total=2)
        self.tick(item, 1, 2)
        Event.objects.create(item=item, content_number=2, datetime=self.clock.moment - timedelta(days=400))
        selected = filter_items_to_fetch(Item.objects.filter(pk=item.pk))
        self.assertFalse(selected.exists())
        self.clock.advance(days=8)
        selected = filter_items_to_fetch(Item.objects.filter(pk=item.pk))
        self.assertTrue(selected.exists())

    def test_an_100_mal_down_keeps_the_numbers_and_works_the_rest_out(self):
        title = self.fake.add(total=2)
        item = self.title(title=title)
        self.tick(item, 1)
        self.fake.fail("mal")
        title.next_episode = None
        anime_tracking.refresh_after_calendar([item])
        self.assertEqual(AnimeListing.objects.get(item=item).total, 2)
        self.assertEqual(self.row(item).progress, 1)


class ProviderFailureShapesTests(AnimeTestCase):
    """A network failure and a provider error both keep the numbers (AN-101)."""

    def test_an_101_a_timeout_keeps_the_numbers(self):
        item = self.title(total=2)
        with mock.patch.object(anilist, "anime_episodes", side_effect=requests.exceptions.Timeout):
            listing = anime_listing.get_listing(item)
        self.assertEqual(listing.total, 2)

    def test_an_101_a_provider_error_keeps_the_numbers(self):
        item = self.title(total=2)
        with mock.patch.object(anilist, "anime_episodes", side_effect=ProviderDown):
            listing = anime_listing.get_listing(item)
        self.assertEqual(listing.total, 2)

    def test_fake_provider_blocks_real_requests(self):
        with FakeAnimeProvider(), self.assertRaises(AssertionError):
            services.api_request("MAL", "GET", "https://example.com")
        self.assertEqual(datetime(2026, 1, 1, tzinfo=UTC).year, 2026)
