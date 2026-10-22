from django.test import SimpleTestCase

from lists.featured.adapters import ADAPTERS, tmdb_trending
from lists.featured.registry import get
from lists.featured.types import AdapterError
from lists.tests.featured.helpers import api_routes, few_rows, load_fixture, make_definition


class TMDBTrendingTests(SimpleTestCase):
    def test_trending_movies_this_week(self):
        with api_routes({"/trending/movie/week": load_fixture("tmdb_trending", "movie_week.json")}) as api:
            result = ADAPTERS["tmdb_trending"].fetch(few_rows(get("tmdb-trending-movies-week")))
        self.assertIn("api_key", api.call_args.kwargs["params"])
        first, second = result.entries
        self.assertEqual((first.rank, first.title, first.year, first.key), (1, "Clayface", 2026, "tmdb:movie:1400940"))
        self.assertEqual(first.image, "https://image.tmdb.org/t/p/w500/clayface.jpg")
        self.assertEqual(second.image, "")  # no poster: the writer fetches metadata

    def test_failures_and_validation(self):
        with api_routes({"/trending/": {"results": []}}), self.assertRaisesMessage(AdapterError, "no trending"):
            ADAPTERS["tmdb_trending"].fetch(few_rows(get("tmdb-trending-tv-week")))
        for params in ({"media_type": "person"}, {"media_type": "movie", "window": "month"}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                tmdb_trending.validate(make_definition(adapter="tmdb_trending", params=params))
        with self.assertRaises(ValueError):
            tmdb_trending.validate(make_definition(adapter="tmdb_trending", params={"media_type": "tv"}))
