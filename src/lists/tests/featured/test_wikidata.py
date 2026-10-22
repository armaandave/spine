from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests
from django.core.cache.backends.locmem import LocMemCache
from django.test import SimpleTestCase, override_settings

from lists.featured import registry
from lists.featured.adapters import ADAPTERS
from lists.featured.adapters import nobel as nobel_adapter
from lists.featured.adapters import wikidata as wd
from lists.featured.definitions import wikidata as definitions
from lists.featured.types import AdapterError
from lists.tests.featured.helpers import load_fixture, make_definition

SUPPLEMENT_HEADER = "list,year,qid,title,creator,published,ref,why\n"


def http_error(status):
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"{status} error", response=response)


@contextmanager
def wikidata_routes(main, types=None, *, hardcover=None, openlibrary=None):
    """Patch ``services.api_request``: SPARQL by query text, Hardcover by ids, OL by URL."""
    queries = []

    def respond(provider, method, url, params=None, data=None, **kwargs):  # noqa: ARG001
        if "query.wikidata.org" in url:
            query = data["query"]
            queries.append(query)
            if "?type wdt:P279*" in query:
                return types or {"results": {"bindings": []}}
            return main(query) if callable(main) else main
        if "api.hardcover.app" in url:
            ids = params["variables"]["ids"]
            return (hardcover or {}).get(ids[0].split("/")[1], {"data": {"book_mappings": []}})
        if "openlibrary.org" in url:
            for fragment, value in (openlibrary or {}).items():
                if fragment in url:
                    if isinstance(value, BaseException):
                        raise value
                    return value
        msg = f"Unexpected {method} {url}"
        raise AssertionError(msg)

    with patch("app.providers.services.api_request", side_effect=respond) as mocked:
        mocked.queries = queries
        yield mocked


@contextmanager
def supplement(body):
    with TemporaryDirectory() as directory:
        path = Path(directory, "wikidata.csv")
        path.write_text(SUPPLEMENT_HEADER + body, encoding="utf-8")
        wd._supplement.cache_clear()
        with patch.object(wd, "SUPPLEMENT", path):
            try:
                yield path
            finally:
                wd._supplement.cache_clear()
    wd._supplement.cache_clear()


def award_definition(**fields):
    values = {
        "key": "wikidata-test",
        "adapter": "wikidata",
        "kind": "award",
        "is_ranked": False,
        "params": {"mode": "award", "awards": ["Q102427"]},
    }
    values.update(fields)
    return make_definition(**values)


