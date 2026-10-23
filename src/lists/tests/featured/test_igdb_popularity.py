from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from lists.featured.adapters import ADAPTERS
from lists.featured.types import AdapterError
from lists.tests.featured.game_routes import bodies, igdb_game, igdb_routes
from lists.tests.featured.helpers import api_routes, make_definition

CALCULATED_AT = 1792635342  # 2026-10-22 02:15 UTC
PRIMITIVES = [
    {"id": 1, "game_id": 301298, "value": 0.00039, "calculated_at": CALCULATED_AT},
    {"id": 2, "game_id": 92550, "value": 0.00038, "calculated_at": CALCULATED_AT},
]


def popularity_definition(**params):
    return make_definition(key="igdb-test", adapter="igdb_popularity", media_types=("game",), position=543, params=params)


@override_settings(IGDB_ID="test-igdb-id", IGDB_SECRET="test-igdb-secret")
class IGDBPopularityAdapterTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_chart(self):
        routes = igdb_routes(
            extra={"v4/popularity_primitives": PRIMITIVES},
            games=[igdb_game(92550, "Fable", 1803254400, "co8abc"), igdb_game(301298, "Deadlock")],
        )
        with api_routes(routes) as api:
            result = ADAPTERS["igdb_popularity"].fetch(popularity_definition(popularity_type=10, limit=2))

        self.assertEqual(result.edition, "Oct 22, 2026")
        self.assertEqual(
            [(entry.rank, entry.title, entry.year, entry.key) for entry in result.entries],
            [(1, "Deadlock", None, "igdb:301298"), (2, "Fable", 2027, "igdb:92550")],
        )
        self.assertEqual(result.entries[1].image, "https://images.igdb.com/igdb/image/upload/t_original/co8abc.jpg")
        self.assertEqual(
            bodies(api, "v4/popularity_primitives"),
            ["fields game_id,value,calculated_at; where popularity_type = 10; sort value desc; limit 2;"],
        )
        self.assertIn("where id = (301298,92550)", bodies(api, "v4/games")[0])

    def test_unknown_games_keep_their_id(self):
        routes = igdb_routes(extra={"v4/popularity_primitives": PRIMITIVES[:1]})
        with api_routes(routes):
            result = ADAPTERS["igdb_popularity"].fetch(popularity_definition(popularity_type=3))
        self.assertEqual([(entry.title, entry.key) for entry in result.entries], [("", "igdb:301298")])

    def test_empty_chart(self):
        with api_routes(igdb_routes(extra={"v4/popularity_primitives": []})), self.assertRaisesMessage(
            AdapterError,
            "IGDB returned no Want to Play chart",
        ):
            ADAPTERS["igdb_popularity"].fetch(popularity_definition(popularity_type=2))

    def test_validate(self):
        validate = ADAPTERS["igdb_popularity"].validate
        validate(popularity_definition(popularity_type=10))
        with self.assertRaisesMessage(ValueError, "popularity_type must be one of"):
            validate(popularity_definition(popularity_type=34))
        with self.assertRaisesMessage(ValueError, "limit must be between"):
            validate(popularity_definition(popularity_type=10, limit=501))
