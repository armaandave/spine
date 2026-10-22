import json

import requests
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from lists.featured import engine
from lists.featured.adapters import ADAPTERS, _igdb_games
from lists.featured.adapters import steam as steam_adapter
from lists.featured.definitions.games import DEFINITIONS
from lists.featured.types import AdapterError
from lists.tests.featured.game_routes import bodies, external, igdb_game, igdb_routes
from lists.tests.featured.helpers import FeaturedTestCase, api_routes, load_fixture, make_definition

CS2_RELEASED = 1695772800  # 2023-09-27
AION2_RELEASED = 1763424000  # 2025-11-18


def steam_definition(**params):
    return make_definition(key="steam-test", adapter="steam", media_types=("game",), position=540, params=params)


def weekly_routes(**igdb):
    return igdb_routes(
        extra={"GetWeeklyTopSellers": load_fixture("steam", "weekly_top_sellers.json")},
        externals=igdb.get(
            "externals",
            [
                external(730, igdb_game(242408, "Counter-Strike 2", CS2_RELEASED, "co7989")),
                external(3393110, igdb_game(117297, "Aion 2", AION2_RELEASED)),
            ],
        ),
    )


def most_played_routes(externals):
    return igdb_routes(extra={"GetMostPlayedGames": load_fixture("steam", "most_played.json")}, externals=externals)


