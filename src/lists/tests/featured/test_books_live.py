"""Book, comic and manga live adapters: parsing, ids, editions and failures (no network)."""

from unittest.mock import patch

import requests
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from lists.featured import registry
from lists.featured.adapters import ADAPTERS, apple_books, nyt_overview, read_with_jenna, reeses_book_club
from lists.featured.definitions import books_live
from lists.featured.types import AdapterError
from lists.tests.featured.helpers import api_routes, few_rows, load_fixture


def http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"{status} error", response=response)


class DefinitionTests(SimpleTestCase):
    def test_positions_stay_in_the_family_ranges(self):
        for definition in books_live.DEFINITIONS:
            with self.subTest(definition.key):
                if definition.media_types == ("book",):
                    self.assertTrue(340 <= definition.position <= 369)
                else:
                    self.assertEqual(definition.media_types, ("manga",))
                    self.assertTrue(460 <= definition.position <= 479)
                self.assertTrue(definition.source_name and definition.source_url)
                self.assertEqual(registry.definition_problems(definition), [])

    def test_nyt_overview_covers_every_active_chart_but_the_headline_five(self):
        headline = {item.key for item in registry.all_definitions() if item.headline and item.adapter == "nyt"}
        overview = {item.key for item in books_live.NYT}
        self.assertEqual(len(headline | overview), 19)
        self.assertFalse(headline & overview)
        self.assertEqual(registry.get("nyt-hardcover-fiction").identity, ("nyt", "hardcover-fiction"))


@override_settings(NYT_BOOKS_API_KEY="test-nyt-key")
class NYTOverviewTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def fetch(self, slug):
        return ADAPTERS["nyt_overview"].fetch(few_rows(registry.get(f"nyt-{slug}")))

    def test_weekly_chart_isbns_notes_and_edition(self):
        with api_routes({"/lists/overview.json": load_fixture("nyt_overview", "overview.json")}) as mocked:
            result = self.fetch("hardcover-fiction")
        self.assertEqual(result.edition, "Week of Nov 1, 2026")
        first, last = result.entries
        self.assertEqual((first.rank, first.title, first.isbns, first.note), (1, "THRESHING DAY", ("9781682818084",), "▲1"))
        self.assertEqual(last.creator, "Wm. Paul Young")  # NYT truncates the author to "Wm"
        self.assertEqual(last.note, "▼6")
        self.assertEqual(mocked.call_args.kwargs["params"], {"api-key": "test-nyt-key"})

    def test_one_overview_request_serves_every_chart(self):
        with api_routes({"/lists/overview.json": load_fixture("nyt_overview", "overview.json")}) as mocked:
            self.fetch("hardcover-fiction")
            monthly = self.fetch("audio-childrens")
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(monthly.edition, "October 2026")
        self.assertEqual(monthly.entries[0].title, "HARRY POTTER AND THE SORCERER’S STONE")
        self.assertEqual(monthly.entries[0].note, "▲4")

    def test_monthly_notes_say_month(self):
        self.assertEqual(nyt_overview._note({"weeks_on_list": 1}, 3, monthly=True), "New this month")
        self.assertEqual(nyt_overview._note({"weeks_on_list": 4, "rank_last_week": 3}, 3, monthly=True), "4 months on the list")

    def test_vendor_ids_are_not_isbns(self):
        with api_routes({"/lists/overview.json": load_fixture("nyt_overview", "overview.json")}):
            entry = self.fetch("audio-fiction").entries[0]
        self.assertEqual(entry.isbns, ())
        self.assertTrue(entry.key.startswith("t:the infinite extent"))

    def test_missing_chart_and_errors_are_sanitized(self):
        with api_routes({"/lists/overview.json": load_fixture("nyt_overview", "overview.json")}):
            with self.assertRaisesRegex(AdapterError, "no picture-books chart"):
                self.fetch("picture-books")
        cache.clear()
        error = requests.HTTPError("https://api.nytimes.com/x?api-key=test-nyt-key", response=requests.Response())
        error.response.status_code = 429
        with api_routes({"/lists/overview.json": error}), self.assertRaises(AdapterError) as raised:
            self.fetch("hardcover-fiction")
        self.assertNotIn("test-nyt-key", str(raised.exception))

    @override_settings(NYT_BOOKS_API_KEY="")
    def test_needs_a_key(self):
        with self.assertRaisesRegex(AdapterError, "not configured"):
            self.fetch("hardcover-fiction")

    def test_validate(self):
        definition = registry.get("nyt-hardcover-fiction")
        nyt_overview.validate(definition)
        with self.assertRaises(ValueError):
            nyt_overview.validate(registry.get("mal-top-manga"))


