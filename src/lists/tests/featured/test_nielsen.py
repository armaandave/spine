from lists.featured.adapters import ADAPTERS, nielsen
from lists.featured.registry import get
from lists.featured.types import AdapterError
from lists.tests.featured.helpers import FeaturedTestCase, api_routes, load_fixture, make_definition, no_overrides
from lists.tests.featured.test_netflix import result, tmdb_search

SEARCHES = {
    ("tv", "wonka's the golden ticket"): [result(286940, "Wonka's The Golden Ticket", "2026-09-23")],
    ("tv", "east of eden"): [result(258165, "East of Eden", "2026-10-01"), result(5, "East of Eden", "1981-02-08")],
    ("movie", "toy story 5"): [result(1084244, "Toy Story 5", "2026-06-17")],
    ("movie", "the breadwinner"): [
        result(1440050, "The Breadwinner", "2026-05-29", popularity=100.0),
        result(435129, "The Breadwinner", "2017-11-17", popularity=200.0),
    ],
}


class NielsenTests(FeaturedTestCase):
    def fetch(self, key):
        routes = {"nielsen.com/data-center/top-ten": load_fixture("nielsen", "top10.html"), "/search/": tmdb_search(SEARCHES)}
        with no_overrides(), api_routes(routes):
            return ADAPTERS["nielsen"].fetch(get(key))

    def test_overall_chart_sets_media_types_per_entry(self):
        fetched = self.fetch("nielsen-streaming-top-10")
        self.assertEqual(fetched.edition, "Week of Sep 28, 2026")
        self.assertEqual(
            [(entry.rank, entry.title, entry.media_type, entry.key) for entry in fetched.entries],
            [
                (1, "Wonka's The Golden Ticket", "tv", "tmdb:tv:286940"),
                (2, "East of Eden", "tv", "tmdb:tv:258165"),  # "(2026)" narrows the year
                (3, "Toy Story 5", "movie", "tmdb:movie:1084244"),  # one episode: tried as a film first
            ],
        )

    def test_movies_chart(self):
        fetched = self.fetch("nielsen-streaming-top-10-movies")
        self.assertEqual([entry.key for entry in fetched.entries], ["tmdb:movie:1084244", "tmdb:movie:1440050"])

    def test_page_without_streaming_tables_raises(self):
        with api_routes({"nielsen.com": "<html><table><tr><th>Program</th></tr></table></html>"}):
            with self.assertRaisesMessage(AdapterError, "no streaming overall table"):
                ADAPTERS["nielsen"].fetch(get("nielsen-streaming-top-10"))

    def test_validate(self):
        with self.assertRaises(ValueError):
            nielsen.validate(make_definition(adapter="nielsen", params={"chart": "original"}))
        with self.assertRaises(ValueError):
            nielsen.validate(make_definition(adapter="nielsen", params={"chart": "overall"}))  # movie only
