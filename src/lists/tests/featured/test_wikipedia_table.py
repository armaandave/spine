"""Tests for the Wikipedia table adapter and its Wikidata ID lookups (no network)."""

from datetime import UTC, datetime
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from app.providers import quota
from lists.featured import engine
from lists.featured.adapters import ADAPTERS
from lists.featured.adapters import _wikipedia_ids as wikidata
from lists.featured.adapters import wikipedia_table
from lists.featured.definitions import wikipedia as definitions
from lists.featured.registry import Guards
from lists.featured.types import AdapterError
from lists.tests.featured.helpers import FeaturedTestCase, api_routes, load_fixture, make_definition

WIKIPEDIA = "en.wikipedia.org/w/api.php"
JA_WIKIPEDIA = "ja.wikipedia.org/w/api.php"
SPARQL = "query.wikidata.org/sparql"
HARDCOVER = "api.hardcover.app"
OPENLIBRARY = "openlibrary.org/search.json"


def parse_response(fixture, title="Page"):
    """Return a MediaWiki ``action=parse`` response holding a fixture's HTML."""
    return {"parse": {"title": title, "revid": 1, "text": load_fixture("wikipedia_table", fixture)}}


def wiki(pages, *, items=None, redirects=(), missing=()):
    """Route MediaWiki requests.

    ``prop=revisions`` gives each page in ``pages`` one settled revision, ``action=parse``
    renders it by ``oldid``, and ``prop=pageprops`` answers from ``items``.
    """
    items = items or {}
    revids = {title: revid for revid, title in enumerate(pages, start=101)}
    titles_by_revid = {revid: title for title, revid in revids.items()}

    def respond(url, params):  # noqa: ARG001
        if params["action"] == "parse":
            title = titles_by_revid.get(int(params["oldid"]))
            if title is None or title in missing:
                return {"error": {"code": "nosuchrevid", "info": "There is no revision with that ID."}}
            return pages[title]
        if params.get("prop") == "revisions":
            title = params["titles"]
            if title in missing or title not in pages:
                return {"query": {"pages": [{"ns": 0, "title": title, "missing": True}]}}
            revision = {"revid": revids[title], "parentid": 1, "timestamp": "2026-10-01T12:00:00Z"}
            return {"query": {"pages": [{"pageid": revids[title], "title": title, "revisions": [revision]}]}}
        titles = params["titles"].split("|")
        return {
            "query": {
                "redirects": [dict(row) for row in redirects if row["from"] in titles],
                "pages": [
                    {"title": title, **({"pageprops": {"wikibase_item": items[title]}} if title in items else {})}
                    for title in {*titles, *(row["to"] for row in redirects)}
                ],
            },
        }

    return respond


def sparql(rows):
    """Return a SPARQL response from ``(qid, pid, value, number, preferred)`` tuples."""
    bindings = []
    for qid, pid, value, *rest in rows:
        number = rest[0] if rest else None
        preferred = rest[1] if len(rest) > 1 else False
        binding = {
            "item": {"value": f"http://www.wikidata.org/entity/{qid}"},
            "pid": {"value": pid},
            "value": {"value": value},
            "rank": {
                "value": "http://wikiba.se/ontology#"
                + ("PreferredRank" if preferred else "NormalRank"),
            },
        }
        if number:
            binding["number"] = {"value": number}
        bindings.append(binding)
    return {"results": {"bindings": bindings}}


def definition_for(key):
    return next(item for item in definitions.DEFINITIONS if item.key == key)


