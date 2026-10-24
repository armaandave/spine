"""Fixed-size charts fail cleanly instead of shrinking (``adapters/_rows``).

The writer's drop guard only judges lists of 20 or more, so a partial source could
replace a top 10 with one row. Chart definitions set ``min_entries`` instead.
"""

from django.test import SimpleTestCase, override_settings

from lists.featured import engine, registry
from lists.featured.adapters import ADAPTERS, _rows
from lists.featured.registry import get
from lists.featured.types import AdapterError, FetchResult
from lists.tests.featured.helpers import (
    FeaturedTestCase,
    api_routes,
    entry,
    load_fixture,
    make_definition,
    no_overrides,
)
from lists.tests.featured.test_netflix import SEARCHES, tmdb_search

# Adapters whose source returns a chart of known size.
FIXED_SIZE_ADAPTERS = {
    "albums_generator",
    "apple_books",
    "apple_music",
    "hardcover_trending",
    "igdb_popularity",
    "listenbrainz",
    "mal_anime",
    "mal_manga",
    "mangaupdates_top",
    "netflix",
    "nielsen",
    "nyt",
    "nyt_overview",
    "official_charts",
    "openlibrary_trending",
    "steam",
    "tmdb_trending",
    "trakt",
    "twitch",
}
EXACT = {
    "netflix-top-10-films-english": 10,
    "netflix-top-10-tv-english": 10,
    "netflix-most-popular-tv": 10,
    "nielsen-streaming-top-10": 10,
    "nielsen-streaming-top-10-movies": 10,
    "trakt-weekend-box-office": 10,
    "trakt-trending-movies": 20,
    "tmdb-trending-tv-week": 20,
}


class ChartSizeRegistryTests(SimpleTestCase):
    def test_every_fixed_size_chart_sets_min_entries(self):
        for definition in registry.all_definitions():
            if definition.adapter not in FIXED_SIZE_ADAPTERS:
                continue
            with self.subTest(key=definition.key):
                self.assertGreater(definition.params.get("min_entries", 1), 1)

    def test_short_charts_expect_every_row(self):
        for key, size in EXACT.items():
            with self.subTest(key=key):
                self.assertEqual(get(key).params["min_entries"], size)


class RowsHelperTests(SimpleTestCase):
    def test_validate(self):
        for value in (0, -1, True, "10", 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _rows.validate(make_definition(params={"min_entries": value}))
        with self.assertRaisesMessage(ValueError, "must not exceed the limit"):
            _rows.validate(make_definition(params={"min_entries": 60}), limit=50)
        _rows.validate(make_definition(params={"min_entries": 50}), limit=50)
        _rows.validate(make_definition())  # optional: defaults to 1

    def test_require(self):
        definition = make_definition(params={"min_entries": 10})
        _rows.require(definition, 10, "The chart")
        with self.assertRaisesMessage(AdapterError, "The chart has 9 rows, fewer than the 10"):
            _rows.require(definition, 9, "The chart")
        _rows.require(make_definition(), 1, "A list without a minimum")


@override_settings(TRAKT_API="test-trakt-key")
class ShortChartTests(FeaturedTestCase):
    def test_netflix_week_with_fewer_than_ten_rows_fails(self):
        routes = {"all-weeks-global.tsv": load_fixture("netflix", "weekly.tsv"), "/search/": tmdb_search(SEARCHES)}
        with no_overrides(), api_routes(routes), self.assertRaisesMessage(AdapterError, "has 2 rows, fewer than the 10"):
            ADAPTERS["netflix"].fetch(get("netflix-top-10-films-english"))

    def test_netflix_most_popular_needs_ten_per_category(self):
        routes = {"most-popular.tsv": load_fixture("netflix", "most-popular.tsv"), "/search/": tmdb_search({})}
        with no_overrides(), api_routes(routes), self.assertRaisesMessage(AdapterError, "fewer than the 10"):
            ADAPTERS["netflix"].fetch(get("netflix-most-popular-tv"))

    def test_nielsen_short_table_fails_before_any_tmdb_search(self):
        with api_routes({"nielsen.com/data-center/top-ten": load_fixture("nielsen", "top10.html")}):
            with self.assertRaisesMessage(AdapterError, "Nielsen's movies table has 2 rows, fewer than the 10"):
                ADAPTERS["nielsen"].fetch(get("nielsen-streaming-top-10-movies"))

    def test_trakt_short_box_office_fails(self):
        with api_routes({"api.trakt.tv/movies/boxoffice": load_fixture("trakt", "boxoffice.json")}):
            with self.assertRaisesMessage(AdapterError, "Trakt's boxoffice chart has 3 rows"):
                ADAPTERS["trakt"].fetch(get("trakt-weekend-box-office"))

    def test_a_short_source_leaves_the_ten_row_list_untouched(self):
        definition = get("nielsen-streaming-top-10-movies")
        full = [
            entry(rank, f"Movie {rank}", media_type="movie", ids={"tmdb": str(rank)}, image=f"https://x/{rank}.jpg")
            for rank in range(1, 11)
        ]
        first = self.run_list(definition, fetch_result=FetchResult(entries=full))
        self.assertTrue(first.ok, first.message)

        page = {"nielsen.com/data-center/top-ten": load_fixture("nielsen", "top10.html")}
        with api_routes(page), registry.override_definitions([definition]):
            second = engine.run(definition, self.owner, trigger="test")
        self.assertEqual(second.status, "failed")
        self.assertIn("fewer than the 10", second.message)
        self.assertListRefs(first.custom_list, [f"tmdb:movie:{rank}" for rank in range(1, 11)])
