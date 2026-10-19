"""Service filter, filter options, and Discover for streaming availability.

Contract: docs/streaming-availability-contract.md (ST-300 to ST-302).
"""

import hashlib
import logging
from datetime import timedelta
from statistics import median
from time import perf_counter
from unittest.mock import patch
from urllib.parse import urlencode

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from api.services import media as media_service
from app.models import TV, Anime, DiaryEntry, Item, MediaTypes, Movie, Sources, Status
from app.providers import tmdb
from lists.models import CustomList, CustomListItem
from streaming import filters as streaming_filters
from streaming.models import (
    StreamingAvailability,
    StreamingOffer,
    StreamingProvider,
    UserStreamingService,
)

logger = logging.getLogger(__name__)

DELAY = "streaming.tasks.refresh_streaming_titles.delay"
REGION_NAMES = {"US": "United States", "GB": "United Kingdom", "FR": "France"}
NETFLIX, HULU, APPLE, MAX = 8, 15, 2, 1899


def make_user(username, *, region=None, services=()):
    """Create a user with an optional saved country and services."""
    extra = {} if region is None else {"watch_provider_region": region}
    user = get_user_model().objects.create_user(
        username=username,
        password="strong-password-123",
        **extra,
    )
    UserStreamingService.objects.bulk_create([
        UserStreamingService(user=user, provider_id=provider_id) for provider_id in services
    ])
    return user


def make_item(media_id, title, *, media_type=MediaTypes.MOVIE.value, source=Sources.TMDB.value, **extra):
    """Create an Item; TMDB movie by default."""
    return Item.objects.create(
        source=source,
        media_type=media_type,
        media_id=media_id,
        title=title,
        **extra,
    )


def make_snapshot(title_type, tmdb_id, offers=(), *, region="US"):
    """Store a checked title with (provider_id, monetization) offers."""
    availability = StreamingAvailability.objects.create(
        title_type=title_type,
        tmdb_id=tmdb_id,
        region=region,
        fetched_at=timezone.now(),
    )
    StreamingOffer.objects.bulk_create([
        StreamingOffer(availability=availability, provider_id=provider_id, monetization=monetization)
        for provider_id, monetization in offers
    ])
    return availability


def make_providers():
    """Create Netflix, Hulu, Max, and Apple TV."""
    StreamingProvider.objects.bulk_create([
        StreamingProvider(id=NETFLIX, name="Netflix", logo_path="/netflix.png", display_priority=1),
        StreamingProvider(id=HULU, name="Hulu", logo_path="/hulu.png", display_priority=2),
        StreamingProvider(id=MAX, name="Max", logo_path="/max.png", display_priority=3),
        StreamingProvider(id=APPLE, name="Apple TV", logo_path="", display_priority=4),
    ])