class AppleBooksTests(SimpleTestCase):
    def fetch(self, routes):
        with api_routes(routes), patch.object(apple_books, "RETRY_SECONDS", 0):
            return ADAPTERS["apple_books"].fetch(few_rows(registry.get("apple-books-top-paid")))

    def test_cover_isbns_titles_and_artwork(self):
        result = self.fetch({"/us/books/top-paid/50/books.json": load_fixture("apple_books", "top_paid.json")})
        self.assertEqual(result.edition, "Oct 22, 2026")
        chain, other, audio = result.entries
        self.assertEqual((chain.title, chain.creator, chain.isbns), ("Chain Reaction", "Lee Child & Andrew Child", ("9798217375165",)))
        self.assertTrue(chain.image.endswith("/600x600bb.jpg"))
        self.assertEqual(other.isbns, ())  # "Her-Last-Kiss-Apple.jpg" is not an ISBN
        self.assertEqual(audio.title, "Chain Reaction")  # ": A Reacher Novel (Unabridged)" stripped

    def test_retries_gateway_errors_then_fails_cleanly(self):
        calls = []

        def flaky(url, params):
            calls.append(url)
            if len(calls) == 1:
                raise http_error(504)
            return load_fixture("apple_books", "top_paid.json")

        self.assertEqual(len(self.fetch({"top-paid": flaky}).entries), 3)
        with self.assertRaisesRegex(AdapterError, "HTTP 502"):
            self.fetch({"top-paid": http_error(502)})
        with self.assertRaisesRegex(AdapterError, "empty chart"):
            self.fetch({"top-paid": {"feed": {"results": []}}})

    def test_title_and_isbn_helpers(self):
        self.assertEqual(apple_books.isbn13("9781250412584"), "9781250412584")
        self.assertEqual(apple_books.isbn13("9781250412585"), "")  # bad check digit
        self.assertEqual(apple_books.isbn13("6610001437619"), "")  # not 978/979
        self.assertEqual(apple_books.clean_title("Dungeon Crawler Carl: Dungeon Crawler Carl, Book 1 (Unabridged)"), "Dungeon Crawler Carl")
        self.assertEqual(apple_books.clean_title("Seducing Mr Remington - Spicy Billionaire Romance"), "Seducing Mr Remington")
        self.assertEqual(apple_books.clean_title("The Let Them Theory: A Life-Changing Tool"), "The Let Them Theory: A Life-Changing Tool")

    def test_validate(self):
        apple_books.validate(registry.get("apple-books-top-audiobooks"))
        with self.assertRaises(ValueError):
            apple_books.validate(registry.get("hardcover-trending"))


@override_settings(HARDCOVER_API="Bearer test-hardcover")
class HardcoverTrendingTests(SimpleTestCase):
    def test_ranked_ids_with_metadata(self):
        def respond(url, params):
            name = "trending.json" if "books_trending" in params["query"] else "books.json"
            return load_fixture("hardcover_trending", name)

        with api_routes({"hardcover.app": respond}) as mocked:
            result = ADAPTERS["hardcover_trending"].fetch(few_rows(registry.get("hardcover-trending")))
        self.assertIn("duration: month", mocked.call_args_list[0].kwargs["params"]["query"])
        self.assertEqual(mocked.call_args_list[1].kwargs["params"]["variables"], {"ids": [446681, 2769384, 427578]})
        # 2769384 is missing from the books response (merged), so it is skipped; ranks keep their places.
        self.assertEqual([(entry.rank, entry.key) for entry in result.entries], [(1, "hardcover:446681"), (3, "hardcover:427578")])
        self.assertEqual((result.entries[1].creator, result.entries[1].year), ("Andy Weir", 2021))

    def test_graphql_errors(self):
        with api_routes({"hardcover.app": {"errors": [{"message": "bad"}]}}), self.assertRaises(AdapterError):
            ADAPTERS["hardcover_trending"].fetch(few_rows(registry.get("hardcover-trending")))
        with api_routes({"hardcover.app": {"data": {"books_trending": {"ids": [], "error": None}}}}):
            with self.assertRaisesRegex(AdapterError, "no trending"):
                ADAPTERS["hardcover_trending"].fetch(few_rows(registry.get("hardcover-trending")))


class OpenLibraryTrendingTests(SimpleTestCase):
    def test_works_become_editions(self):
        with api_routes({"/trending/daily.json": load_fixture("openlibrary_trending", "daily.json")}) as mocked:
            result = ADAPTERS["openlibrary_trending"].fetch(few_rows(registry.get("openlibrary-trending-daily")))
        self.assertEqual(mocked.call_args.kwargs["params"], {"limit": 50})
        cover, first_edition, none = result.entries
        self.assertEqual(cover.ids, {"olid": "OL36647151M"})
        self.assertEqual((cover.creator, cover.year), ("James Clear", 2016))
        self.assertTrue(cover.image.startswith("https://covers.openlibrary.org/b/id/"))
        self.assertTrue(first_edition.ids["olid"].endswith("M"))  # no cover edition: first listed edition
        self.assertEqual((none.ids, none.image), ({}, ""))

    def test_empty_response_fails(self):
        with api_routes({"/trending/weekly.json": {"works": []}}), self.assertRaises(AdapterError):
            ADAPTERS["openlibrary_trending"].fetch(few_rows(registry.get("openlibrary-trending-weekly")))