@override_settings(IGDB_ID="test-igdb-id", IGDB_SECRET="test-igdb-secret")
class SteamAdapterTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_weekly_top_sellers_keeps_released_games_only(self):
        with api_routes(weekly_routes()) as api:
            result = ADAPTERS["steam"].fetch(steam_definition(chart="weekly_top_sellers", country="US"))

        self.assertEqual(result.edition, "Week of Oct 13, 2026")
        # The preorder (#1), Steam Deck (#13), a DLC (#21) and a package row are dropped.
        self.assertEqual(
            [(entry.rank, entry.title) for entry in result.entries],
            [(1, "Aion 2"), (2, "Counter-Strike 2")],
        )
        cs2 = result.entries[1]
        self.assertEqual(cs2.ids, {"steam": "730", "igdb": "242408"})
        self.assertEqual(cs2.key, "igdb:242408")
        self.assertEqual(cs2.year, 2023)
        self.assertEqual(cs2.image, "https://images.igdb.com/igdb/image/upload/t_original/co7989.jpg")
        self.assertEqual(result.entries[0].image, "")  # IGDB has no cover: the writer fetches one

        steam_call = next(call for call in api.call_args_list if "GetWeeklyTopSellers" in call.args[2])
        body = json.loads(steam_call.kwargs["params"]["input_json"])
        self.assertEqual((body["country_code"], body["page_count"]), ("US", 100))
        [igdb_body] = bodies(api, "v4/external_games")
        self.assertIn('where uid = ("3393110","730") & external_game_source = 1;', igdb_body)
        token_call = next(call for call in api.call_args_list if "oauth2/token" in call.args[2])
        self.assertEqual(token_call.kwargs["data"]["client_id"], "test-igdb-id")

    def test_limit_counts_games_only(self):
        with api_routes(weekly_routes()):
            result = ADAPTERS["steam"].fetch(steam_definition(chart="weekly_top_sellers", limit=2))
        self.assertEqual([entry.rank for entry in result.entries], [1, 2])

    def test_most_played_drops_software(self):
        routes = most_played_routes([external(570, igdb_game(2963, "Dota 2"))])
        with api_routes(routes):
            result = ADAPTERS["steam"].fetch(steam_definition(chart="most_played"))

        self.assertEqual(result.edition, "Oct 20, 2026")
        # Wallpaper Engine (software) is dropped; appids IGDB doesn't know keep their steam id.
        self.assertEqual(
            [(entry.rank, entry.title, entry.key) for entry in result.entries],
            [(1, "Counter-Strike 2", "steam:730"), (2, "Dota 2", "igdb:2963"), (3, "Call of Duty®", "steam:1938090")],
        )

    def test_corrections_replace_igdb_mapping(self):
        routes = igdb_routes(
            games=[igdb_game(117263, "Mahjong Soul", None, "co2abc")],
            externals=[external(730, igdb_game(242408, "Counter-Strike 2"))],
        )
        with api_routes(routes) as api:
            found = _igdb_games.games_by_external(["1329410", "730"], 1, steam_adapter.IGDB_CORRECTIONS)

        self.assertEqual(found["1329410"].igdb_id, "117263")
        self.assertEqual(found["1329410"].name, "Mahjong Soul")
        self.assertEqual(found["730"].igdb_id, "242408")
        self.assertIn("where id = (117263)", bodies(api, "v4/games")[0])
        self.assertNotIn("1329410", bodies(api, "v4/external_games")[0])

    def test_external_mapping_keeps_the_oldest_row(self):
        routes = igdb_routes(
            externals=[
                external(730, igdb_game(242408, "Counter-Strike 2")),
                external(730, igdb_game(1372, "Counter-Strike: Global Offensive")),
            ],
        )
        with api_routes(routes) as api:
            found = _igdb_games.games_by_external(["730"], 1)
        self.assertEqual(found["730"].igdb_id, "242408")
        self.assertIn("sort id asc", bodies(api, "v4/external_games")[0])

    def test_failures_are_adapter_errors(self):
        empty = igdb_routes(extra={"GetMostPlayedGames": {"response": {}}})
        with api_routes(empty), self.assertRaisesMessage(AdapterError, "Steam returned no most played chart"):
            ADAPTERS["steam"].fetch(steam_definition(chart="most_played"))

        error = requests.HTTPError(response=type("R", (), {"status_code": 503})())
        with api_routes({"GetWeeklyTopSellers": error}), self.assertRaisesMessage(AdapterError, "HTTP 503"):
            ADAPTERS["steam"].fetch(steam_definition(chart="weekly_top_sellers"))

        bad_igdb = {**weekly_routes(), "v4/external_games": {"message": "nope"}}
        with api_routes(bad_igdb), self.assertRaisesMessage(AdapterError, "unexpected response"):
            ADAPTERS["steam"].fetch(steam_definition(chart="weekly_top_sellers"))

    def test_validate(self):
        validate = ADAPTERS["steam"].validate
        validate(steam_definition(chart="most_played"))
        for params, message in (
            ({"chart": "top_rated"}, "chart must be one of"),
            ({"chart": "most_played", "country": "us"}, "two-letter"),
            ({"chart": "most_played", "limit": 101}, "limit must be between"),
        ):
            with self.subTest(params=params), self.assertRaisesMessage(ValueError, message):
                validate(steam_definition(**params))
        with self.assertRaisesMessage(ValueError, "game lists"):
            validate(make_definition(adapter="steam", params={"chart": "most_played"}))


@override_settings(IGDB_ID="test-igdb-id", IGDB_SECRET="test-igdb-secret")
class SteamSyncTests(FeaturedTestCase):
    def test_most_played_sync_writes_games_and_skips_the_launcher(self):
        definition = next(item for item in DEFINITIONS if item.key == "steam-most-played")
        covers = [
            external(730, igdb_game(242408, "Counter-Strike 2", CS2_RELEASED, "co7989")),
            external(570, igdb_game(2963, "Dota 2", None, "co9xyz")),
        ]
        with api_routes(most_played_routes(covers)):
            result = engine.run(definition, self.owner, trigger="test")

        self.assertEqual(result.status, "ok")
        # The Call of Duty® launcher app is skipped by overrides/games.csv.
        self.assertListRefs(result.custom_list, ["igdb:game:242408", "igdb:game:2963"])
        self.assertEqual(result.custom_list.featured_sync.edition, "Oct 20, 2026")
        self.assertEqual(result.custom_list.featured_sync.entry_count, 2)