class ParseTests(SimpleTestCase):
    """Table parsing: spans, winners, titles, merging and order."""

    def rows(self, fixture, params):
        rows, _ = wikipedia_table.parse_rows(load_fixture("wikipedia_table", fixture), params)
        return wikipedia_table._merge(rows, params)

    def test_emmy_winners_newest_first_with_repeat_years_merged(self):
        rows = self.rows("emmy_drama.html", definition_for("emmys-outstanding-drama-series").params)
        self.assertEqual([row.title for row in rows], ["Succession", "Game of Thrones"])
        succession, thrones = rows
        self.assertEqual(succession.notes, ["2022", "2020"])
        # The 2022 row links only its season list; the merge keeps the series link.
        self.assertEqual(succession.link, "Succession (TV series)")
        self.assertEqual(thrones.year, 2019)
        self.assertEqual(thrones.link, "Game of Thrones")

    def test_nominees_without_a_winner_yet_are_left_out(self):
        rows = self.rows("emmy_drama.html", {**definition_for("emmys-outstanding-drama-series").params, "winners": "all"})
        titles = [row.title for row in rows]
        self.assertIn("Severance", titles)
        self.assertIn("Andor", titles)
        self.assertNotIn("Game of Thrones Wins", titles)
        andor = next(row for row in rows if row.title == "Andor")
        self.assertEqual(andor.link, "")  # a red link has no article

    def test_result_column_and_caption_year(self):
        rows = self.rows("nba_translated.html", definition_for("national-book-award-translated-literature").params)
        self.assertEqual(
            [(row.title, row.year, row.creator) for row in rows],
            [
                ("We Are Green and Trembling", 2025, "Gabriela Cabezón Cámara"),
                ("Taiwan Travelogue", 2024, "Yáng Shuāng-zǐ"),
            ],
        )

    def test_main_title_drops_the_subtitle(self):
        params = {"pages": ["P"], "columns": {"title": "Program"}, "winners": "all", "main_title": True}
        rows, _ = wikipedia_table.parse_rows(
            "<table class='wikitable'><tr><th>Year</th><th>Program</th></tr>"
            "<tr><td>2002</td><td><i>Master of the Senate: The Years of Lyndon Johnson</i></td></tr></table>",
            params,
        )
        self.assertEqual(rows[0].title, "Master of the Senate")

    def test_rowspan_weeks_fold_into_one_album_with_its_latest_week(self):
        rows = self.rows("billboard.html", definition_for("billboard-200-number-one-albums").params)
        self.assertEqual(
            [(row.title, row.notes) for row in rows],
            [
                ("I'm the Problem", ["August 8", "January 17"]),
                ("Golden Hour: Part.5", ["July 11"]),
                ("The Life of a Showgirl", ["January 10", "January 3"]),
            ],
        )
        self.assertEqual(rows[0].link, "I'm the Problem (Morgan Wallen album)")
        self.assertEqual(rows[2].creator, "Taylor Swift")

    def test_latest_group_only_with_ranks(self):
        params = definition_for("kono-manga-ga-sugoi-mens-top-10").params
        rows = self.rows("kono_manga_ja.html", params)
        self.assertEqual(
            [(row.rank, row.title, row.year) for row in rows],
            [(1, "本なら売るほど", 2026), (2, "「壇蜜」", 2026), (2, "サンキューピッチ", 2026)],
        )
        self.assertEqual(rows[0].link, "本なら売るほど")
        self.assertEqual(rows[1].link, "")

    def test_two_title_columns_and_creator_in_the_title_cell(self):
        rows = self.rows("kono_manga_en.html", definition_for("kono-manga-ga-sugoi-number-ones").params)
        by_title = {row.title: row for row in rows}
        self.assertEqual(by_title["Pluto"].creator, "Naoki Urasawa")
        self.assertEqual(by_title["Pluto"].notes, ["2006 · Men's"])
        self.assertEqual(by_title["Honey and Clover"].notes, ["2007 · Women's", "2006 · Women's"])
        self.assertEqual(by_title["Her"].link, "")  # the cell links the author, not the work
        self.assertEqual(rows[0].year, 2011)

    def test_clean_title(self):
        self.assertEqual(wikipedia_table.clean_title("M*A*S*H"), ("M*A*S*H", ""))
        self.assertEqual(wikipedia_table.clean_title("Gaku ‡"), ("Gaku", ""))
        self.assertEqual(
            wikipedia_table.clean_title("The True Story (and His Mother)"),
            ("The True Story (and His Mother)", ""),
        )
        self.assertEqual(
            wikipedia_table.clean_title("Show (comedy series) (Season 3)", strip_parenthetical=True),
            ("Show", ""),
        )

    def test_expand_fills_row_and_column_spans(self):
        from bs4 import BeautifulSoup  # noqa: PLC0415

        table = BeautifulSoup(
            "<table><tr><td rowspan='2'>a</td><td colspan='2'>b</td></tr><tr><td>c</td><td>d</td></tr></table>",
            "html.parser",
        ).table
        grid = [[cell.get_text() for cell in row] for _, row in wikipedia_table.expand(table)]
        self.assertEqual(grid, [["a", "b", "b"], ["a", "c", "d"]])


