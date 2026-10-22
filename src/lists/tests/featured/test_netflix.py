from unittest.mock import patch

import requests

from lists.featured import overrides
from lists.featured.adapters import ADAPTERS, netflix
from lists.featured.adapters import _tmdb_match as tmdb_match
from lists.featured.registry import get
from lists.featured.types import AdapterError
from lists.tests.featured.helpers import (
    FeaturedTestCase,
    api_routes,
    few_rows,
    load_fixture,
    make_definition,
    no_overrides,
)


def tmdb_search(results_by_query):
    """Route TMDB /search/* by query: ``{("tv", "below"): [results…]}``, else no results."""

    def respond(url, params):
        media_type = url.rsplit("/", 1)[-1]
        return {"results": results_by_query.get((media_type, params["query"].casefold()), [])}

    return respond


def result(media_id, title, date, *, language="en", popularity=10.0, original=None, poster="/p.jpg", votes=100):
    return {
        "id": media_id,
        "title": title,
        "name": title,
        "original_title": original or title,
        "release_date": date,
        "first_air_date": date,
        "original_language": language,
        "popularity": popularity,
        "vote_count": votes,
        "poster_path": poster,
    }


def latin1(text):
    """What requests hands back for a UTF-8 body served without a charset."""
    return text.encode("utf-8").decode("latin-1")


SEARCHES = {
    ("movie", "animals"): [
        result(9001, "Animals", "1998-01-01", popularity=2.0),
        result(1236045, "Animals", "2026-10-02", popularity=80.0),
    ],
    ("movie", "ice age"): [
        result(425, "Ice Age", "2002-03-10", popularity=40.0),
        result(9002, "Ice Age: The Meltdown", "2006-03-29", popularity=30.0),
    ],
    ("tv", "below"): [result(285322, "Below", "2026-10-01", popularity=50.0)],
    ("tv", "the diplomat"): [result(203857, "The Diplomat", "2023-04-20", popularity=60.0)],
    ("tv", "lovesick"): [
        result(62668, "Lovesick", "2014-10-02", popularity=12.7),
        result(290234, "Lovesick", "2026-09-02", language="es", popularity=9.1),
    ],
}


class NetflixHelpersTests(FeaturedTestCase):
    def test_clean_title_and_season_label(self):
        self.assertEqual(netflix.clean_title("Below: Limited Series"), "Below")
        self.assertEqual(netflix.clean_title("The Diplomat: Season 4"), "The Diplomat")
        self.assertEqual(netflix.clean_title("Money Heist: Part 5"), "Money Heist")
        self.assertEqual(netflix.clean_title("Ice Age: Collision Course"), "Ice Age: Collision Course")
        self.assertEqual(netflix.season_label("Below", "Below: Limited Series"), "Limited Series")
        self.assertEqual(netflix.season_label("Stranger Things", "Stranger Things 4"), "4")
        self.assertEqual(netflix.season_label("Animals", "N/A"), "")

    def test_tv_titles_try_the_series_name_from_season_title(self):
        self.assertEqual(
            netflix.tv_titles("DAHMER", "DAHMER: Monster: The Jeffrey Dahmer Story"),
            ["DAHMER", "Monster: The Jeffrey Dahmer Story", "DAHMER: Monster: The Jeffrey Dahmer Story"],
        )
        self.assertEqual(netflix.tv_titles("The Diplomat", "The Diplomat: Season 4"), ["The Diplomat"])
        # never the bare main title: "Instadocs: AI Gone Wild" is not "Instadocs"
        self.assertEqual(netflix.tv_titles("Instadocs: AI Gone Wild", "N/A"), ["Instadocs: AI Gone Wild"])

    def test_edition_and_utf8(self):
        self.assertEqual(netflix.edition_label("2026-10-18"), "Week of Oct 12, 2026")
        self.assertEqual(netflix.as_utf8(latin1("JoJo’s")), "JoJo’s")
        self.assertEqual(netflix.as_utf8("JoJo’s"), "JoJo’s")  # already decoded: unchanged

    def test_validate(self):
        for params in (
            {"file": "daily", "category": "Films (English)"},
            {"file": "weekly", "category": "Films (French)"},
            {"file": "weekly", "categories": ["Films (English)"]},
        ):
            with self.subTest(params=params), self.assertRaises(ValueError):
                netflix.validate(make_definition(adapter="netflix", params=params))
        with self.assertRaisesMessage(ValueError, "media_types"):
            netflix.validate(make_definition(adapter="netflix", params={"file": "weekly", "category": "TV (English)"}))