@override_settings(MAL_API="test-mal", MAL_NSFW=False)
class MALMangaTests(SimpleTestCase):
    def test_ranking_with_mal_ids(self):
        with api_routes({"/manga/ranking": load_fixture("mal_manga", "ranking_manga.json")}) as mocked:
            result = ADAPTERS["mal_manga"].fetch(few_rows(registry.get("mal-top-manga")))
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["headers"]["X-MAL-CLIENT-ID"], "test-mal")
        self.assertEqual((kwargs["params"]["ranking_type"], kwargs["params"]["limit"]), ("manga", 50))
        self.assertNotIn("nsfw", kwargs["params"])
        berserk = result.entries[0]
        self.assertEqual((berserk.rank, berserk.key, berserk.year), (1, "mal:manga:2", 1989))
        self.assertEqual(berserk.creator, "Kentarou Miura, Studio Gaga")
        self.assertTrue(berserk.image.startswith("https://cdn.myanimelist.net/"))

    def test_errors(self):
        with api_routes({"/manga/ranking": http_error(403)}), self.assertRaisesRegex(AdapterError, "HTTP 403"):
            ADAPTERS["mal_manga"].fetch(few_rows(registry.get("mal-most-popular-manga")))


@override_settings(MAL_NSFW=False)
class MangaUpdatesTopTests(SimpleTestCase):
    def test_rating_order_with_ids(self):
        with api_routes({"/series/search": load_fixture("mangaupdates_top", "search_rating.json")}) as mocked:
            result = ADAPTERS["mangaupdates_top"].fetch(few_rows(registry.get("mangaupdates-top-rated")))
        body = mocked.call_args.kwargs["params"]
        self.assertEqual((body["orderby"], body["type"], body["perpage"]), ("rating", ["Manga"], 50))
        self.assertIn("Hentai", body["exclude_genre"])
        self.assertEqual([entry.key for entry in result.entries], ["mangaupdates:51239621230", "mangaupdates:55665151734"])
        self.assertEqual(result.entries[0].year, 1989)


class ReesesBookClubTests(SimpleTestCase):
    def test_picks_newest_first_with_authors_and_labels(self):
        page = load_fixture("reeses_book_club", "our-picks.html")
        with api_routes({"reesesbookclub.com": lambda url, params: page}), patch.object(reeses_book_club, "MIN_PICKS", 2):
            result = ADAPTERS["reeses_book_club"].fetch(few_rows(registry.get("reeses-book-club")))
        self.assertEqual(
            [(entry.rank, entry.title, entry.creator, entry.note) for entry in result.entries],
            [
                (None, "Sophie, Standing There", "Meg Mason", ""),
                (None, "Big Little Truths", "Liane Moriarty", ""),
                (None, "The Wild Beneath", "Kelly Anderson", "August ’26 Pick"),
            ],
        )
        self.assertEqual(result.edition, "")  # the newest pick has no label yet

    def test_a_changed_layout_fails_instead_of_emptying_the_list(self):
        with api_routes({"reesesbookclub.com": lambda url, params: "<html></html>"}), self.assertRaises(AdapterError):
            ADAPTERS["reeses_book_club"].fetch(few_rows(registry.get("reeses-book-club")))


class ReadWithJennaTests(SimpleTestCase):
    def test_months_titles_authors_and_link_isbns(self):
        page = load_fixture("read_with_jenna", "list.html")
        with api_routes({"today.com": lambda url, params: page}), patch.object(read_with_jenna, "MIN_PICKS", 2):
            result = ADAPTERS["read_with_jenna"].fetch(few_rows(registry.get("read-with-jenna")))
        self.assertEqual(result.edition, "October 2026")
        self.assertEqual(
            [(entry.note, entry.title, entry.creator, entry.isbns) for entry in result.entries],
            [
                ("October 2026", "Partita", "Barbara Kingsolver", ("0063577542",)),
                ("July 2026", "The Shampoo Effect", "Jenny Jackson", ()),  # a Kindle ASIN, not an ISBN
                ("January 2023", "Sam", "Allegra Goodman", ()),  # author from the Bookshop.org slug
                ("December 2020", "The Bluest Eye", "Toni Morrison", ()),
            ],
        )

    def test_link_helpers(self):
        redirect = "https://ncls1.com/irk?o=1&amp;d=https%3A%2F%2Fbookshop.org%2Fbooks%2Fthe-family-9781432895556%2F9780525541998"
        self.assertEqual(read_with_jenna.link_isbn(redirect), "9780525541998")
        self.assertEqual(read_with_jenna.link_isbn("https://bookshop.org/p/books/x/1?ean=9781524798673"), "9781524798673")
        self.assertEqual(read_with_jenna.isbn10("0063577542"), "0063577542")
        self.assertEqual(read_with_jenna.isbn10("1064696121"), "")
        self.assertEqual(read_with_jenna.split_title('"Black Buck," by Mateo Askaripour'), ("Black Buck", "Mateo Askaripour"))
        self.assertEqual(read_with_jenna.split_title("The Secret History"), ("The Secret History", ""))
