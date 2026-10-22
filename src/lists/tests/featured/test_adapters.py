from unittest.mock import patch

import requests
from django.test import SimpleTestCase, override_settings

from lists.featured.adapters import ADAPTERS
from lists.featured.adapters import mdblist as mdblist_adapter
from lists.featured.adapters import nyt as nyt_adapter
from lists.featured.adapters import static as static_adapter
from lists.featured.helpers import get_json, sanitize
from lists.featured.types import AdapterError
from lists.tests.featured.helpers import FIXTURES, api_routes, load_fixture, make_definition


def mdblist_definition(**params):
    return make_definition(key="mdblist-test", adapter="mdblist", params=params, media_types=("movie", "tv"))


@override_settings(MDBLIST_API="test-mdblist-key")
class MDBListAdapterTests(SimpleTestCase):
    def test_discovers_the_foundation_adapters(self):
        self.assertTrue({"mdblist", "nyt", "static"} <= set(ADAPTERS))

    def test_external_list_by_id(self):
        routes = {
            "/external/lists/155837/items": load_fixture("mdblist", "external_list_items.json"),
            "/external/lists/155837": load_fixture("mdblist", "external_list.json"),
        }
        with api_routes(routes) as api:
            result = ADAPTERS["mdblist"].fetch(mdblist_definition(external="155837"))

        self.assertEqual(result.name, "Letterboxd's Top 500 Films")
        self.assertEqual(result.expected_count, 500)
        first = result.entries[0]
        self.assertEqual((first.rank, first.title, first.year, first.media_type), (1, "Harakiri", 1962, "movie"))
        self.assertEqual(first.ids, {"tmdb": "14537", "imdb": "tt0056058"})
        self.assertEqual(first.key, "tmdb:movie:14537")
        self.assertEqual(first.image, "https://image.tmdb.org/t/p/w500/5konZnIbcAxZjP616Cz5o9bKEfW.jpg")
        self.assertEqual(api.call_args_list[0].kwargs["params"]["apikey"], "test-mdblist-key")

    def test_external_list_by_letterboxd_url_matches_our_account(self):
        user_lists = [
            {"id": 1, "url": "official/list/other", "name": "Other", "items": 1},
            *load_fixture("mdblist", "external_lists_user.json"),
        ]
        routes = {
            "/external/lists/user": user_lists,
            "/external/lists/155837/items": load_fixture("mdblist", "external_list_items.json"),
        }
        definition = mdblist_definition(external="https://letterboxd.com/official/list/letterboxds-top-500-films/")
        with api_routes(routes):
            result = ADAPTERS["mdblist"].fetch(definition)
        self.assertEqual(result.expected_count, 500)
        self.assertEqual(len(result.entries), 3)

        with api_routes({"/external/lists/user": user_lists}), self.assertRaisesMessage(AdapterError, "No MDBList external list"):
            ADAPTERS["mdblist"].fetch(mdblist_definition(external="https://letterboxd.com/official/list/missing/"))

    def test_public_list_ranks_are_renumbered(self):
        routes = {
            "/lists/101881/items": load_fixture("mdblist", "public_list_items.json"),
            "/lists/101881": load_fixture("mdblist", "public_list.json"),
        }
        with api_routes(routes):
            result = ADAPTERS["mdblist"].fetch(mdblist_definition(list_id="101881"))
        self.assertEqual([entry.rank for entry in result.entries], [1, 2, 3])
        self.assertEqual(result.entries[0].title, "The Shawshank Redemption")
        self.assertEqual(result.expected_count, 250)
        self.assertEqual(result.name, "Top 250 Movies (iMDB)")

    def test_official_list_with_rank_changes_and_limit(self):
        with api_routes({"/lists/official/justwatch-streaming-charts/items": load_fixture("mdblist", "official_list_items_show.json")}) as api:
            result = ADAPTERS["mdblist"].fetch(
                mdblist_definition(official="justwatch-streaming-charts", mediatype="show", limit=2),
            )
        self.assertEqual(api.call_args.kwargs["params"]["mediatype"], "show")
        self.assertEqual([entry.media_type for entry in result.entries], ["tv", "tv"])
        self.assertEqual([entry.note for entry in result.entries], ["▲23", "▼1"])
        self.assertIsNone(result.expected_count)

    def test_follows_cursor_pagination(self):
        first = {"movies": [{"id": 1, "rank": 1, "title": "A"}], "shows": [], "pagination": {"next_cursor": "c2"}}
        second = {"movies": [{"id": 2, "rank": 2, "title": "B"}], "shows": []}
        pages = iter([first, second])
        with api_routes({"/lists/official/x/items": lambda url, params: next(pages)}) as api:
            result = ADAPTERS["mdblist"].fetch(mdblist_definition(official="x", mediatype="movie"))
        self.assertEqual([entry.title for entry in result.entries], ["A", "B"])
        self.assertEqual(api.call_args_list[1].kwargs["params"]["cursor"], "c2")

    def test_validate(self):
        validate = ADAPTERS["mdblist"].validate
        validate(mdblist_definition(external="155837"))
        validate(mdblist_definition(list_id="101881"))
        validate(mdblist_definition(official="moviemeter", mediatype="movie"))
        for params, message in (
            ({}, "exactly one"),
            ({"external": "1", "list_id": "2"}, "exactly one"),
            ({"external": "abc"}, "MDBList list ID or a URL"),
            ({"list_id": "x"}, "numeric"),
            ({"official": "moviemeter"}, "mediatype"),
        ):
            with self.subTest(params), self.assertRaisesMessage(ValueError, message):
                validate(mdblist_definition(**params))
        with self.assertRaisesMessage(ValueError, "movies and TV only"):
            validate(make_definition(adapter="mdblist", params={"list_id": "1"}, media_types=("book",)))

    def test_poster_sizes(self):
        self.assertEqual(mdblist_adapter.poster_url({"poster_path": "/a.jpg"}), "https://image.tmdb.org/t/p/original/a.jpg")
        self.assertEqual(
            mdblist_adapter.poster_url({"poster": "https://image.tmdb.org/t/p/w200/a.jpg"}),
            "https://image.tmdb.org/t/p/w500/a.jpg",
        )