class WikidataTestCase(SimpleTestCase):
    def setUp(self):
        super().setUp()
        local = LocMemCache(f"wikidata-{id(self)}", {})
        patcher = patch.object(wd, "cache", local)
        patcher.start()
        self.addCleanup(patcher.stop)
        empty = TemporaryDirectory()
        self.addCleanup(empty.cleanup)
        path = Path(empty.name, "wikidata.csv")
        path.write_text(SUPPLEMENT_HEADER, encoding="utf-8")
        wd._supplement.cache_clear()
        patcher = patch.object(wd, "SUPPLEMENT", path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(wd._supplement.cache_clear)


class AwardTests(WikidataTestCase):
    def fetch(self, definition=None):
        with wikidata_routes(load_fixture("wikidata", "award_films.json"), load_fixture("wikidata", "types_film.json")) as api:
            result = ADAPTERS["wikidata"].fetch(definition or award_definition())
        return result, api

    def test_winners_newest_first_with_year_notes_and_ties(self):
        result, _ = self.fetch()
        self.assertEqual(
            [(entry.title, entry.note) for entry in result.entries],
            [
                ("Taste of Cherry", "1997 · tie"),
                ("The Eel", "1997 · tie"),
                ("All Quiet on the Western Front", "1930"),  # two ceremonies in 1930: not a tie
                ("The Broadway Melody", "1930"),
                ("Wings", "1929"),
            ],
        )
        self.assertEqual(result.edition, "1997")
        wings = result.entries[-1]
        self.assertEqual(wings.ids, {"wikidata": "Q272036", "tmdb": "28966", "imdb": "tt0018578"})
        self.assertEqual((wings.rank, wings.year, wings.creator, wings.media_type), (None, 1927, "William A. Wellman", "movie"))
        self.assertEqual(wings.key, "tmdb:movie:28966")
        # A person's IMDb id is not a title id.
        self.assertNotIn("imdb", result.entries[1].ids)

    def test_non_works_and_undated_winners_are_left_out(self):
        result, _ = self.fetch()
        titles = {entry.title for entry in result.entries}
        self.assertNotIn("Daniels", titles)  # a duo, not a film
        self.assertNotIn("Undated Winner", titles)

    def test_one_query_names_the_award_and_author_statements_are_opt_in(self):
        _, api = self.fetch()
        main = api.queries[0]
        self.assertIn("VALUES ?award { wd:Q102427 }", main)
        self.assertIn("FILTER NOT EXISTS { ?w wdt:P31 wd:Q5 }", main)
        self.assertNotIn("pq:P1686", main)
        self.assertIn("pq:P1686", wd.build_query(award_definition(params={"mode": "award", "awards": ["Q160082"], "author_statements": True})))

    def test_sparql_results_are_cached(self):
        definition = award_definition()
        with wikidata_routes(load_fixture("wikidata", "award_films.json"), load_fixture("wikidata", "types_film.json")) as api:
            first = ADAPTERS["wikidata"].fetch(definition)
            second = ADAPTERS["wikidata"].fetch(definition)
        self.assertEqual(first, second)
        self.assertEqual(len(api.queries), 2)  # the main query and the type check, once each

    def test_source_failures_are_adapter_errors(self):
        with patch("app.providers.services.api_request", side_effect=http_error(504)), self.assertRaisesMessage(
            AdapterError,
            "wikidata request failed (HTTP 504)",
        ):
            ADAPTERS["wikidata"].fetch(award_definition())
        with wikidata_routes({"results": {"bindings": []}}), self.assertRaisesMessage(AdapterError, "no entries"):
            ADAPTERS["wikidata"].fetch(award_definition())
        with wikidata_routes({"unexpected": True}), self.assertRaisesMessage(AdapterError, "unexpected SPARQL"):
            ADAPTERS["wikidata"].fetch(award_definition(params={"mode": "award", "awards": ["Q179808"]}))

    def test_games_use_the_numeric_igdb_qualifier(self):
        definition = award_definition(media_types=("game",), position=525)
        with wikidata_routes(load_fixture("wikidata", "award_games.json"), load_fixture("wikidata", "types_game.json")) as api:
            result = ADAPTERS["wikidata"].fetch(definition)
        self.assertEqual(result.entries[0].ids, {"wikidata": "Q64441774", "igdb": "119171"})
        self.assertIn("pq:P9043 ?igdb", api.queries[0])


class SupplementTests(WikidataTestCase):
    def test_rows_fix_years_add_winners_and_exclude_works(self):
        body = (
            "wikidata-test,1931,Q463620,The Broadway Melody,,,,year fix\n"
            "wikidata-test,2026,Q117085614,One Battle After Another,,,,Wikidata lacks it\n"
            "wikidata-test,2025,,Anora,Sean Baker,2024,tmdb:movie:1064213,pinned\n"
            "wikidata-test,2024,,Untitled Short,,2023,,title only\n"
            "wikidata-test,1997,Q1189212,The Eel,,,-,not the winner\n"
        )

        def main(query):
            data = load_fixture("wikidata", "award_films.json")
            if "wd:Q117085614" in query:
                data["results"]["bindings"].append(
                    {
                        "w": {"value": "http://www.wikidata.org/entity/Q117085614"},
                        "title": {"value": "One Battle After Another"},
                        "wins": {"value": "s|||"},
                        "types": {"value": ""},  # supplement rows skip the class check
                        "tmdb_movie_": {"value": "1054867"},
                    },
                )
            return data

        with supplement(body), wikidata_routes(main, load_fixture("wikidata", "types_film.json")) as api:
            definition = award_definition()
            result = ADAPTERS["wikidata"].fetch(definition)
            version = wd.version(definition)
        self.assertIn("VALUES ?w { wd:Q117085614 wd:Q463620 }", api.queries[0])
        notes = [(entry.title, entry.note) for entry in result.entries]
        self.assertEqual(notes[:3], [("One Battle After Another", "2026"), ("Anora", "2025"), ("Untitled Short", "2024")])
        self.assertIn(("The Broadway Melody", "1931"), notes)
        self.assertIn(("Taste of Cherry", "1997"), notes)  # no longer a tie
        self.assertNotIn("The Eel", [title for title, _ in notes])
        self.assertEqual(result.entries[1].ids, {"ref": "tmdb:movie:1064213"})
        self.assertEqual(result.entries[2].key, "t:untitled short|2023")
        self.assertEqual(result.entries[0].ids["tmdb"], "1054867")
        self.assertNotEqual(version, wd.version(definition))  # the empty supplement hashes differently

    def test_bad_rows_fail_validation(self):
        cases = {
            "wikidata-test,,Q1,,,,,\n": "list and year are required",
            "wikidata-test,2020,X1,,,,,\n": "qid must look like Q123",
            "wikidata-test,2020,,,,,,\n": "needs a title",
            "wikidata-test,2020,,Title,,,-,\n": "needs a qid",
            "wikidata-test,2020,,Title,,,tmdb:book:1,\n": "tmdb does not provide book",
            "wikidata-test,twenty,Q1,,,,,\n": "year must be a whole number",
        }
        for body, message in cases.items():
            with self.subTest(message), supplement(body), self.assertRaisesMessage(ValueError, message):
                wd.validate(award_definition())

    def test_the_repository_supplement_is_valid_and_scoped_to_wikidata_lists(self):
        wd._supplement.cache_clear()
        with patch.object(wd, "SUPPLEMENT", Path(wd.__file__).resolve().parent.parent / "static" / "supplements" / "wikidata.csv"):
            rows = wd._supplement()
            wd._supplement.cache_clear()
        keys = {definition.key for definition in definitions.DEFINITIONS if definition.adapter == "wikidata"}
        self.assertTrue(rows)
        self.assertEqual({row["list"] for row in rows} - keys, set())


@override_settings(HARDCOVER_API="test-hardcover-key")
class BookTests(WikidataTestCase):
    def fetch(self, **routes):
        definition = award_definition(
            key="wikidata-test",
            media_types=("book",),
            position=330,
            params={"mode": "award", "awards": ["Q160082"], "author_statements": True},
        )
        openlibrary = {
            "/works/OL2W/editions.json": http_error(404),
            "/works/OL2W.json": load_fixture("wikidata", "work_OL2W_redirect.json"),
            "/works/OL3W/editions.json": load_fixture("wikidata", "editions_OL3W.json"),
            "/search.json": {"docs": []},
            **routes,
        }
        with wikidata_routes(
            load_fixture("wikidata", "award_books.json"),
            load_fixture("wikidata", "types_book.json"),
            hardcover={"works": load_fixture("wikidata", "hardcover_works.json")},
            openlibrary=openlibrary,
        ):
            return ADAPTERS["wikidata"].fetch(definition)

    def test_works_become_hardcover_books_or_open_library_editions(self):
        entries = {entry.title: entry for entry in self.fetch().entries}
        orbital = entries["Orbital"]
        self.assertEqual(orbital.ids, {"hardcover": "1001", "wikidata": "Q115"})
        self.assertEqual(orbital.image, "https://assets.hardcover.app/orbital.jpg")
        self.assertEqual(orbital.note, "2024")  # from the author's statement
        song = entries["Prophet Song"]  # merged work: redirect followed, English edition with a cover
        self.assertEqual(song.ids, {"olid": "OL12M", "wikidata": "Q116"})
        self.assertEqual(song.image, "https://covers.openlibrary.org/b/id/12345-L.jpg")
        self.assertEqual(entries["The Conservationist"].ids, {"olid": "OL5M", "wikidata": "Q118"})
        holiday = entries["Holiday"]  # no Open Library id: left to the strict title search
        self.assertEqual(holiday.ids, {"wikidata": "Q117"})
        self.assertEqual((holiday.note, entries["The Conservationist"].note), ("1974 · tie", "1974 · tie"))

    def test_hardcover_mappings_must_agree_on_the_title(self):
        wrong = {"data": {"book_mappings": [{"external_id": "/works/OL1W", "book": {"id": 9, "title": "Something Else"}}]}}
        definition = award_definition(media_types=("book",), position=330, params={"mode": "award", "awards": ["Q160082"]})
        with wikidata_routes(
            load_fixture("wikidata", "award_books.json"),
            load_fixture("wikidata", "types_book.json"),
            hardcover={"works": wrong},
            openlibrary={
                "/works/OL1W/editions.json": {"entries": [{"key": "/books/OL7M", "title": "Orbital"}]},
                "/works/OL2W/editions.json": {"entries": []},
                "/search.json": {"docs": []},
            },
        ):
            entries = {entry.title: entry for entry in ADAPTERS["wikidata"].fetch(definition).entries}
        self.assertEqual(entries["Orbital"].ids, {"olid": "OL7M", "wikidata": "Q115"})
        self.assertEqual(entries["Prophet Song"].ids, {"wikidata": "Q116"})

    def test_books_without_ids_are_found_by_title_and_author(self):
        docs = {
            "docs": [
                {"key": "/works/OL8W", "title": "Holiday", "author_name": ["Someone Else"], "first_publish_year": 1965, "edition_count": 90},
                {"key": "/works/OL9W", "title": "Holiday", "author_name": ["Stanley Middleton"], "first_publish_year": 1990, "edition_count": 80},
                {"key": "/works/OL10W", "title": "Holiday", "author_name": ["Stanley Middleton"], "first_publish_year": 1974, "edition_count": 3},
                {"key": "/works/OL11W", "title": "Holiday", "author_name": ["Stanley Middleton"], "first_publish_year": 1966, "edition_count": 2},
                {"key": "/works/OL12W", "title": "Holidays Abroad", "author_name": ["Stanley Middleton"], "first_publish_year": 1965, "edition_count": 9},
            ],
        }
        editions = {"entries": [{"key": "/books/OL21M", "title": "Holiday", "languages": [{"key": "/languages/eng"}], "covers": [77]}]}
        entries = {entry.title: entry for entry in self.fetch(**{"/search.json": docs, "/works/OL11W/editions.json": editions}).entries}
        # Wrong author, too far from 1965 and a different title are all refused.
        self.assertEqual(entries["Holiday"].ids, {"olid": "OL21M", "wikidata": "Q117"})
        self.assertEqual(entries["Holiday"].image, "https://covers.openlibrary.org/b/id/77-L.jpg")

    def test_an_open_library_failure_leaves_the_entry_to_title_search(self):
        entries = {entry.title: entry for entry in self.fetch(**{"/works/OL2W.json": http_error(500)}).entries}
        self.assertEqual(entries["Prophet Song"].ids, {"wikidata": "Q116"})

    def test_books_left_to_title_search_use_the_main_title(self):
        body = (
            "wikidata-test,2021,,Wilmington's Lie: The Murderous Coup of 1898,David Zucchino,2020,,\n"
            "wikidata-test,2020,,Big: A Short One,Vashti Harrison,2019,,\n"
            "wikidata-test,2019,,Ok: Two Letters,Someone,2018,,\n"
            "wikidata-test,2018,,Blood in the Water (book),Heather Ann Thompson,2016,,\n"
        )
        with supplement(body):
            entries = {entry.creator: entry for entry in self.fetch().entries}
        self.assertEqual(entries["David Zucchino"].title, "Wilmington's Lie")
        self.assertEqual(entries["David Zucchino"].key, "t:wilmingtons lie|2020")
        self.assertEqual(entries["Vashti Harrison"].title, "Big")
        self.assertEqual(entries["Someone"].title, "Ok: Two Letters")  # too short to search alone
        self.assertEqual(entries["Heather Ann Thompson"].title, "Blood in the Water")

    def test_supplement_rows_fill_a_missing_year_and_add_their_author_spelling(self):
        body = "wikidata-test,1974,Q117,Holiday,Stanley Middleton Jr.,1965,,\nwikidata-test,2024,Q115,Orbital,,2023,,\n"
        with supplement(body):
            entries = {entry.title: entry for entry in self.fetch().entries}
        self.assertEqual(entries["Holiday"].creator, "Stanley Middleton, Stanley Middleton Jr.")
        self.assertEqual(entries["Orbital"].creator, "Samantha Harvey")

    @override_settings(HARDCOVER_API="")
    def test_without_a_hardcover_key_books_use_open_library(self):
        entries = {entry.title: entry for entry in self.fetch(**{"/works/OL1W/editions.json": {"entries": [{"key": "/books/OL7M"}]}}).entries}
        self.assertEqual(entries["Orbital"].ids, {"olid": "OL7M", "wikidata": "Q115"})


class CollectionTests(WikidataTestCase):
    def test_criterion_is_ranked_by_spine_number(self):
        definition = registry.get("criterion-collection")
        with wikidata_routes(load_fixture("wikidata", "criterion.json"), load_fixture("wikidata", "types_film.json")) as api:
            result = ADAPTERS["wikidata"].fetch(definition)
        self.assertEqual([(entry.rank, entry.title) for entry in result.entries], [(1, "Grand Illusion"), (2, "Seven Samurai"), (11, "The Seventh Seal")])
        self.assertIn("ps:P12279 ?number", api.queries[0])

    def test_registry_is_newest_induction_first(self):
        definition = registry.get("national-film-registry")
        body = "national-film-registry,2016,Q36479,The Lion King,,,,year fix\n"
        with supplement(body), wikidata_routes(load_fixture("wikidata", "registry.json"), load_fixture("wikidata", "types_film.json")):
            result = ADAPTERS["wikidata"].fetch(replace(definition, params=dict(definition.params)))
        self.assertEqual(
            [(entry.title, entry.note) for entry in result.entries],
            [("Iron Man", "Inducted 2022"), ("The Lion King", "Inducted 2016"), ("Citizen Kane", "Inducted 1989")],
        )
        self.assertEqual(result.edition, "2022")


class ValidationTests(SimpleTestCase):
    def problems(self, **fields):
        return registry.definition_problems(award_definition(**fields), ADAPTERS)

    def test_rejects_bad_params(self):
        cases = {
            "mode must be one of": {"params": {"mode": "nope"}},
            "award lists need at least one award": {"params": {"mode": "award", "awards": []}},
            "awards must be Wikidata item ids": {"params": {"mode": "award", "awards": ["102427"]}},
            "award lists are unranked": {"is_ranked": True},
            "registry lists hold movies": {"params": {"mode": "registry", "part_of": "Q823422"}, "media_types": ("book",), "position": 330},
            "part_of must be Wikidata item ids": {"params": {"mode": "registry", "part_of": "823422"}},
            "Wikidata lists can't hold": {"media_types": ("movie", "comic")},
        }
        for message, fields in cases.items():
            with self.subTest(message):
                self.assertTrue(any(message in problem for problem in self.problems(**fields)), self.problems(**fields))

    def test_family_definitions_are_in_range_and_unranked_awards(self):
        for definition in definitions.DEFINITIONS:
            with self.subTest(definition.key):
                self.assertEqual(registry.definition_problems(definition, ADAPTERS), [])
                self.assertTrue(definition.source_name and definition.source_url)
                if definition.kind == "award":
                    self.assertFalse(definition.is_ranked)
                section = registry.section_for(definition.position)[0]
                ranges = {"film": (130, 149), "books": (310, 329), "games": (520, 529), "people": (900, 909)}
                low, high = ranges[section]
                self.assertTrue(low <= definition.position <= high)


class NobelTests(WikidataTestCase):
    def fetch(self):
        definition = registry.get("nobel-prize-literature")

        def respond(provider, method, url, params=None, data=None, **kwargs):  # noqa: ARG001
            if url.endswith("/nobelPrizes"):
                self.assertEqual(params, {"nobelPrizeCategory": "lit", "limit": nobel_adapter.LIMIT})
                return load_fixture("wikidata", "nobel_prizes.json")
            if url.endswith("/laureates"):
                return load_fixture("wikidata", "nobel_laureates.json")
            if "query.wikidata.org" in url:
                self.assertIn("wd:Q2633699", data["query"])
                return load_fixture("wikidata", "nobel_people.json")
            msg = f"Unexpected {url}"
            raise AssertionError(msg)

        with patch("app.providers.services.api_request", side_effect=respond):
            return ADAPTERS["nobel"].fetch(definition)

    def test_laureates_newest_first_mapped_to_open_library(self):
        result = self.fetch()
        self.assertEqual(
            [(entry.title, entry.note, entry.ids.get("openlibrary_person")) for entry in result.entries],
            [
                ("Anne Carson", "2026", None),
                ("László Krasznahorkai", "2025", "OL1300A"),
                ("Eyvind Johnson", "1974 · shared", "OL1100A"),
                ("Harry Martinson", "1974 · shared", "OL1200A"),  # Wikidata id from sameAs
            ],
        )
        self.assertEqual(result.edition, "2026")
        carson = result.entries[0]
        self.assertEqual(carson.ids, {"wikidata": "Q2633699"})  # resolved by strict name search
        self.assertEqual(carson.key, "wikidata:Q2633699")
        self.assertEqual(
            result.entries[2].image,
            "https://commons.wikimedia.org/wiki/Special:FilePath/Eyvind_Johnson.jpg?width=400",
        )

    def test_failures_and_validation(self):
        with patch("app.providers.services.api_request", side_effect=http_error(524)), self.assertRaisesMessage(
            AdapterError,
            "nobelprize request failed (HTTP 524)",
        ):
            ADAPTERS["nobel"].fetch(registry.get("nobel-prize-literature"))
        with patch("app.providers.services.api_request", return_value={"nobelPrizes": []}), self.assertRaisesMessage(
            AdapterError,
            "no prizes",
        ):
            ADAPTERS["nobel"].fetch(registry.get("nobel-prize-literature"))
        problems = registry.definition_problems(
            make_definition(adapter="nobel", params={"category": "art"}, position=905, list_type="people"),
            ADAPTERS,
        )
        self.assertTrue(any("category must be one of" in problem for problem in problems))
