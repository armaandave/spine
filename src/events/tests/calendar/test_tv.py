import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import requests
from django.core.cache import cache
from django.test import TestCase

from app import tv_tracking
from app.models import TV, Season, Status
from app.tests.tv_fixtures import FakeTVProvider, patch_now
from events.calendar.helpers import date_parser
from events.calendar.tv import (
    get_episode_datetime,
    get_seasons_to_process,
    get_tvmaze_episode_map,
    get_tvmaze_response,
    process_season_episodes,
    process_tv,
)
from events.models import Event
from events.tests.calendar.utils import CalendarFixturesMixin


class CalendarTVTests(CalendarFixturesMixin, TestCase):
    """Test TV calendar processing."""

    @patch("events.calendar.tv.tmdb.tv")
    @patch("events.calendar.tv.tmdb.tv_with_seasons")
    @patch("events.calendar.tv.get_tvmaze_episode_map")
    def test_process_tv_season(
        self,
        mock_get_tvmaze_episode_map,
        mock_tv_with_seasons,
        mock_tv,
    ):
        """Test processing for a TV season."""
        mock_tv.return_value = {
            "related": {
                "seasons": [
                    {"season_number": 1, "episodes": [1, 2, 3]},
                    {"season_number": 2, "episodes": [1, 2]},
                    {"season_number": 3, "episodes": [1]},
                ],
            },
            "next_episode_season": 2,
        }

        mock_tv_with_seasons.return_value = {
            "season/1": {
                "image": "http://example.com/season1.jpg",
                "season_number": 1,
                "episodes": [
                    {"episode_number": 1, "air_date": "2008-01-20"},
                    {"episode_number": 2, "air_date": "2008-01-27"},
                    {"episode_number": 3, "air_date": "2008-02-03"},
                ],
                "tvdb_id": "81189",
            },
            "season/2": {
                "image": "http://example.com/season2.jpg",
                "season_number": 2,
                "episodes": [
                    {"episode_number": 1, "air_date": "2009-01-20"},
                    {"episode_number": 2, "air_date": "2009-01-27"},
                ],
                "tvdb_id": "81189",
            },
            "season/3": {
                "image": "http://example.com/season3.jpg",
                "season_number": 3,
                "episodes": [
                    {"episode_number": 1, "air_date": "2010-01-20"},
                ],
                "tvdb_id": "81189",
            },
        }

        mock_get_tvmaze_episode_map.return_value = {
            "1_1": "2008-01-20T22:00:00+00:00",
            "1_2": "2008-01-27T22:00:00+00:00",
            "1_3": "2008-02-03T22:00:00+00:00",
        }

        events_bulk = []
        process_tv(self.tv_item, events_bulk)

        self.assertEqual(len(events_bulk), 6)
        self.assertEqual(events_bulk[0].item, self.season_item)
        self.assertEqual(events_bulk[0].content_number, 1)

        expected_date = datetime.datetime.fromisoformat("2008-01-20T22:00:00+00:00")
        self.assertEqual(events_bulk[0].datetime, expected_date)

    def _finished_show(self, fake):
        """Replace the fixture's rows with a show the user finished by marking it."""
        TV.objects.filter(item=self.tv_item, user=self.user).delete()
        fake.add_show(
            self.tv_item.media_id,
            title="Breaking Bad",
            seasons={1: [datetime.date(2008, 1, 20)]},
            status="Returning Series",
        )
        tv_tracking.mark_watched(self.user, self.tv_item)
        Event.objects.create(
            item=self.season_item,
            content_number=1,
            datetime=date_parser("2008-01-20"),
        )
        tv = TV.objects.get(item=self.tv_item, user=self.user)
        self.assertEqual(tv.status, Status.COMPLETED.value)
        return tv

    @patch("events.calendar.tv.get_tvmaze_episode_map", return_value={})
    @patch("app.models.Item.fetch_releases")
    def test_process_tv_reopens_completed_show_with_new_season_as_planning(
        self,
        _fetch_releases,
        _tvmaze,
    ):
        """A new season that hasn't aired leaves a finished show Completed, caught up.

        Rewritten for TV-202 and TV-205: the old rule reopened the show and created the
        new season as Planning. Now the show waits in Completed with the next season's
        date and Spine creates no empty season; it reopens when the season airs.
        """
        with FakeTVProvider() as fake, patch_now(datetime.datetime(2026, 10, 25, 12, 0)):
            self._finished_show(fake)
            fake.show(self.tv_item.media_id).set_season(2, [datetime.date(2027, 1, 20)])
            fake.show(self.tv_item.media_id).next_episode_to_air = {
                "season_number": 2,
                "episode_number": 1,
                "air_date": datetime.date(2027, 1, 20),
            }

            events_bulk = []
            process_tv(self.tv_item, events_bulk)

        tv = TV.objects.get(item=self.tv_item, user=self.user)
        self.assertEqual(tv.status, Status.COMPLETED.value)
        self.assertTrue(tv.caught_up)
        self.assertEqual((tv.next_air_season, tv.next_air_date), (2, datetime.date(2027, 1, 20)))
        self.assertFalse(
            Season.objects.filter(
                related_tv=tv,
                item__season_number=2,
            ).exists(),
        )
        self.assertEqual(len(events_bulk), 1)

    @patch("events.calendar.tv.get_tvmaze_episode_map", return_value={})
    @patch("app.models.Item.fetch_releases")
    def test_process_tv_does_not_reopen_completed_show_for_past_only_season(
        self,
        _fetch_releases,
        _tvmaze,
    ):
        """An aired season the provider adds reopens a finished show (TV-206, A-12).

        Rewritten for TV-201 and TV-206: the old rule ignored seasons whose episodes had
        all aired. Every regular season with an aired episode must be watched to finish
        a show, so it moves to Watching with the new season's first episode up next,
        and still no season tracking is created for it (TV-205).
        """
        with FakeTVProvider() as fake, patch_now(datetime.datetime(2026, 10, 25, 12, 0)):
            self._finished_show(fake)
            fake.show(self.tv_item.media_id).set_season(2, [datetime.date(2010, 1, 20)])
            fake.show(self.tv_item.media_id).status = "Ended"

            events_bulk = []
            process_tv(self.tv_item, events_bulk)

        tv = TV.objects.get(item=self.tv_item, user=self.user)
        self.assertEqual(tv.status, Status.IN_PROGRESS.value)
        self.assertEqual((tv.up_next_season, tv.up_next_episode), (2, 1))
        self.assertFalse(
            Season.objects.filter(
                related_tv=tv,
                item__season_number=2,
            ).exists(),
        )
        self.assertEqual(len(events_bulk), 1)

    @patch("events.calendar.tv.services.api_request")
    def test_get_tvmaze_episode_map(self, mock_api_request):
        """Test get_tvmaze_episode_map function."""
        cache.clear()

        mock_api_request.side_effect = [
            {"id": 12345},
            {
                "_embedded": {
                    "episodes": [
                        {
                            "season": 1,
                            "number": 1,
                            "airstamp": "2008-01-20T22:00:00+00:00",
                            "airtime": "22:00",
                        },
                        {
                            "season": 1,
                            "number": 2,
                            "airstamp": "2008-01-27T22:00:00+00:00",
                            "airtime": "22:00",
                        },
                    ],
                },
            },
        ]

        result = get_tvmaze_episode_map("81189")

        self.assertEqual(len(result), 2)
        self.assertIn("1_1", result)
        self.assertIn("1_2", result)
        self.assertEqual(result["1_1"], "2008-01-20T22:00:00+00:00")
        self.assertEqual(result["1_2"], "2008-01-27T22:00:00+00:00")

        cached_result = cache.get("tvmaze_map_81189")
        self.assertEqual(cached_result, result)

        mock_api_request.reset_mock()
        get_tvmaze_episode_map("81189")
        mock_api_request.assert_not_called()

    @patch("events.calendar.tv.services.api_request")
    def test_get_tvmaze_episode_map_lookup_failure(self, mock_api_request):
        """Test get_tvmaze_episode_map when lookup fails."""
        cache.clear()
        mock_api_request.return_value = None

        result = get_tvmaze_episode_map("invalid_id")

        self.assertEqual(result, {})
        mock_api_request.assert_called_once()

    def test_get_episode_datetime_falls_back_to_tmdb_air_date(self):
        """TMDB dates should be used when TVMaze has no timestamp."""
        result = get_episode_datetime(
            {"air_date": "2025-01-31"},
            season_number=1,
            episode_number=2,
            tvmaze_map={},
        )

        self.assertEqual(result, date_parser("2025-01-31"))

    def test_get_episode_datetime_returns_placeholder_for_invalid_date(self):
        """Invalid or missing episode dates should produce a placeholder datetime."""
        result = get_episode_datetime(
            {"air_date": "not-a-date"},
            season_number=1,
            episode_number=2,
            tvmaze_map={},
        )

        self.assertEqual(result, datetime.datetime.min.replace(tzinfo=ZoneInfo("UTC")))

    @patch("events.calendar.tv.services.api_request")
    def test_get_tvmaze_response_returns_empty_on_not_found(self, mock_api_request):
        """A 404 from the TVMaze lookup should be tolerated."""
        response = MagicMock()
        response.status_code = requests.codes.not_found
        response.text = "missing"
        mock_api_request.side_effect = requests.exceptions.HTTPError(response=response)

        self.assertEqual(get_tvmaze_response("999"), {})

    @patch("events.calendar.tv.tmdb.tv")
    def test_get_seasons_to_process_returns_empty_when_no_seasons(self, mock_tv):
        """TV metadata without seasons should short-circuit processing."""
        mock_tv.return_value = {"related": {"seasons": []}}

        self.assertEqual(get_seasons_to_process(self.tv_item), [])

    @patch("events.calendar.tv.get_seasons_to_process")
    def test_process_tv_returns_when_no_seasons_need_processing(
        self,
        mock_get_seasons_to_process,
    ):
        """process_tv should stop cleanly when there is nothing new to fetch."""
        mock_get_seasons_to_process.return_value = []

        events_bulk = []
        process_tv(self.tv_item, events_bulk)

        self.assertEqual(events_bulk, [])

    def test_process_season_episodes_handles_missing_tvdb_and_episodes(self):
        """A season without TVDB data or episodes should not add events."""
        events_bulk = []
        process_season_episodes(
            self.season_item,
            {
                "season_number": 1,
                "episodes": [],
            },
            events_bulk,
        )

        self.assertEqual(events_bulk, [])

    @patch("events.calendar.tv.services.api_request")
    def test_get_tvmaze_response_returns_empty_when_lookup_has_no_id(
        self,
        mock_api_request,
    ):
        """Lookup responses without a TVMaze id should return an empty mapping."""
        mock_api_request.return_value = {"name": "Breaking Bad"}

        self.assertEqual(get_tvmaze_response("81189"), {})

    @patch("events.calendar.tv.services.api_request")
    def test_get_tvmaze_response_returns_empty_when_episode_fetch_fails(
        self,
        mock_api_request,
    ):
        """Episode fetch errors after a successful lookup should be tolerated."""
        response = MagicMock()
        response.status_code = 500
        response.text = "boom"
        mock_api_request.side_effect = [
            {"id": 12345},
            requests.exceptions.HTTPError(response=response),
        ]

        self.assertEqual(get_tvmaze_response("81189"), {})