class ValidateTests(SimpleTestCase):
    def test_every_definition_is_valid(self):
        for definition in definitions.DEFINITIONS:
            ADAPTERS["wikipedia_table"].validate(definition)

    def test_bad_params(self):
        base = {"pages": ["P"], "columns": {"title": "Title"}}
        bad = [
            {},
            {"pages": ["P"]},
            {**base, "columns": {"title": "Title", "nope": "x"}},
            {**base, "winners": "some"},
            {**base, "order": "oldest"},
            {**base, "site": "english"},
            {**base, "tables": {"index": ["1"]}},
            {**base, "category": "("},
            {"pages": ["P"], "columns": {"title": "T"}, "titles": [{"header": "A"}]},
        ]
        for params in bad:
            with self.subTest(params=params), self.assertRaises(ValueError):
                wikipedia_table.validate(make_definition(adapter="wikipedia_table", params=params))
        with self.assertRaises(ValueError):
            wikipedia_table.validate(
                make_definition(adapter="wikipedia_table", params=base, media_types=("movie", "tv")),
            )
        with self.assertRaises(ValueError):
            wikipedia_table.validate(
                make_definition(adapter="wikipedia_table", params={**base, "mangaupdates_search": True}),
            )


class FetchTests(SimpleTestCase):
    """End to end: page, pageprops, SPARQL, provider mappings."""

    def tv_definition(self, **params):
        definition = definition_for("emmys-outstanding-drama-series")
        return make_definition(
            key="test-emmys",
            adapter="wikipedia_table",
            media_types=("tv",),
            position=230,
            is_ranked=False,
            params={**definition.params, "min_entries": 1, **params},
        )

    def test_tv_ids_from_wikidata_with_imdb_and_series_fallbacks(self):
        page = "Primetime Emmy Award for Outstanding Drama Series"
        routes = {
            WIKIPEDIA: wiki(
                {page: parse_response("emmy_drama.html")},
                items={"Succession (TV series)": "Q1", "Game of Thrones": "Q2"},
            ),
            SPARQL: sparql([("Q1", "P345", "tt7660850"), ("Q2", "P179", "http://www.wikidata.org/entity/Q3")]),
        }
        # The second SPARQL call (the series of Q2) answers with Q3's TMDB ID.
        calls = []

        def answer(url, params):
            calls.append(params["query"])
            if "wd:Q3" in params["query"]:
                return sparql([("Q3", "P4983", "1399")])
            return routes[SPARQL]

        with api_routes({WIKIPEDIA: routes[WIKIPEDIA], SPARQL: answer}):
            result = wikipedia_table.fetch(self.tv_definition())
        succession, thrones = result.entries
        self.assertEqual(succession.ids, {"imdb": "tt7660850", "wikidata": "Q1"})
        self.assertEqual(succession.note, "2022, 2020")
        self.assertIsNone(succession.year)  # match_year is off for TV
        self.assertIsNone(succession.rank)
        self.assertEqual(thrones.ids, {"tmdb": "1399", "wikidata": "Q2"})
        self.assertEqual(thrones.key, "tmdb:tv:1399")
        self.assertEqual(result.edition, "Through 2022")
        self.assertEqual(len(calls), 2)
        self.assertIn("P4983", calls[0])

    def test_section_redirects_get_no_item(self):
        page = "List of Billboard 200 number-one albums of {year}"
        definition = make_definition(
            key="test-billboard",
            adapter="wikipedia_table",
            media_types=("music",),
            position=750,
            is_ranked=False,
            params={**definition_for("billboard-200-number-one-albums").params, "pages": [page.replace("{year}", "X")]},
        )
        routes = {
            WIKIPEDIA: wiki(
                {page.replace("{year}", "X"): parse_response("billboard.html")},
                items={"Ateez discography": "Q9", "The Life of a Showgirl": "Q8"},
                redirects=[{"from": "I'm the Problem (Morgan Wallen album)", "to": "Morgan Wallen", "tofragment": "x"}],
            ),
            SPARQL: sparql([("Q8", "P436", "a1b2c3d4-0000-0000-0000-000000000000"), ("Q9", "P436", "ffff")]),
        }
        albums = {
            "release-groups": [
                {"id": "single", "title": "I'm the Problem", "primary-type": "Single", "artist-credit": [{"name": "Morgan Wallen"}]},
                {"id": "rg-problem", "title": "I'm the Problem", "primary-type": "Album", "artist-credit": [{"name": "Morgan Wallen"}]},
                {"id": "other", "title": "I'm the Problem", "primary-type": "Album", "artist-credit": [{"name": "Someone Else"}]},
            ],
        }

        def search(query, limit):
            self.assertEqual(limit, 25)
            return albums if query.startswith("I'm the Problem") else {}

        with api_routes(routes), patch.object(wikidata.musicbrainz, "search_release_groups", side_effect=search):
            result = wikipedia_table.fetch(definition)
        ids = {entry.title: entry.ids for entry in result.entries}
        self.assertEqual(ids["The Life of a Showgirl"]["mbid_rg"], "a1b2c3d4-0000-0000-0000-000000000000")
        self.assertEqual(ids["I'm the Problem"], {"mbid_rg": "rg-problem"})
        # The cell links a section of the band's discography, not an album article.
        self.assertEqual(ids["Golden Hour: Part.5"], {})

    def test_current_year_page_falls_back_to_last_year(self):
        definition = make_definition(
            key="test-billboard",
            adapter="wikipedia_table",
            media_types=("music",),
            position=750,
            is_ranked=False,
            params={**definition_for("billboard-200-number-one-albums").params},
        )
        with (
            patch.object(wikipedia_table, "datetime") as clock,
            api_routes(
                {
                    WIKIPEDIA: wiki({"List of Billboard 200 number-one albums of 2026": parse_response("billboard.html")}),
                    SPARQL: sparql([]),
                },
            ),
        ):
            clock.now.return_value.year = 2027
            with patch.object(wikidata.musicbrainz, "search_release_groups", return_value={}):
                result = wikipedia_table.fetch(definition)
        self.assertEqual(result.edition, "2026")
        self.assertEqual(len(result.entries), 3)

    def test_failures_raise_adapter_errors(self):
        definition = self.tv_definition()
        page = "Primetime Emmy Award for Outstanding Drama Series"
        with api_routes({WIKIPEDIA: wiki({}, missing=[page])}), self.assertRaises(AdapterError):
            wikipedia_table.fetch(definition)
        with (
            api_routes({WIKIPEDIA: wiki({page: parse_response("emmy_drama.html")})}),
            self.assertRaisesMessage(AdapterError, "expected at least 500"),
        ):
            wikipedia_table.fetch(self.tv_definition(min_entries=500))
        with (
            api_routes({WIKIPEDIA: {"error": {"code": "internal_api_error"}}}),
            self.assertRaises(AdapterError),
        ):
            wikipedia_table.fetch(definition)
        with (
            api_routes(
                {
                    WIKIPEDIA: wiki({page: parse_response("emmy_drama.html")}, items={"Game of Thrones": "Q2"}),
                    SPARQL: {"unexpected": True},
                },
            ),
            self.assertRaises(AdapterError),
        ):
            wikipedia_table.fetch(definition)