@override_settings(NYT_BOOKS_API_KEY="test-nyt-key")
class NYTAdapterTests(SimpleTestCase):
    def definition(self):
        return make_definition(
            key="nyt-combined-print-and-e-book-fiction",
            adapter="nyt",
            media_types=("book",),
            position=300,
            params={"slug": "combined-print-and-e-book-fiction", "fallback_name": "Fiction"},
        )

    def test_chart_becomes_isbn_entries(self):
        with api_routes({"/lists/current/combined-print-and-e-book-fiction.json": load_fixture("nyt", "current_fiction.json")}):
            result = ADAPTERS["nyt"].fetch(self.definition())

        self.assertEqual(result.edition, "Week of Oct 25, 2026")
        self.assertEqual(result.description, "The New York Times Best Sellers list published 2026-10-25.")
        self.assertEqual(result.name, "Combined Print & E-Book Fiction")
        first, second, third = result.entries
        self.assertEqual((first.rank, first.title, first.creator), (1, "ONYX STORM", "Rebecca Yarros"))
        self.assertEqual(first.isbns, ("9780593798430", "9781649376879", "0593798430", "1649376871"))
        self.assertEqual(first.note, "New this week")
        self.assertEqual(second.note, "▼1")
        self.assertEqual(third.note, "40 weeks on the list")
        self.assertEqual(first.key, "isbn:9780593798430")

    def test_provider_errors_are_sanitized(self):
        response = requests.Response()
        response.status_code = 429
        error = requests.HTTPError("https://api.nytimes.com/x?api-key=test-nyt-key", response=response)
        with api_routes({"/lists/current/": error}), self.assertRaises(AdapterError) as raised:
            ADAPTERS["nyt"].fetch(self.definition())
        self.assertIn("HTTP 429", str(raised.exception))
        self.assertNotIn("test-nyt-key", str(raised.exception))

    def test_validate(self):
        ADAPTERS["nyt"].validate(self.definition())
        with self.assertRaisesMessage(ValueError, "slug"):
            ADAPTERS["nyt"].validate(make_definition(adapter="nyt", media_types=("book",), position=300))

    def test_edition_label_tolerates_missing_dates(self):
        self.assertEqual(nyt_adapter.edition_label(""), "")
        self.assertEqual(nyt_adapter.edition_label("2026-10-01", monthly=True), "October 2026")


class StaticAdapterTests(SimpleTestCase):
    def test_fetch_reads_the_file(self):
        definition = make_definition(adapter="static", params={"file": "sample-canon.csv"}, cadence=None)
        with patch.object(static_adapter, "DIRECTORY", FIXTURES / "static"):
            result = ADAPTERS["static"].fetch(definition)
        self.assertEqual([entry.ids["ref"] for entry in result.entries], ["tmdb:movie:15", "tmdb:movie:426", "tmdb:movie:18148"])

    def test_missing_file_is_an_adapter_error(self):
        definition = make_definition(adapter="static", params={"file": "missing.csv"}, cadence=None)
        with self.assertRaises(AdapterError):
            ADAPTERS["static"].fetch(definition)


@override_settings(MDBLIST_API="secret-mdblist-value")
class HelperTests(SimpleTestCase):
    def test_sanitize_removes_configured_keys_and_keyed_urls(self):
        message = sanitize("GET https://x.test/?apikey=abc123&q=1 secret-mdblist-value Bearer tok.en-1")
        self.assertNotIn("abc123", message)
        self.assertNotIn("secret-mdblist-value", message)
        self.assertNotIn("tok.en-1", message)
        self.assertIn("q=1", message)

    def test_get_json_sends_user_agent_and_wraps_errors(self):
        with api_routes({"example.test": {"ok": True}}) as api:
            self.assertEqual(get_json("example", "https://example.test/a"), {"ok": True})
        self.assertIn("Spine/1.0", api.call_args.kwargs["headers"]["User-Agent"])
        with api_routes({"example.test": requests.ConnectionError("boom")}), self.assertRaisesMessage(AdapterError, "network error"):
            get_json("example", "https://example.test/a")
