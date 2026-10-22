from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from lists.featured.adapters import ADAPTERS
from lists.featured.adapters import twitch
from lists.featured.types import AdapterError
from lists.tests.featured.game_routes import bodies, external, igdb_game, igdb_routes
from lists.tests.featured.helpers import api_routes, load_fixture, make_definition

TWITCH_SOURCE = 14


def twitch_definition(**params):
    return make_definition(key="twitch-test", adapter="twitch", media_types=("game",), position=542, params=params)


def twitch_routes(externals=None):
    return igdb_routes(
        extra={"api.twitch.tv/helix/games/top": load_fixture("twitch", "top_games.json")},
        games=[
            igdb_game(1020, "Grand Theft Auto V", 1379376000, "co2lbd"),
            igdb_game(242408, "Counter-Strike 2", 1695772800, "co7989"),
        ],
        externals=externals if externals is not None else [external(29595, igdb_game(2963, "Dota 2"), TWITCH_SOURCE)],
    )


@override_settings(IGDB_ID="test-igdb-id", IGDB_SECRET="test-igdb-secret")
class TwitchAdapterTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_top_games(self):
        with api_routes(twitch_routes()) as api:
            result = ADAPTERS["twitch"].fetch(twitch_definition(limit=100))

        # Just Chatting and I'm Only Sleeping (a sleep-stream category Twitch links to an
        # old game) are dropped; Counter-Strike is corrected to CS2; Dota 2 comes from IGDB's
        # Twitch mapping; Dressmaker (2026) falls back to a title match.
        self.assertEqual(
            [(entry.rank, entry.title, entry.year, entry.key) for entry in result.entries],
            [
                (1, "Grand Theft Auto V", 2013, "igdb:1020"),
                (2, "Counter-Strike 2", 2023, "igdb:242408"),
                (3, "Dota 2", None, "igdb:2963"),
                (4, "Dressmaker", 2026, "t:dressmaker|2026"),
            ],
        )
        self.assertEqual(result.entries[0].image, "https://images.igdb.com/igdb/image/upload/t_original/co2lbd.jpg")

        helix = next(call for call in api.call_args_list if "helix" in call.args[2])
        self.assertEqual(helix.kwargs["params"], {"first": 100})
        self.assertEqual(helix.kwargs["headers"]["Client-ID"], "test-igdb-id")
        self.assertEqual(helix.kwargs["headers"]["Authorization"], "Bearer test-token")
        [external_body] = bodies(api, "v4/external_games")
        self.assertIn('where uid = ("29595","1288749557") & external_game_source = 14;', external_body)
        # Twitch's igdb_ids (not the dropped I'm Only Sleeping), then the correction.
        self.assertEqual(
            [body.split("where ")[1] for body in bodies(api, "v4/games")],
            ["id = (1020); limit 500;", "id = (242408); limit 500;"],
        )

    def test_split_year(self):
        self.assertEqual(twitch.split_year("Dressmaker (2026)"), ("Dressmaker", 2026))
        self.assertEqual(twitch.split_year("Pokémon FireRed/LeafGreen"), ("Pokémon FireRed/LeafGreen", None))
        self.assertEqual(twitch.split_year("Tom Clancy's (Rainbow)"), ("Tom Clancy's (Rainbow)", None))

    def test_failures(self):
        routes = {**twitch_routes(), "api.twitch.tv/helix/games/top": {"data": []}}
        with api_routes(routes), self.assertRaisesMessage(AdapterError, "Twitch returned no top games"):
            ADAPTERS["twitch"].fetch(twitch_definition())

    def test_validate(self):
        validate = ADAPTERS["twitch"].validate
        validate(twitch_definition(limit=50))
        with self.assertRaisesMessage(ValueError, "limit must be between"):
            validate(twitch_definition(limit=0))
        with self.assertRaisesMessage(ValueError, "game list"):
            validate(make_definition(adapter="twitch"))