class AnimeSeasonTests(SimpleTestCase):
    def test_a_series_mal_id_is_kept_only_for_the_series_title(self):
        definition = make_definition(
            key="test-anime",
            adapter="wikipedia_table",
            media_types=("anime",),
            position=630,
            is_ranked=False,
            params={"pages": ["P"], "columns": {"title": "Anime"}},
        )
        rows = [
            wikipedia_table.Row(title="Attack on Titan: The Final Season Part 1", link="Attack on Titan (TV series)"),
            wikipedia_table.Row(title="Jujutsu Kaisen", link="Jujutsu Kaisen (TV series)"),
        ]
        with (
            patch.object(wikidata, "wikidata_items", return_value={row.link: f"Q{i}" for i, row in enumerate(rows)}),
            patch.object(wikidata, "provider_ids", return_value={"Q0": ({"mal": "16498"}, ""), "Q1": ({"mal": "40748"}, "")}),
        ):
            entries = wikipedia_table._entries(definition, rows)
        self.assertEqual([entry.ids for entry in entries], [{"wikidata": "Q0"}, {"mal": "40748", "wikidata": "Q1"}])


class WikidataIdTests(SimpleTestCase):
    """Property-to-id rules."""

    def ids(self, media_type, rows):
        with api_routes({SPARQL: sparql(rows)}):
            return wikidata.provider_ids(media_type, {row[0] for row in rows})

    def test_igdb_needs_the_numeric_qualifier_else_steam(self):
        result = self.ids(
            "game",
            [("Q1", "P5794", "hades--1", "113112"), ("Q2", "P5794", "slug-only"), ("Q2", "P1733", "1145360")],
        )
        self.assertEqual(result["Q1"], ({"igdb": "113112"}, ""))
        self.assertEqual(result["Q2"], ({"steam": "1145360"}, ""))

    def test_ambiguous_values_are_dropped_unless_one_is_preferred(self):
        result = self.ids(
            "anime",
            [
                ("Q1", "P4086", "40748"),
                ("Q1", "P4086", "51009"),
                ("Q2", "P4086", "1"),
                ("Q2", "P4086", "2", None, True),
            ],
        )
        self.assertNotIn("Q1", result)
        self.assertEqual(result["Q2"], ({"mal": "2"}, ""))

    def test_manga_comic_and_music_ids(self):
        self.assertEqual(
            self.ids("manga", [("Q1", "P4087", "745"), ("Q1", "P11149", "pb8uwds")])["Q1"][0],
            {"mal": "745", "mangaupdates": str(int("pb8uwds", 36))},
        )
        result = self.ids("comic", [("Q1", "P5905", "4050-18166"), ("Q2", "P5905", "4075-909")])
        self.assertEqual(result, {"Q1": ({"comicvine": "18166"}, "")})
        self.assertEqual(
            self.ids("music", [("Q1", "P436", "rg-mbid")]),
            {"Q1": ({"mbid_rg": "rg-mbid"}, "")},
        )

    @override_settings(HARDCOVER_API="test-key")
    def test_books_prefer_hardcover_then_open_library_editions(self):
        hardcover = {
            "data": {
                "book_mappings": [
                    {"external_id": "7937843", "book": {"id": 382550, "title": "Room", "cached_image": "https://img/room"}},
                ],
            },
        }
        openlibrary = {
            "docs": [
                {"key": "/works/OL14958468W", "cover_edition_key": "OL50230M", "cover_i": 419595},
                {"key": "/works/OL1W", "editions": {"docs": [{"key": "/books/OL9M", "cover_i": None}]}},
            ],
        }
        rows = [
            ("Q1", "P2969", "7937843"),
            ("Q1", "P648", "OL15212619W"),
            ("Q2", "P648", "OL14958468W"),
            ("Q3", "P648", "OL1W"),
            ("Q4", "P648", "OL5M"),
            ("Q5", "P8383", "9585076"),
        ]
        with api_routes({SPARQL: sparql(rows), HARDCOVER: hardcover, OPENLIBRARY: openlibrary}) as mocked:
            result = wikidata.provider_ids("book", {"Q1", "Q2", "Q3", "Q4", "Q5"})
        self.assertEqual(result["Q1"], ({"hardcover": "382550"}, "https://img/room"))
        self.assertEqual(result["Q2"], ({"olid": "OL50230M"}, "https://covers.openlibrary.org/b/id/419595-L.jpg"))
        self.assertEqual(result["Q3"], ({"olid": "OL9M"}, ""))
        self.assertEqual(result["Q4"], ({"olid": "OL5M"}, ""))
        self.assertNotIn("Q5", result)
        hardcover_call = next(call for call in mocked.call_args_list if HARDCOVER in call.args[2])
        self.assertEqual(hardcover_call.kwargs["params"]["variables"], {"ids": ["7937843"]})
        openlibrary_call = next(call for call in mocked.call_args_list if OPENLIBRARY in call.args[2])
        self.assertNotIn("OL15212619W", openlibrary_call.kwargs["params"]["q"])  # Q1 is on Hardcover

    def test_mangaupdates_needs_one_exact_matched_name(self):
        def search(url, params):  # noqa: ARG001
            results = {
                "環と周": [
                    {"hit_title": "環と周", "record": {"series_id": 20894714508, "image": {"url": {"original": "https://i/t"}}}},
                    {"hit_title": "環と康平", "record": {"series_id": 72452311599}},
                ],
                "Her": [
                    {"hit_title": "Her", "record": {"series_id": 1}},
                    {"hit_title": "HER", "record": {"series_id": 2}},
                ],
                "ボールアンドチェイン": [{"hit_title": "ホールアントチェイン", "record": {"series_id": 3}}],
            }[params["search"]]
            return {"total_hits": len(results), "results": results}

        with api_routes({"api.mangaupdates.com/v1/series/search": search}):
            found = wikidata.mangaupdates_by_title(["環と周", "Her", "ボールアンドチェイン"])
        self.assertEqual(found, {"環と周": ({"mangaupdates": "20894714508"}, "https://i/t")})

    def test_musicbrainz_album_replaces_an_unconfirmed_wikidata_id(self):
        definition = make_definition(
            key="test-billboard",
            adapter="wikipedia_table",
            media_types=("music",),
            position=750,
            is_ranked=False,
            params={**definition_for("billboard-200-number-one-albums").params, "pages": ["B"]},
        )
        response = {
            "release-groups": [
                {"id": "rg-album", "title": "The Life of a Showgirl", "primary-type": "Album", "artist-credit": [{"name": "Taylor Swift"}]},
                {"id": "rg-live", "title": "The Life of a Showgirl", "primary-type": "Album", "secondary-types": ["Live"], "artist-credit": [{"name": "Taylor Swift"}]},
            ],
        }
        routes = {
            WIKIPEDIA: wiki({"B": parse_response("billboard.html")}, items={"The Life of a Showgirl": "Q8"}),
            SPARQL: sparql([("Q8", "P436", "a-release-id")]),
        }
        with (
            api_routes(routes),
            patch.object(
                wikidata.musicbrainz,
                "search_release_groups",
                side_effect=lambda query, limit: response if "Showgirl" in query else {},  # noqa: ARG005
            ),
        ):
            result = wikipedia_table.fetch(definition)
        showgirl = next(entry for entry in result.entries if entry.title == "The Life of a Showgirl")
        self.assertEqual(showgirl.ids, {"mbid_rg": "rg-album", "wikidata": "Q8"})

    @override_settings(HARDCOVER_API="test-key")
    def test_hardcover_errors_stop_the_run(self):
        with (
            api_routes({SPARQL: sparql([("Q1", "P2969", "1")]), HARDCOVER: {"errors": [{"message": "x"}]}}),
            self.assertRaises(AdapterError),
        ):
            wikidata.provider_ids("book", {"Q1"})