class NetflixWeeklyTests(FeaturedTestCase):
    def fetch(self, key, searches=SEARCHES):
        routes = {
            "all-weeks-global.tsv": latin1(load_fixture("netflix", "weekly.tsv")),
            "/search/": tmdb_search(searches),
        }
        with no_overrides(), api_routes(routes) as api:
            return ADAPTERS["netflix"].fetch(few_rows(get(key))), api

    def test_latest_week_of_one_category_with_tmdb_ids(self):
        result_, _ = self.fetch("netflix-top-10-films-english")
        self.assertEqual(result_.edition, "Week of Oct 12, 2026")
        self.assertEqual([entry.title for entry in result_.entries], ["Animals", "Ice Age"])
        animals, ice_age = result_.entries
        # the most popular exact title wins: this year's Animals, not 1998's
        self.assertEqual((animals.rank, animals.media_type, animals.ids), (1, "movie", {"tmdb": "1236045"}))
        self.assertEqual(animals.image, "https://image.tmdb.org/t/p/w500/p.jpg")
        self.assertEqual(ice_age.ids, {"tmdb": "425"})

    def test_tv_strips_season_labels_and_keeps_them_as_notes(self):
        result_, api = self.fetch("netflix-top-10-tv-english")
        self.assertEqual([entry.key for entry in result_.entries], ["tmdb:tv:285322", "tmdb:tv:203857"])
        self.assertEqual([entry.note for entry in result_.entries], ["Limited Series", "Season 4"])
        queries = [call.kwargs["params"]["query"] for call in api.call_args_list if "/search/" in call.args[2]]
        self.assertEqual(queries, ["Below", "The Diplomat"])

    def test_non_english_chart_skips_english_originals_and_prefers_new_first_seasons(self):
        result_, _ = self.fetch("netflix-top-10-tv-non-english")
        lovesick, jojo = result_.entries
        self.assertEqual(lovesick.ids, {"tmdb": "290234"})  # not the 2014 British show
        self.assertEqual(jojo.title, "STEEL BALL RUN JoJo’s Bizarre Adventure")  # decoded as UTF-8
        self.assertEqual(jojo.key, "t:steel ball run jojos bizarre adventure|")  # unmatched: left to the resolver

    def test_recent_first_season_beats_a_more_popular_old_show(self):
        searches = {
            **SEARCHES,
            ("tv", "lovesick"): [
                result(1781, "Lovesick", "1974-09-11", language="es", popularity=68.4),
                result(290234, "Lovesick", "2026-09-02", language="es", popularity=9.1),
            ],
        }
        result_, _ = self.fetch("netflix-top-10-tv-non-english", searches)
        self.assertEqual(result_.entries[0].ids, {"tmdb": "290234"})

    def test_an_override_on_the_title_key_skips_the_adapter_match(self):
        with patch.object(overrides, "table", return_value={("netflix", "t:animals|"): "tmdb:movie:1"}):
            routes = {
                "all-weeks-global.tsv": load_fixture("netflix", "weekly.tsv"),
                "/search/": tmdb_search(SEARCHES),
            }
            with api_routes(routes):
                result_ = ADAPTERS["netflix"].fetch(few_rows(get("netflix-top-10-films-english")))
        self.assertEqual(result_.entries[0].key, "t:animals|")
        self.assertEqual(result_.entries[1].ids, {"tmdb": "425"})

    def test_tmdb_search_errors_leave_entries_to_the_resolver(self):
        routes = {
            "all-weeks-global.tsv": load_fixture("netflix", "weekly.tsv"),
            "/search/": requests.ConnectionError("down"),
        }
        with no_overrides(), api_routes(routes):
            result_ = ADAPTERS["netflix"].fetch(few_rows(get("netflix-top-10-films-english")))
        self.assertEqual([entry.key for entry in result_.entries], ["t:animals|", "t:ice age|"])

    def test_missing_columns_or_category_raise(self):
        with api_routes({"all-weeks-global.tsv": "week\tcategory\n2026-10-18\tFilms (English)\n"}):
            with self.assertRaisesMessage(AdapterError, "missing columns"):
                ADAPTERS["netflix"].fetch(few_rows(get("netflix-top-10-films-english")))
        header = load_fixture("netflix", "weekly.tsv").splitlines()[0]
        with api_routes({"all-weeks-global.tsv": header + "\n"}), self.assertRaisesMessage(AdapterError, "no weekly rows"):
            ADAPTERS["netflix"].fetch(few_rows(get("netflix-top-10-films-english")))

    def test_runs_through_the_engine(self):
        routes = {
            "all-weeks-global.tsv": load_fixture("netflix", "weekly.tsv"),
            "/search/": tmdb_search(SEARCHES),
        }
        definition = few_rows(get("netflix-top-10-films-english"))
        with no_overrides(), api_routes(routes):
            fetched = ADAPTERS["netflix"].fetch(definition)
        run = self.run_list(definition, fetch_result=fetched)
        self.assertTrue(run.ok, run.message)
        self.assertListRefs(run.custom_list, ["tmdb:movie:1236045", "tmdb:movie:425"])


