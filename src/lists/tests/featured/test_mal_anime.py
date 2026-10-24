from datetime import date
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from lists.featured.adapters import ADAPTERS
from lists.featured.adapters import mal_anime
from lists.featured.types import AdapterError
from lists.tests.featured.helpers import api_routes, load_fixture, make_definition


def mal_definition(**params):
    return make_definition(key="mal-test", adapter="mal_anime", media_types=("anime",), position=600, params=params)


@override_settings(MAL_API="test-mal-client-id")
class MALAnimeAdapterTests(SimpleTestCase):
    def test_ranking(self):
        with api_routes({"api.myanimelist.net/v2/anime/ranking": load_fixture("mal_anime", "ranking_all.json")}) as api:
            result = ADAPTERS["mal_anime"].fetch(mal_definition(ranking="all", limit=3))

        first = result.entries[0]
        self.assertEqual(
            (first.rank, first.title, first.year, first.creator, first.media_type, first.key),
            (1, "Sousou no Frieren", 2023, "Madhouse", "anime", "mal:anime:52991"),
        )
        self.assertEqual(first.image, "https://cdn.myanimelist.net/images/anime/1015/138006l.jpg")
        self.assertEqual([entry.rank for entry in result.entries], [1, 2, 3])
        self.assertEqual(result.edition, "")
        call = api.call_args_list[0]
        self.assertEqual(call.kwargs["params"]["ranking_type"], "all")
        self.assertEqual(call.kwargs["params"]["limit"], 3)
        self.assertEqual(call.kwargs["headers"]["X-MAL-CLIENT-ID"], "test-mal-client-id")

    def test_this_season_keeps_premieres(self):
        routes = {"api.myanimelist.net/v2/anime/season/2026/fall": load_fixture("mal_anime", "season_fall_2026.json")}
        with api_routes(routes) as api, patch("django.utils.timezone.localdate", return_value=date(2026, 10, 21)):
            result = ADAPTERS["mal_anime"].fetch(mal_definition(season="current", limit=2))

        self.assertEqual(result.edition, "Fall 2026")
        # One Piece (1999) is on MAL's fall list as a continuing show, so it's left out.
        self.assertEqual(
            [entry.title for entry in result.entries],
            ["Kusuriya no Hitorigoto 3rd Season", "Black Clover 2nd Season"],
        )
        self.assertEqual(api.call_args_list[0].kwargs["params"]["sort"], "anime_num_list_users")

    def test_current_season(self):
        self.assertEqual(mal_anime.current_season(date(2027, 1, 1)), (2027, "winter"))
        self.assertEqual(mal_anime.current_season(date(2027, 4, 30)), (2027, "spring"))
        self.assertEqual(mal_anime.current_season(date(2027, 9, 30)), (2027, "summer"))
        self.assertEqual(mal_anime.current_season(date(2026, 12, 31)), (2026, "fall"))

    def test_empty_response(self):
        with api_routes({"anime/ranking": {"data": []}}), self.assertRaisesMessage(AdapterError, "no anime"):
            ADAPTERS["mal_anime"].fetch(mal_definition(ranking="airing"))

    def test_validate(self):
        validate = ADAPTERS["mal_anime"].validate
        validate(mal_definition(ranking="bypopularity"))
        validate(mal_definition(season="current"))
        for params, message in (
            ({}, "exactly one"),
            ({"ranking": "favorite"}, "ranking must be one of"),
            ({"season": "fall"}, 'season must be "current"'),
            ({"ranking": "all", "limit": 600}, "limit must be between"),
        ):
            with self.subTest(params=params), self.assertRaisesMessage(ValueError, message):
                validate(mal_definition(**params))
        with self.assertRaisesMessage(ValueError, "anime lists"):
            validate(make_definition(adapter="mal_anime", params={"ranking": "all"}))