class EngineTests(FeaturedTestCase):
    """A book list through the engine's dry run, with Hardcover IDs from Wikidata."""

    @override_settings(HARDCOVER_API="test-key")
    def test_dry_run_resolves_hardcover_ids_without_title_searches(self):
        definition = make_definition(
            key="test-nba",
            name="National Book Award for Translated Literature",
            adapter="wikipedia_table",
            media_types=("book",),
            position=373,
            kind="award",
            is_ranked=False,
            params={**definition_for("national-book-award-translated-literature").params, "min_entries": 1},
            guards=Guards(max_title_searches=0),
        )
        page = "National Book Award for Translated Literature"
        routes = {
            WIKIPEDIA: wiki(
                {page: parse_response("nba_translated.html")},
                items={"Taiwan Travelogue": "Q1", "We Are Green and Trembling": "Q2"},
            ),
            SPARQL: sparql([("Q1", "P2969", "11"), ("Q2", "P2969", "22")]),
            HARDCOVER: {
                "data": {
                    "book_mappings": [
                        {"external_id": "11", "book": {"id": 101, "title": "Taiwan Travelogue", "cached_image": "https://i/1"}},
                        {"external_id": "22", "book": {"id": 202, "title": "We Are Green", "cached_image": "https://i/2"}},
                    ],
                },
            },
        }
        with api_routes(routes):
            result = engine.dry_run(definition, self.owner, fetch_result=ADAPTERS["wikipedia_table"].fetch(definition))
        self.assertEqual(
            [resolution.match.ref for resolution in result.resolutions],
            ["hardcover:book:202", "hardcover:book:101"],
        )
        self.assertEqual(result.resolutions[0].entry.note, "2025")
        self.assertEqual(result.message, "")