class NetflixMostPopularTests(FeaturedTestCase):
    def test_merges_categories_by_views_and_lists_a_show_once(self):
        searches = {
            ("tv", "wednesday"): [result(119051, "Wednesday", "2022-11-23", popularity=200.0)],
            ("tv", "dahmer"): [],
            ("tv", "monster: the jeffrey dahmer story"): [],
            ("tv", "dahmer: monster: the jeffrey dahmer story"): [
                result(113988, "DAHMER - Monster: The Jeffrey Dahmer Story", "2022-09-21"),
            ],
            ("tv", "squid game"): [result(93405, "Squid Game", "2021-09-17", language="ko")],
        }
        routes = {"most-popular.tsv": load_fixture("netflix", "most-popular.tsv"), "/search/": tmdb_search(searches)}
        with no_overrides(), api_routes(routes):
            fetched = ADAPTERS["netflix"].fetch(few_rows(get("netflix-most-popular-tv")))
        self.assertEqual(
            [(entry.rank, entry.key, entry.note) for entry in fetched.entries],
            [
                (1, "tmdb:tv:93405", "Season 1"),
                (2, "tmdb:tv:119051", "Season 1"),
                (3, "tmdb:tv:113988", "Monster: The Jeffrey Dahmer Story"),
            ],
        )
        self.assertEqual(fetched.edition, "")


class TMDBMatchTests(FeaturedTestCase):
    def test_year_bounds_and_vote_count(self):
        searches = {
            ("movie", "a star is born"): [
                result(332562, "A Star Is Born", "2018-10-03", votes=12000),
                result(19610, "A Star Is Born", "1976-12-17", votes=300),
            ],
        }
        with api_routes({"/search/": tmdb_search(searches)}):
            match = tmdb_match.best_match("movie", ["A Star Is Born"], min_year=1975, max_year=1977, prefer="vote_count")
            newest = tmdb_match.best_match("movie", ["A Star Is Born"], prefer="vote_count")
        self.assertEqual(match["id"], 19610)
        self.assertEqual(newest["id"], 332562)

    def test_matches_original_titles_and_caches_searches(self):
        searches = {("tv", "die falle"): [result(283371, "The Trap", "2026-10-13", original="Die Falle", language="de")]}
        with api_routes({"/search/": tmdb_search(searches)}) as api:
            first = tmdb_match.best_match("tv", ["Die Falle"])
            second = tmdb_match.best_match("tv", ["Die Falle"])
        self.assertEqual((first["id"], second["id"]), (283371, 283371))
        self.assertEqual(api.call_count, 1)
