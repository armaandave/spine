import copy
from datetime import date
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from lists.featured.adapters import ADAPTERS, trakt
from lists.featured.registry import get
from lists.featured.types import AdapterError
from lists.tests.featured.helpers import api_routes, few_rows, load_fixture, make_definition


@override_settings(TRAKT_API="test-trakt-key")
class TraktAdapterTests(SimpleTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)

    def test_box_office_sends_trakt_headers_and_ids(self):
        with api_routes({"api.trakt.tv/movies/boxoffice": load_fixture("trakt", "boxoffice.json")}) as api:
            result = ADAPTERS["trakt"].fetch(few_rows(get("trakt-weekend-box-office")))
        headers = api.call_args.kwargs["headers"]
        self.assertEqual((headers["trakt-api-key"], headers["trakt-api-version"]), ("test-trakt-key", "2"))
        self.assertIsNone(api.call_args.kwargs["params"])
        first, _, third = result.entries
        self.assertEqual((first.rank, first.title, first.year, first.media_type), (1, "Street Fighter", 2026, "movie"))
        self.assertEqual(first.ids, {"tmdb": "1153576", "imdb": "tt27419420"})
        self.assertEqual(first.note, "$31.0M")
        self.assertEqual((third.ids, third.note), ({"tmdb": "1400837"}, "$850K"))
        self.assertTrue(result.edition.startswith("Weekend of "))

    def test_trending_shows(self):
        with api_routes({"api.trakt.tv/shows/trending": load_fixture("trakt", "trending_shows.json")}) as api:
            result = ADAPTERS["trakt"].fetch(few_rows(get("trakt-trending-shows")))
        self.assertEqual(api.call_args.kwargs["params"], {"limit": 20})
        self.assertEqual([entry.key for entry in result.entries], ["tmdb:tv:213375", "tmdb:tv:95480"])
        self.assertEqual((result.entries[0].note, result.edition), ("", ""))

    def test_weekend_label_is_the_last_finished_weekend(self):
        self.assertEqual(trakt.weekend_label(date(2026, 10, 21)), "Weekend of Oct 16, 2026")  # Wednesday
        self.assertEqual(trakt.weekend_label(date(2026, 10, 19)), "Weekend of Oct 16, 2026")  # Monday
        self.assertEqual(trakt.weekend_label(date(2026, 10, 18)), "Weekend of Oct 9, 2026")  # Sunday: still running

    def box_office_on(self, day, rows):
        with (
            patch.object(trakt.timezone, "localdate", return_value=day),
            api_routes({"api.trakt.tv/movies/boxoffice": rows}),
        ):
            return ADAPTERS["trakt"].fetch(few_rows(get("trakt-weekend-box-office"))).edition

    def test_box_office_label_follows_the_chart_around_mondays(self):
        last_week = load_fixture("trakt", "boxoffice.json")
        this_week = copy.deepcopy(last_week)
        this_week[0]["revenue"] = 12_000_000
        self.assertEqual(self.box_office_on(date(2026, 10, 21), last_week), "Weekend of Oct 16, 2026")
        # Monday before Trakt's update: the same chart keeps last weekend's label.
        self.assertEqual(self.box_office_on(date(2026, 10, 26), last_week), "Weekend of Oct 16, 2026")
        # Once the chart changes it is the weekend that just ended.
        self.assertEqual(self.box_office_on(date(2026, 10, 26), this_week), "Weekend of Oct 23, 2026")
        self.assertEqual(self.box_office_on(date(2026, 10, 28), this_week), "Weekend of Oct 23, 2026")

    def test_failures(self):
        with api_routes({"api.trakt.tv": []}), self.assertRaisesMessage(AdapterError, "no movies"):
            ADAPTERS["trakt"].fetch(few_rows(get("trakt-weekend-box-office")))
        with override_settings(TRAKT_API=""), self.assertRaisesMessage(AdapterError, "TRAKT_API"):
            ADAPTERS["trakt"].fetch(few_rows(get("trakt-weekend-box-office")))

    def test_validate(self):
        bad = (
            ({"chart": "popular"}, ("movie",)),
            ({"chart": "trending", "media": "people"}, ("movie",)),
            ({"chart": "boxoffice", "media": "shows"}, ("tv",)),
            ({"chart": "trending", "media": "shows"}, ("movie",)),
            ({"chart": "trending", "media": "movies", "limit": 500}, ("movie",)),
        )
        for params, media_types in bad:
            with self.subTest(params=params), self.assertRaises(ValueError):
                trakt.validate(make_definition(adapter="trakt", params=params, media_types=media_types))