class StableRevisionTests(SimpleTestCase):
    """Pages are read at their newest revision that has stood for 30 minutes."""

    NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)

    def test_parses_the_newest_revision_at_least_half_an_hour_old(self):
        calls = []

        def respond(url, params):  # noqa: ARG001
            calls.append(params)
            if params["action"] == "query":  # the vandal's edit at 11:50 is newer than 11:30
                revision = {"revid": 555, "parentid": 554, "timestamp": "2026-10-22T09:00:00Z"}
                return {"query": {"pages": [{"pageid": 7, "title": "Page", "revisions": [revision]}]}}
            return {"parse": {"title": "Page", "revid": 555, "text": "<p>settled</p>"}}

        with api_routes({WIKIPEDIA: respond}):
            html, revid = wikipedia_table.fetch_page("en", "Page", now=self.NOW)
        self.assertEqual((html, revid), ("<p>settled</p>", 555))
        query, parse = calls
        self.assertEqual(
            {name: query[name] for name in ("prop", "titles", "redirects", "rvlimit", "rvdir", "rvstart")},
            {
                "prop": "revisions",
                "titles": "Page",
                "redirects": "1",
                "rvlimit": "1",
                "rvdir": "older",
                "rvstart": "2026-10-22T11:30:00Z",
            },
        )
        self.assertEqual(parse["oldid"], "555")
        self.assertNotIn("page", parse)

    def test_a_missing_page_is_none(self):
        missing = {"query": {"pages": [{"ns": 0, "title": "Nope", "missing": True}]}}
        with api_routes({WIKIPEDIA: missing}):
            self.assertEqual(wikipedia_table.fetch_page("en", "Nope", now=self.NOW), (None, None))

    def test_a_page_created_in_the_last_half_hour_defers_the_run(self):
        fresh = {"query": {"pages": [{"pageid": 9, "title": "2026 Awards"}]}}  # no revision before 11:30
        with api_routes({WIKIPEDIA: fresh}), self.assertRaises(quota.Deferred) as raised:
            wikipedia_table.fetch_page("en", "2026 Awards", now=self.NOW)
        self.assertEqual(raised.exception.until, datetime(2026, 10, 22, 12, 30, tzinfo=UTC))

    def test_unreadable_revision_answers_raise(self):
        for payload in ({"error": {"code": "internal_api_error"}}, {"query": {}}, ["odd"]):
            with self.subTest(payload=payload), api_routes({WIKIPEDIA: payload}), self.assertRaises(AdapterError):
                wikipedia_table.fetch_page("en", "Page", now=self.NOW)