class ServiceFilterTests(TestCase):
    """ST-300/ST-301: the service filter on tracking, list items, and diary."""

    @classmethod
    def setUpTestData(cls):
        make_providers()
        cls.user = make_user("streamer", services=[NETFLIX, HULU])
        cls.netflix_movie = make_item("100", "Alpha Netflix")
        cls.hulu_movie = make_item("101", "Bravo Hulu Ads")
        cls.rent_movie = make_item("102", "Charlie Rent")
        cls.max_movie = make_item("103", "Delta Max")
        cls.abroad_movie = make_item("104", "Echo Abroad")
        cls.unchecked_movie = make_item("105", "Foxtrot Unchecked")
        # TMDB movie 200 is a different title from TMDB show 200.
        cls.twin_movie = make_item("200", "Golf Movie Twin")
        cls.manual_movie = make_item("100", "Hotel Manual", source=Sources.MANUAL.value)
        cls.show = make_item("200", "India Show", media_type=MediaTypes.TV.value)
        cls.season = make_item("200", "India Show", media_type=MediaTypes.SEASON.value, season_number=1)
        cls.episode = make_item(
            "200",
            "India Show",
            media_type=MediaTypes.EPISODE.value,
            season_number=1,
            episode_number=1,
        )
        cls.buy_show = make_item("300", "Juliet Buy Show", media_type=MediaTypes.TV.value)
        cls.anime = make_item("100", "Kilo Anime", media_type=MediaTypes.ANIME.value, source=Sources.MAL.value)
        cls.book = make_item("100", "Lima Book", media_type=MediaTypes.BOOK.value, source=Sources.HARDCOVER.value)

        make_snapshot("movie", "100", [(NETFLIX, "flatrate"), (APPLE, "rent")])
        make_snapshot("movie", "101", [(HULU, "ads")])
        make_snapshot("movie", "102", [(NETFLIX, "rent"), (NETFLIX, "buy"), (APPLE, "rent")])
        make_snapshot("movie", "103", [(MAX, "flatrate")])
        make_snapshot("movie", "104", [])
        make_snapshot("movie", "104", [(NETFLIX, "flatrate")], region="GB")
        make_snapshot("tv", "200", [(NETFLIX, "flatrate")])
        make_snapshot("tv", "300", [(NETFLIX, "buy")])

        Movie.objects.bulk_create([
            Movie(user=cls.user, item=item, status=Status.COMPLETED.value)
            for item in (
                cls.netflix_movie,
                cls.hulu_movie,
                cls.rent_movie,
                cls.max_movie,
                cls.abroad_movie,
                cls.unchecked_movie,
                cls.twin_movie,
                cls.manual_movie,
            )
        ])
        TV.objects.bulk_create([
            TV(user=cls.user, item=cls.show, status=Status.IN_PROGRESS.value),
            TV(user=cls.user, item=cls.buy_show, status=Status.IN_PROGRESS.value),
        ])
        Anime.objects.create(user=cls.user, item=cls.anime, status=Status.COMPLETED.value)

    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _tracking(self, user=None, headers=None, **params):
        if user is not None:
            self.client.force_authenticate(user)
        return self.client.get(
            "/api/v1/tracking/",
            {"media_type": MediaTypes.MOVIE.value, "sort": "title", **params},
            headers=headers,
        )

    @staticmethod
    def _titles(response):
        return [result["media"]["title"] for result in response.data["results"]]

    def test_my_services_match_streamable_movies_in_my_country(self):
        response = self._tracking(service="mine")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._titles(response), ["Alpha Netflix", "Bravo Hulu Ads"])
        self.assertEqual(response.data["count"], 2)
        # 105 has no snapshot; movie 200 only shares an ID with a checked show.
        self.assertEqual(response.data["streaming"], {"region": "US", "pending_count": 2})

    def test_specific_services_match_any_listed_provider(self):
        cases = {
            str(MAX): ["Delta Max"],
            (str(NETFLIX), str(MAX)): ["Alpha Netflix", "Delta Max"],
            str(HULU): ["Bravo Hulu Ads"],
            # Apple TV only rents these titles, and rent/buy never count.
            str(APPLE): [],
        }
        for service, expected in cases.items():
            with self.subTest(service=service):
                response = self._tracking(service=list(service) if isinstance(service, tuple) else service)

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(self._titles(response), expected)

    def test_tv_shelf_matches_show_snapshots_and_skips_buy_only_shows(self):
        response = self._tracking(media_type=MediaTypes.TV.value, service="mine")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self._titles(response), ["India Show"])
        self.assertEqual(response.data["streaming"], {"region": "US", "pending_count": 0})

    def test_non_movie_tv_items_never_match(self):
        response = self._tracking(media_type=MediaTypes.ANIME.value, service=str(NETFLIX))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"], [])
        self.assertEqual(response.data["streaming"], {"region": "US", "pending_count": 0})

    def test_list_items_match_seasons_and_episodes_through_their_show(self):
        custom_list = CustomList.objects.create(owner=self.user, name="Mixed")
        for item in (
            self.netflix_movie,
            self.rent_movie,
            self.season,
            self.episode,
            self.anime,
            self.book,
            self.manual_movie,
            self.unchecked_movie,
            self.twin_movie,
        ):
            CustomListItem.objects.create(custom_list=custom_list, item=item)

        response = self.client.get(f"/api/v1/lists/{custom_list.id}/items/", {"service": "mine"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertCountEqual(
            [result["ref"]["item_id"] for result in response.data["results"]],
            [self.netflix_movie.id, self.season.id, self.episode.id],
        )
        self.assertEqual(response.data["streaming"], {"region": "US", "pending_count": 2})
        self.assertIn("completion", response.data)

    def test_public_lists_use_the_viewers_services(self):
        owner = make_user("list-owner", services=[MAX])
        custom_list = CustomList.objects.create(
            owner=owner,
            name="Public",
            visibility=CustomList.Visibility.PUBLIC,
        )
        for item in (self.netflix_movie, self.max_movie):
            CustomListItem.objects.create(custom_list=custom_list, item=item)

        response = self.client.get(f"/api/v1/lists/{custom_list.id}/items/", {"service": "mine"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [result["ref"]["item_id"] for result in response.data["results"]],
            [self.netflix_movie.id],
        )

    def test_diary_matches_episode_entries_through_their_show(self):
        now = timezone.now()
        for offset, item in enumerate((self.episode, self.max_movie, self.netflix_movie, self.abroad_movie)):
            DiaryEntry.objects.create(user=self.user, item=item, consumed_at=now - timedelta(days=offset))
        DiaryEntry.objects.create(user=self.user, item=self.netflix_movie, consumed_at=now - timedelta(days=9))

        response = self.client.get("/api/v1/diary/", {"service": "mine"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [result["media"]["ref"]["item_id"] for result in response.data["results"]],
            [self.episode.id, self.netflix_movie.id, self.netflix_movie.id],
        )
        self.assertEqual(response.data["streaming"], {"region": "US", "pending_count": 0})

    def test_device_region_header_is_used_until_a_country_is_saved(self):
        response = self._tracking(service="mine", headers={"X-Spine-Region": "gb"})

        self.assertEqual(self._titles(response), ["Echo Abroad"])
        self.assertEqual(response.data["streaming"]["region"], "GB")
        self.assertEqual(response.data["streaming"]["pending_count"], 6)

        saved = make_user("saved-us", region="US", services=[NETFLIX])
        Movie.objects.create(user=saved, item=self.netflix_movie, status=Status.COMPLETED.value)
        Movie.objects.create(user=saved, item=self.abroad_movie, status=Status.COMPLETED.value)

        response = self._tracking(user=saved, service="mine", headers={"X-Spine-Region": "GB"})

        self.assertEqual(self._titles(response), ["Alpha Netflix"])
        self.assertEqual(response.data["streaming"]["region"], "US")

    def test_my_services_without_services_match_nothing(self):
        viewer = make_user("no-services")
        Movie.objects.create(user=viewer, item=self.netflix_movie, status=Status.COMPLETED.value)

        mine = self._tracking(user=viewer, service="mine")
        explicit = self._tracking(user=viewer, service=str(NETFLIX))

        self.assertEqual(mine.status_code, status.HTTP_200_OK)
        self.assertEqual(mine.data["results"], [])
        self.assertEqual(mine.data["streaming"], {"region": "US", "pending_count": 0})
        self.assertEqual(self._titles(explicit), ["Alpha Netflix"])

    def test_disabled_availability_matches_nothing(self):
        viewer = make_user("disabled", region="", services=[NETFLIX])
        Movie.objects.create(user=viewer, item=self.netflix_movie, status=Status.COMPLETED.value)
        Movie.objects.create(user=viewer, item=self.unchecked_movie, status=Status.COMPLETED.value)

        with patch(DELAY) as delay, self.captureOnCommitCallbacks(execute=True):
            response = self._tracking(user=viewer, service="mine")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["streaming"], {"region": None, "pending_count": 0})
        delay.assert_not_called()

    def test_invalid_service_values_are_rejected(self):
        for service in ("netflix", "0", "-8", "8.5", "٨", "99999999999", ["mine", "8"]):
            with self.subTest(service=service):
                response = self._tracking(service=service)

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(
                    response.data["error"]["fields"],
                    {"service": ["Use mine or streaming provider IDs."]},
                )

        response = self._tracking(service=[str(provider_id) for provider_id in range(1, 102)])

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("service", response.data["error"]["fields"])

    def test_invalid_service_is_rejected_on_lists_and_diary(self):
        custom_list = CustomList.objects.create(owner=self.user, name="Mine")

        for url in (f"/api/v1/lists/{custom_list.id}/items/", "/api/v1/diary/"):
            with self.subTest(url=url):
                response = self.client.get(url, {"service": "netflix"})

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("service", response.data["error"]["fields"])

    def test_without_service_responses_are_unchanged(self):
        custom_list = CustomList.objects.create(owner=self.user, name="Mine")
        CustomListItem.objects.create(custom_list=custom_list, item=self.netflix_movie)

        responses = {
            "tracking": self._tracking(),
            "empty_service": self._tracking(service=""),
            "list": self.client.get(f"/api/v1/lists/{custom_list.id}/items/"),
            "diary": self.client.get("/api/v1/diary/"),
        }

        for name, response in responses.items():
            with self.subTest(name=name):
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertNotIn("streaming", response.data)
        self.assertEqual(responses["tracking"].data["count"], 8)

    def test_service_combines_with_other_filters_and_completion(self):
        Movie.objects.filter(user=self.user, item=self.hulu_movie).update(status=Status.PLANNING.value)

        response = self._tracking(service="mine", status=Status.PLANNING.value)

        self.assertEqual(self._titles(response), ["Bravo Hulu Ads"])
        self.assertEqual(response.data["completion"], {"completed_count": 0, "total_count": 1})
        # Only the Planning shelf is considered for pending titles.
        self.assertEqual(response.data["streaming"], {"region": "US", "pending_count": 0})


class PendingTitleTests(TestCase):
    """ST-301: unchecked titles are reported and queued for a check."""

    def setUp(self):
        cache.clear()
        make_providers()
        self.user = make_user("pending", services=[NETFLIX])
        # A saved GB user with services makes GB an active country.
        make_user("gb-subscriber", region="GB", services=[NETFLIX])
        self.items = [make_item(str(index), f"Movie {index}") for index in (1, 2, 3)]
        Movie.objects.bulk_create([
            Movie(user=self.user, item=item, status=Status.COMPLETED.value) for item in self.items
        ])
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _get(self, url="/api/v1/tracking/", headers=None, **params):
        query = {"service": "mine", **params}
        if url == "/api/v1/tracking/":
            query.setdefault("media_type", MediaTypes.MOVIE.value)
        with patch(DELAY) as delay, self.captureOnCommitCallbacks(execute=True):
            response = self.client.get(url, query, headers=headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response, delay

    def test_unchecked_titles_are_counted_and_queued_after_commit(self):
        make_snapshot("movie", "2", [])

        response, delay = self._get()

        self.assertEqual(response.data["streaming"], {"region": "US", "pending_count": 2})
        self.assertEqual(response.data["results"], [])
        delay.assert_called_once_with([["movie", "1"], ["movie", "3"]], force=False)

    def test_checks_are_not_queued_before_commit(self):
        with patch(DELAY) as delay, self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.client.get("/api/v1/tracking/", {"media_type": "movie", "service": "mine"})

        delay.assert_not_called()
        self.assertEqual(len(callbacks), 1)

    def test_checks_are_debounced_per_user_and_country(self):
        _, first = self._get()
        _, repeat = self._get(service=str(NETFLIX))
        _, other_country = self._get(headers={"X-Spine-Region": "GB"})
        other_user = make_user("pending-two", services=[NETFLIX])
        Movie.objects.create(user=other_user, item=self.items[0], status=Status.COMPLETED.value)
        self.client.force_authenticate(other_user)
        response, other = self._get()

        first.assert_called_once()
        repeat.assert_not_called()
        other_country.assert_called_once_with([["movie", "1"], ["movie", "2"], ["movie", "3"]], force=False)
        other.assert_called_once_with([["movie", "1"]], force=False)
        self.assertEqual(response.data["streaming"]["pending_count"], 1)

    def test_debounced_requests_still_report_pending_titles(self):
        self._get()
        response, delay = self._get()

        delay.assert_not_called()
        self.assertEqual(response.data["streaming"]["pending_count"], 3)

    def test_shows_seasons_and_episodes_are_one_title(self):
        custom_list = CustomList.objects.create(owner=self.user, name="Shows")
        for item in (
            make_item("7", "Show", media_type=MediaTypes.TV.value),
            make_item("7", "Show", media_type=MediaTypes.SEASON.value, season_number=1),
            make_item("7", "Show", media_type=MediaTypes.SEASON.value, season_number=2),
            make_item("7", "Show", media_type=MediaTypes.EPISODE.value, season_number=1, episode_number=1),
            make_item("7", "Movie Seven"),
            make_item("8", "Manual", source=Sources.MANUAL.value),
        ):
            CustomListItem.objects.create(custom_list=custom_list, item=item)

        response, delay = self._get(f"/api/v1/lists/{custom_list.id}/items/")

        self.assertEqual(response.data["streaming"]["pending_count"], 2)
        delay.assert_called_once_with([["movie", "7"], ["tv", "7"]], force=False)

    def test_queued_titles_are_batched_and_capped(self):
        items = Item.objects.bulk_create([
            Item(
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                media_id=f"bulk-{index:04d}",
                title=f"Bulk {index:04d}",
            )
            for index in range(600)
        ])
        Movie.objects.bulk_create([
            Movie(user=self.user, item=item, status=Status.COMPLETED.value) for item in items
        ])

        response, delay = self._get()

        self.assertEqual(response.data["streaming"]["pending_count"], 603)
        batches = [call.args[0] for call in delay.call_args_list]
        self.assertEqual([len(batch) for batch in batches], [100] * 5)
        queued = [title for batch in batches for title in batch]
        self.assertEqual(len(queued), streaming_filters.PENDING_CHECK_LIMIT)
        self.assertEqual(len({tuple(title) for title in queued}), 500)

    def test_countries_without_snapshots_are_not_queued(self):
        response, delay = self._get(headers={"X-Spine-Region": "FR"})

        self.assertEqual(response.data["streaming"], {"region": "FR", "pending_count": 3})
        delay.assert_not_called()

    def test_queue_failures_are_logged_not_raised(self):
        # The callback is robust, so a broker failure is logged after commit.
        with (
            patch(DELAY, side_effect=OSError("broker down")) as delay,
            self.assertLogs("django.test", level="ERROR") as logs,
            self.captureOnCommitCallbacks(execute=True),
        ):
            response = self.client.get("/api/v1/tracking/", {"media_type": "movie", "service": "mine"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        delay.assert_called_once()
        self.assertIn("broker down", logs.output[0])


class FilterOptionsStreamingTests(TestCase):
    """GET /filter-options/ adds a streaming block for every scope."""

    def setUp(self):
        cache.clear()
        make_providers()
        # Created out of order to prove display-priority ordering.
        self.user = make_user("options", services=[HULU, APPLE, NETFLIX])
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        region_names = patch("streaming.services.region_names", return_value=REGION_NAMES)
        region_names.start()
        self.addCleanup(region_names.stop)

    def _streaming(self, headers=None, **params):
        response = self.client.get("/api/v1/filter-options/", params, headers=headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data["streaming"]

    def test_tracking_scope_lists_my_services_in_display_priority_order(self):
        self.assertEqual(
            self._streaming(scope="tracking", media_type="movie"),
            {
                "available": True,
                "region": "US",
                "region_name": "United States",
                "services": [
                    {
                        "value": "8",
                        "label": "Netflix",
                        "logo_url": "https://image.tmdb.org/t/p/w154/netflix.png",
                    },
                    {
                        "value": "15",
                        "label": "Hulu",
                        "logo_url": "https://image.tmdb.org/t/p/w154/hulu.png",
                    },
                    {"value": "2", "label": "Apple TV", "logo_url": None},
                ],
            },
        )

    def test_tracking_scope_is_available_for_movie_and_tv_only(self):
        for media_type, available in (("movie", True), ("tv", True), ("book", False), ("anime", False)):
            with self.subTest(media_type=media_type):
                self.assertIs(self._streaming(scope="tracking", media_type=media_type)["available"], available)

    def test_list_scope_is_available_when_the_list_holds_tmdb_movie_or_tv_items(self):
        books = CustomList.objects.create(owner=self.user, name="Books")
        CustomListItem.objects.create(
            custom_list=books,
            item=make_item("b1", "Book", media_type=MediaTypes.BOOK.value, source=Sources.HARDCOVER.value),
        )
        manual = CustomList.objects.create(owner=self.user, name="Manual")
        CustomListItem.objects.create(custom_list=manual, item=make_item("m1", "Manual", source=Sources.MANUAL.value))
        mixed = CustomList.objects.create(owner=self.user, name="Mixed")
        CustomListItem.objects.create(
            custom_list=mixed,
            item=make_item("s1", "Show", media_type=MediaTypes.SEASON.value, season_number=1),
        )
        CustomListItem.objects.create(
            custom_list=mixed,
            item=make_item("b2", "Book", media_type=MediaTypes.BOOK.value, source=Sources.HARDCOVER.value),
        )

        cases = [
            ({"list_id": books.id}, False),
            ({"list_id": manual.id}, False),
            ({"list_id": mixed.id}, True),
            ({"list_id": mixed.id, "media_type": "book"}, False),
            ({"list_id": mixed.id, "media_type": "season"}, True),
        ]
        for params, available in cases:
            with self.subTest(params=params):
                block = self._streaming(scope="list", **params)

                self.assertIs(block["available"], available)
                self.assertEqual([service["value"] for service in block["services"]], ["8", "15", "2"])

    def test_diary_scope_is_available_when_entries_include_tmdb_movie_or_tv(self):
        self.assertFalse(self._streaming(scope="diary")["available"])

        DiaryEntry.objects.create(
            user=self.user,
            item=make_item("g1", "Game", media_type=MediaTypes.GAME.value, source=Sources.IGDB.value),
            consumed_at=timezone.now(),
        )
        self.assertFalse(self._streaming(scope="diary")["available"])

        DiaryEntry.objects.create(
            user=self.user,
            item=make_item("e1", "Show", media_type=MediaTypes.EPISODE.value, season_number=1, episode_number=1),
            consumed_at=timezone.now(),
        )
        self.assertTrue(self._streaming(scope="diary")["available"])

    def test_region_follows_header_and_disabled_preference(self):
        header = self._streaming(scope="tracking", media_type="movie", headers={"X-Spine-Region": "GB"})

        self.assertEqual((header["region"], header["region_name"]), ("GB", "United Kingdom"))

        self.client.force_authenticate(make_user("options-off", region=""))
        disabled = self._streaming(scope="tracking", media_type="movie")

        self.assertEqual(
            disabled,
            {"available": True, "region": None, "region_name": None, "services": []},
        )


@patch("api.services.media.provider_services.discover")
class DiscoverServiceTests(TestCase):
    """ST-302: Discover can browse what streams on the user's services."""

    def setUp(self):
        cache.clear()
        make_providers()
        self.user = make_user("discover", services=[HULU, NETFLIX])
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    @staticmethod
    def _results():
        return {
            "per_page": 20,
            "total_results": 1,
            "results": [
                {
                    "media_id": "550",
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.MOVIE.value,
                    "title": "Fight Club",
                    "image": "https://example.com/fight-club.jpg",
                },
            ],
        }

    def test_my_services_need_no_genre_or_year(self, discover):
        discover.return_value = self._results()

        response = self.client.get("/api/v1/media/discover/", {"media_type": "movie", "service": "mine"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"][0]["title"], "Fight Club")
        discover.assert_called_once_with(
            MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
            page=1,
            page_size=25,
            genre=None,
            year=None,
            platform=None,
            sort="vote_count",
            watch_region="US",
            watch_providers=[NETFLIX, HULU],
        )

    def test_specific_services_combine_with_genre_and_device_region(self, discover):
        discover.return_value = self._results()

        response = self.client.get(
            "/api/v1/media/discover/",
            {"media_type": "tv", "service": ["337", "8"], "genre": "Drama"},
            headers={"X-Spine-Region": "GB"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        kwargs = discover.call_args.kwargs
        self.assertEqual(
            (kwargs["genre"], kwargs["watch_region"], kwargs["watch_providers"]),
            ("Drama", "GB", [8, 337]),
        )

    def test_my_services_without_services_are_rejected(self, discover):
        self.client.force_authenticate(make_user("discover-none"))

        response = self.client.get("/api/v1/media/discover/", {"media_type": "movie", "service": "mine"})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, {"service": ["Choose your streaming services first."]})
        discover.assert_not_called()

    def test_service_is_only_for_movies_and_tv(self, discover):
        for media_type in ("book", "anime", "game"):
            with self.subTest(media_type=media_type):
                response = self.client.get(
                    "/api/v1/media/discover/",
                    {"media_type": media_type, "service": "mine", "genre": "Drama"},
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(response.data, {"service": ["Streaming services can only filter movie or tv."]})
        discover.assert_not_called()

    def test_invalid_service_is_rejected_without_the_genre_error(self, discover):
        response = self.client.get("/api/v1/media/discover/", {"media_type": "movie", "service": "netflix"})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data, {"service": ["Use mine or streaming provider IDs."]})
        discover.assert_not_called()

    def test_disabled_availability_returns_no_results(self, discover):
        self.client.force_authenticate(make_user("discover-off", region="", services=[NETFLIX]))

        response = self.client.get("/api/v1/media/discover/", {"media_type": "movie", "service": "mine"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"count": 0, "next": None, "previous": None, "results": []})
        discover.assert_not_called()

    def test_without_service_behavior_and_cache_key_are_unchanged(self, discover):
        discover.return_value = self._results()

        missing = self.client.get("/api/v1/media/discover/", {"media_type": "movie"})
        response = self.client.get("/api/v1/media/discover/", {"media_type": "movie", "genre": "Drama"})

        self.assertEqual(missing.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            missing.data,
            {"non_field_errors": ["At least one of genre, year, or platform is required."]},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        discover.assert_called_once_with(
            MediaTypes.MOVIE.value,
            source=Sources.TMDB.value,
            page=1,
            page_size=25,
            genre="Drama",
            year=None,
            platform=None,
            sort="vote_count",
        )
        legacy_filters = urlencode(
            {"genre": "Drama", "year": "", "platform": "", "sort": "vote_count", "page_size": 25},
        )
        legacy_hash = hashlib.sha256(legacy_filters.encode()).hexdigest()[:24]
        self.assertIsNotNone(cache.get(f"api:v1:discover:movie:tmdb:{legacy_hash}:p1"))

    def test_api_cache_key_includes_region_and_sorted_services(self, discover):
        discover.return_value = self._results()

        def run(region, providers):
            media_service.discover_media(
                media_type="movie",
                watch_region=region,
                watch_providers=providers,
            )

        run("US", [8])
        run("US", [8])
        self.assertEqual(discover.call_count, 1)
        run("GB", [8])
        self.assertEqual(discover.call_count, 2)
        run("US", [15, 8])
        run("US", [8, 15])
        self.assertEqual(discover.call_count, 3)
        self.assertEqual(discover.call_args.kwargs["watch_providers"], [8, 15])
        media_service.discover_media(media_type="movie")
        self.assertEqual(discover.call_count, 4)
        self.assertNotIn("watch_providers", discover.call_args.kwargs)


@patch("app.providers.tmdb.services.api_request", return_value={"results": [], "total_results": 0})
class TmdbDiscoverWatchProviderTests(TestCase):
    """TMDB discover sends watch-provider filters and caches per region and services."""

    def setUp(self):
        cache.clear()

    def test_watch_filters_are_sent_to_tmdb(self, api_request):
        tmdb.discover("movie", watch_region="US", watch_providers=[15, 8])

        params = api_request.call_args.kwargs["params"]
        self.assertEqual(api_request.call_args.args[2], f"{tmdb.base_url}/discover/movie")
        self.assertEqual(params["watch_region"], "US")
        self.assertEqual(params["with_watch_providers"], "8|15")
        self.assertEqual(params["with_watch_monetization_types"], "flatrate|free|ads")
        self.assertEqual(params["sort_by"], "vote_count.desc")

    def test_cache_key_includes_region_and_sorted_providers(self, api_request):
        tmdb.discover("tv", watch_region="US", watch_providers=[8])
        tmdb.discover("tv", watch_region="US", watch_providers=[8])
        self.assertEqual(api_request.call_count, 1)
        tmdb.discover("tv", watch_region="GB", watch_providers=[8])
        self.assertEqual(api_request.call_count, 2)
        tmdb.discover("tv", watch_region="US", watch_providers=[8, 15])
        tmdb.discover("tv", watch_region="US", watch_providers=[15, 8])
        self.assertEqual(api_request.call_count, 3)

    def test_requests_without_providers_are_unchanged(self, api_request):
        tmdb.discover("movie", genre=None, year="1999")

        params = api_request.call_args.kwargs["params"]
        self.assertFalse({"watch_region", "with_watch_providers", "with_watch_monetization_types"} & set(params))
        self.assertIsNotNone(
            cache.get(f"discover_tmdb_movie_None_1999_1_{tmdb.settings.TMDB_LANG}_{tmdb.settings.TMDB_NSFW}"),
        )


class ServiceFilterPerformanceTests(TestCase):
    """Bound queries and latency for a filtered library at realistic size."""

    ROW_COUNT = 3000

    @classmethod
    def setUpTestData(cls):
        StreamingProvider.objects.bulk_create([
            StreamingProvider(id=provider_id, name=f"Provider {provider_id}", display_priority=provider_id)
            for provider_id in range(1, 9)
        ])
        cls.user = make_user("streaming-performance", services=[1, 2])
        items = Item.objects.bulk_create([
            Item(
                source=Sources.TMDB.value,
                media_type=MediaTypes.MOVIE.value,
                media_id=f"{index}",
                title=f"Movie {index:05d}",
            )
            for index in range(cls.ROW_COUNT)
        ])
        Movie.objects.bulk_create([
            Movie(user=cls.user, item=item, status=Status.COMPLETED.value) for item in items
        ])
        now = timezone.now()
        # Every title but the last 100 is checked in the US; half also in GB.
        availabilities = StreamingAvailability.objects.bulk_create(
            [
                StreamingAvailability(title_type="movie", tmdb_id=f"{index}", region="US", fetched_at=now)
                for index in range(cls.ROW_COUNT - 100)
            ]
            + [
                StreamingAvailability(title_type="movie", tmdb_id=f"{index}", region="GB", fetched_at=now)
                for index in range(0, cls.ROW_COUNT, 2)
            ],
        )
        monetizations = ["flatrate", "ads", "rent", "buy", "free"]
        StreamingOffer.objects.bulk_create(
            [
                StreamingOffer(
                    availability=availability,
                    provider_id=index % 8 + 1,
                    monetization=monetizations[index % len(monetizations)],
                )
                for index, availability in enumerate(availabilities)
            ]
            + [
                StreamingOffer(availability=availability, provider_id=(index + 3) % 8 + 1, monetization="rent")
                for index, availability in enumerate(availabilities)
            ]
            + [
                StreamingOffer(availability=availability, provider_id=(index + 5) % 8 + 1, monetization="flatrate")
                for index, availability in enumerate(availabilities)
                if index % 2 == 0
            ],
        )
        cls.offer_count = StreamingOffer.objects.count()
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                for model in (Item, Movie, StreamingAvailability, StreamingOffer):
                    cursor.execute(f"ANALYZE {model._meta.db_table}")

    def _request(self, page_size, **params):
        client = APIClient()
        # A fresh user per request, as real authentication gives; completed_item_ids()
        # memoizes on the user object.
        client.force_authenticate(get_user_model().objects.get(pk=self.user.pk))
        response = client.get(
            "/api/v1/tracking/",
            {"media_type": MediaTypes.MOVIE.value, "sort": "title", "page_size": page_size, **params},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response

    def test_filtered_page_query_count_is_constant_and_bounded(self):
        self.assertGreater(self.offer_count, 7000)
        with patch(DELAY):
            # The first filtered request also claims the pending-check debounce.
            warm = self._request(25, service="mine")
            counts = {}
            for label, page_size, params in (
                ("plain", 25, {}),
                ("filtered", 25, {"service": "mine"}),
                ("filtered", 100, {"service": "mine"}),
                ("explicit", 100, {"service": ["1", "2"]}),
            ):
                with CaptureQueriesContext(connection) as queries:
                    self._request(page_size, **params)
                counts[(label, page_size)] = len(queries)

        self.assertEqual(warm.data["streaming"], {"region": "US", "pending_count": 100})
        self.assertGreater(warm.data["count"], 0)
        self.assertEqual(counts[("filtered", 25)], counts[("filtered", 100)])
        # The filter adds the service lookup and the pending count, nothing per row.
        self.assertLessEqual(counts[("filtered", 25)] - counts[("plain", 25)], 2)
        self.assertLessEqual(counts[("explicit", 100)] - counts[("plain", 25)], 1)

    def test_filtered_tracking_latency_stays_close_to_unfiltered(self):
        with patch(DELAY):
            self._request(100, service="mine")
            timings = {}
            for label, params in (("plain", {}), ("filtered", {"service": "mine"})):
                samples = []
                for _ in range(5):
                    started = perf_counter()
                    self._request(100, **params)
                    samples.append(perf_counter() - started)
                timings[label] = median(samples)
        logger.info(
            "streaming_filter_performance rows=%s offers=%s plain_ms=%.2f filtered_ms=%.2f",
            self.ROW_COUNT,
            self.offer_count,
            timings["plain"] * 1000,
            timings["filtered"] * 1000,
        )
        self.assertLessEqual(timings["filtered"] - timings["plain"], 0.05)

    def test_sqlite_plans_look_up_snapshots_by_title_and_region(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite plan check")
        queryset = Movie.objects.filter(user=self.user)
        filtered = queryset.filter(streaming_filters.streamable_filter("US", [1, 2])).explain()
        pending = streaming_filters._unchecked_rows(queryset, "US", "item__").explain()

        # SQLite names unique-constraint indexes sqlite_autoindex_<table>_N.
        self.assertIn("(availability_id=? AND provider_id=? AND monetization=?)", filtered)
        for plan in (filtered, pending):
            with self.subTest(plan=plan):
                self.assertIn("(title_type=? AND tmdb_id=? AND region=?)", plan)
                # Driven by the user's rows, never a scan of a global table.
                self.assertIn("SEARCH app_movie USING", plan)
                self.assertNotIn("SCAN", plan)
