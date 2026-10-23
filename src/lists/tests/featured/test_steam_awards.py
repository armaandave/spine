from datetime import UTC, datetime
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from lists.featured.adapters import ADAPTERS
from lists.featured.adapters import steam_awards
from lists.featured.types import AdapterError
from lists.tests.featured.game_routes import external, igdb_game, igdb_routes
from lists.tests.featured.helpers import api_routes, load_fixture, make_definition

PAGE_2025 = load_fixture("steam_awards", "2025.html")
PAGE_2018 = load_fixture("steam_awards", "2018.html")
SILKSONG, MIDNIGHT_WALK, PUBG = "1030300", "2863640", "578080"


def awards_definition(**params):
    return make_definition(key="steam-awards-test", adapter="steam_awards", media_types=("game",), position=551, params=params)


def pages(url, params):  # noqa: ARG001
    """Steam serves 2025 and 2018; any other year redirects to the latest page (2025)."""
    return PAGE_2018 if url.endswith("/2018") else PAGE_2025


def awards_routes():
    return igdb_routes(
        extra={"store.steampowered.com/steamawards/": pages},
        externals=[
            external(SILKSONG, igdb_game(115289, "Hollow Knight: Silksong", 1756944000, "co9ab1")),
            external(PUBG, igdb_game(27789, "PUBG: Battlegrounds", 1513728000, "co5vmg")),
        ],
    )


class SteamAwardsParsingTests(SimpleTestCase):
    def test_config_page(self):
        categories = steam_awards.parse_page(PAGE_2025, 2025)
        self.assertEqual(
            [(category.name, category.title, category.winner) for category in categories],
            [
                ("GOTY", "Game of the Year", SILKSONG),
                ("VRGOTY", "VR Game of the Year", MIDNIGHT_WALK),
                ("BestGameYouSuckAt", "Best Game You Suck At", SILKSONG),
            ],
        )
        self.assertEqual(len(categories[0].nominees), 5)
        self.assertIn(SILKSONG, categories[0].nominees)

    def test_legacy_page(self):
        categories = steam_awards.parse_page(PAGE_2018, 2018)
        self.assertEqual(
            [(category.name, category.winner, category.winner_name) for category in categories],
            [("GOTY", PUBG, "PUBG: BATTLEGROUNDS"), ("Best Developer", "", "")],  # a developer, not an app
        )

    def test_an_unreadable_config_is_an_adapter_error(self):
        for raw in ("{not json", "[1, 2]", "&quot;text&quot;"):
            page = f'<div data-steam_awards_config="{raw}"></div>'
            with self.subTest(raw=raw), self.assertRaises(AdapterError):
                steam_awards.parse_page(page, 2025)
        # A config without votes yet isn't this year's page.
        self.assertIsNone(steam_awards.parse_page('<div data-steam_awards_config="{}"></div>', 2025))

    def test_a_page_for_another_year_is_rejected(self):
        # /steamawards/2026 redirects to the 2025 page until Steam publishes 2026.
        self.assertIsNone(steam_awards.parse_page(PAGE_2025, 2026))
        self.assertIsNone(steam_awards.parse_page(PAGE_2018, 2019))
        self.assertIsNone(steam_awards.parse_page("<html>No awards here</html>", 2025))


@override_settings(IGDB_ID="test-igdb-id", IGDB_SECRET="test-igdb-secret")
class SteamAwardsAdapterTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_year_merges_a_game_winning_several_categories(self):
        with api_routes(awards_routes()):
            result = ADAPTERS["steam_awards"].fetch(awards_definition(year=2025))

        self.assertEqual(result.edition, "2025")
        self.assertEqual(result.live_label, "")
        self.assertEqual(
            [(entry.title, entry.key, entry.note) for entry in result.entries],
            [
                ("Hollow Knight: Silksong", "igdb:115289", "Game of the Year · Best Game You Suck At"),
                ("", f"steam:{MIDNIGHT_WALK}", "VR Game of the Year"),  # IGDB doesn't know it here
            ],
        )
        self.assertEqual(result.entries[0].image, "https://images.igdb.com/igdb/image/upload/t_original/co9ab1.jpg")

    def test_year_before_winners_are_announced(self):
        page = PAGE_2025.replace("&quot;winner_appid&quot;: 2863640", "&quot;winner_appid&quot;: 0")
        routes = {**awards_routes(), "store.steampowered.com/steamawards/": page}
        with api_routes(routes):
            result = ADAPTERS["steam_awards"].fetch(awards_definition(year=2025))
        self.assertEqual(result.live_label, "2 of 3 announced")
        self.assertEqual(len(result.entries), 1)

    def test_year_without_a_page(self):
        with api_routes(awards_routes()), self.assertRaisesMessage(AdapterError, "no Steam Awards page for 2026"):
            ADAPTERS["steam_awards"].fetch(awards_definition(year=2026))

    def test_category_across_years_newest_first(self):
        now = datetime(2026, 10, 21, tzinfo=UTC)
        with api_routes(awards_routes()) as api, patch("django.utils.timezone.now", return_value=now):
            result = ADAPTERS["steam_awards"].fetch(awards_definition(category="GOTY", since=2018))

        self.assertEqual(result.edition, "2018–2025")
        self.assertEqual(
            [(entry.title, entry.note) for entry in result.entries],
            [("Hollow Knight: Silksong", "2025"), ("PUBG: Battlegrounds", "2018")],
        )
        fetched = [call.args[2] for call in api.call_args_list if "steamawards" in call.args[2]]
        self.assertEqual(fetched[0], "https://store.steampowered.com/steamawards/2026")
        self.assertEqual(len(fetched), 9)  # 2026 back to 2018

    def test_validate(self):
        validate = ADAPTERS["steam_awards"].validate
        validate(awards_definition(year=2025))
        validate(awards_definition(category="GOTY", since=2018))
        for params, message in (
            ({}, "exactly one"),
            ({"year": 2025, "category": "GOTY", "since": 2018}, "exactly one"),
            ({"year": 2010}, "year must be"),
            ({"category": "GOTY"}, "since"),
        ):
            with self.subTest(params=params), self.assertRaisesMessage(ValueError, message):
                validate(awards_definition(**params))
