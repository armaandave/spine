from lists.featured.adapters import ADAPTERS, goldenglobes
from lists.featured.registry import get
from lists.featured.types import AdapterError
from lists.tests.featured.helpers import FeaturedTestCase, api_routes, load_fixture, make_definition, no_overrides
from lists.tests.featured.test_netflix import result, tmdb_search


def by_include(payload):
    """Return only the records whose ids the request asked for."""

    def respond(url, params):  # noqa: ARG001
        wanted = {int(value) for value in params["include"].split(",")}
        return [row for row in payload if row["id"] in wanted]

    return respond


class GoldenGlobesHelpersTests(FeaturedTestCase):
    def test_display_title(self):
        self.assertEqual(goldenglobes.display_title("Fabelmans, The"), ("The Fabelmans", None))
        self.assertEqual(goldenglobes.display_title("Star Is Born, A (1976)"), ("A Star Is Born", 1976))
        self.assertEqual(goldenglobes.display_title("Dynasty (1981-1989)"), ("Dynasty", 1981))
        self.assertEqual(goldenglobes.display_title("Office, The (UK)"), ("The Office", None))
        self.assertEqual(goldenglobes.display_title("Fame &#8211; TV Show"), ("Fame", None))
        self.assertEqual(goldenglobes.display_title("M*A*S*H (TV Show)"), ("M*A*S*H", None))

    def test_validate(self):
        with self.assertRaises(ValueError):
            goldenglobes.validate(make_definition(adapter="goldenglobes", params={"category": "5"}, is_ranked=False))
        with self.assertRaises(ValueError):
            goldenglobes.validate(make_definition(adapter="goldenglobes", params={"category": 5}))  # ranked
        with self.assertRaises(ValueError):
            goldenglobes.validate(
                make_definition(adapter="goldenglobes", params={"category": 5}, is_ranked=False, media_types=("movie", "tv")),
            )


class GoldenGlobesAdapterTests(FeaturedTestCase):
    def test_tv_winners_newest_first_one_row_per_win(self):
        searches = {
            ("tv", "the pitt"): [result(250307, "The Pitt", "2025-01-09", votes=900)],
            ("tv", "succession"): [
                result(76331, "Succession", "2018-06-03", votes=5000),
                result(999, "Succession", "2030-01-01", votes=9000),  # premiered after its wins: not it
            ],
            ("tv", "l.a. law"): [
                result(732, "L.A. Law", "1986-09-15", votes=200),
                result(998, "L.A. Law", "2026-01-01", votes=300),
            ],
        }
        routes = {
            "award-categories/33": load_fixture("goldenglobes", "category_33.json"),
            "/nomination": load_fixture("goldenglobes", "nominations_33.json"),
            "/tv-show": by_include(load_fixture("goldenglobes", "tv_shows.json")),
            "/search/": tmdb_search(searches),
        }
        with no_overrides(), api_routes(routes) as api:
            fetched = ADAPTERS["goldenglobes"].fetch(get("golden-globes-best-television-series-drama"))
        self.assertEqual(fetched.edition, "2026")
        self.assertEqual(
            [(entry.rank, entry.title, entry.key, entry.note) for entry in fetched.entries],
            [
                (None, "The Pitt", "tmdb:tv:250307", "2026"),
                (None, "Succession", "tmdb:tv:76331", "2024"),
                (None, "Succession", "tmdb:tv:76331", "2022"),
                (None, "L.A. Law", "tmdb:tv:732", "1988"),
            ],
        )
        # The show is matched once, however many times it won.
        self.assertEqual(sum("/search/" in call.args[2] and "succession" in str(call.kwargs.get("params")).lower() for call in api.call_args_list), 1)
        nomination_call = next(call for call in api.call_args_list if "/nomination" in call.args[2])
        self.assertEqual(nomination_call.kwargs["params"]["award-categories"], 33)

        run = self.run_list(get("golden-globes-best-television-series-drama"), fetch_result=fetched)
        self.assertTrue(run.ok, run.message)
        self.assertListRefs(run.custom_list, ["tmdb:tv:250307", "tmdb:tv:76331", "tmdb:tv:76331", "tmdb:tv:732"])
        self.assertEqual(
            list(run.custom_list.customlistitem_set.values_list("position", "occurrence", "note")),
            [(1, 0, "2026"), (2, 0, "2024"), (3, 1, "2022"), (4, 0, "1988")],
        )

    def test_films_use_release_year_and_fall_back_to_tv_show_records(self):
        searches = {
            ("movie", "a star is born"): [
                result(332562, "A Star Is Born", "2018-10-03", votes=12000),
                result(19610, "A Star Is Born", "1976-12-17", votes=300),
            ],
            ("movie", "babe"): [result(9598, "Babe", "1995-07-18")],
            ("movie", "the graduate"): [result(37247, "The Graduate", "1967-12-21")],
        }
        routes = {
            "award-categories/7": load_fixture("goldenglobes", "category_7.json"),
            "/nomination": load_fixture("goldenglobes", "nominations_7.json"),
            "/film": by_include(load_fixture("goldenglobes", "films_7.json")),
            "/tv-show": by_include(load_fixture("goldenglobes", "tv_shows_7.json")),
            "/search/": tmdb_search(searches),
        }
        with no_overrides(), api_routes(routes):
            fetched = ADAPTERS["goldenglobes"].fetch(get("golden-globes-best-motion-picture-musical-or-comedy"))
        self.assertEqual(
            [(entry.title, entry.year, entry.key) for entry in fetched.entries],
            [
                ("Babe", 1995, "tmdb:movie:9598"),
                ("A Star Is Born", 1976, "tmdb:movie:19610"),
                ("The Graduate", 1967, "tmdb:movie:37247"),
            ],
        )

    def test_failures(self):
        with api_routes({"award-categories/33": {"id": 33, "count": 0}}), self.assertRaisesMessage(AdapterError, "no nominations"):
            ADAPTERS["goldenglobes"].fetch(get("golden-globes-best-television-series-drama"))
        routes = {
            "award-categories/33": load_fixture("goldenglobes", "category_33.json"),
            "/nomination": load_fixture("goldenglobes", "nominations_33.json"),
            "/tv-show": [],
            "/film": [],
        }
        with api_routes(routes), self.assertRaisesMessage(AdapterError, "records missing"):
            ADAPTERS["goldenglobes"].fetch(get("golden-globes-best-television-series-drama"))

    def test_malformed_responses_are_adapter_errors(self):
        definition = get("golden-globes-best-television-series-drama")
        category = load_fixture("goldenglobes", "category_33.json")
        bad = (
            ({"award-categories/33": ["not", "a", "term"]}, "unexpected award-categories response"),
            ({"award-categories/33": category, "/nomination": {"code": "rest_error"}}, "unexpected nomination response"),
            ({"award-categories/33": category, "/nomination": ["oops"]}, "unreadable nomination"),
            (
                {
                    "award-categories/33": category,
                    "/nomination": [{"id": 9, "acf": {"is_winner": True, "film_tv_show": 1, "year": "TBA"}}],
                },
                "nomination 9 has an unreadable year",
            ),
            (
                {
                    "award-categories/33": category,
                    "/nomination": [{"id": 9, "acf": {"is_winner": True, "film_tv_show": 1, "year": 2026}}],
                    "/tv-show": [{"title": {"rendered": "No id"}}],
                },
                "unreadable tv-show record",
            ),
        )
        for routes, message in bad:
            with self.subTest(message), api_routes(routes), self.assertRaisesMessage(AdapterError, message):
                ADAPTERS["goldenglobes"].fetch(definition)
