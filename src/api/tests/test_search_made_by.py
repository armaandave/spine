"""Who made each search result, and its year: the line under a result's title."""

import threading
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from api.serializers.common import made_by_from_payload, year_from_payload
from api.services import media as media_service
from app.models import MediaTypes, Sources


def row(media_type, media_id, source=Sources.TMDB.value, **fields):
    """Return a search result as the summary serializer leaves it."""
    return {"ref": {"source": source, "media_type": media_type, "media_id": media_id}, "made_by": [], **fields}


class SearchRowYearTests(TestCase):
    """A row's year, however its provider says it."""

    def test_the_year_reads_dates_bare_years_and_igdb_times(self):
        self.assertEqual(year_from_payload({"release_date": "2021-09-15"}), 2021)
        self.assertEqual(year_from_payload({"release_date": "", "start_year": "1986"}), 1986)
        self.assertEqual(year_from_payload({"release_year": 2024}), 2024)
        # IGDB's first release, a Unix time: 4 September 2025.
        self.assertEqual(year_from_payload({"first_release_date": 1756944000}), 2025)
        self.assertIsNone(year_from_payload({"release_date": None, "start_year": "TBA"}))


class SearchRowMadeByTests(TestCase):
    """Who made a row, as its provider names them."""

    def test_music_credits_its_artist_as_one_name(self):
        payload = {
            "artist_credits": [
                {"name": "Kendrick Lamar", "join_phrase": " & "},
                {"name": "SZA", "join_phrase": ""},
            ],
        }

        self.assertEqual(made_by_from_payload(payload), ["Kendrick Lamar & SZA"])
        # Credits with no names fall back to the row's artist.
        blank = {"artist_credits": [{"name": "", "join_phrase": ""}], "artist_name": "Toto"}
        self.assertEqual(made_by_from_payload(blank), ["Toto"])

    def test_names_are_trimmed_and_said_once(self):
        payload = {"made_by": [" Alan Moore ", "Dave Gibbons", "Alan Moore", "", None]}

        self.assertEqual(made_by_from_payload(payload), ["Alan Moore", "Dave Gibbons"])


class SearchMadeByLookupTests(TestCase):
    """Movies and shows: TMDB's search doesn't say who made them; search looks it up."""

    def setUp(self):
        cache.clear()

    @patch("api.services.media.tmdb.made_by")
    def test_movies_and_shows_name_who_made_them_and_keep_it(self, made_by):
        made_by.side_effect = lambda _media_type, media_id, **_: {
            "438631": ["Denis Villeneuve"],
            "95396": ["Apple TV"],
        }[media_id]
        book = row(MediaTypes.BOOK.value, "1", Sources.HARDCOVER.value, made_by=["Percival Everett"])

        results = media_service.with_made_by(
            [row(MediaTypes.MOVIE.value, "438631"), row(MediaTypes.TV.value, "95396"), book],
        )
        again = media_service.with_made_by([row(MediaTypes.MOVIE.value, "438631")])

        self.assertEqual(
            [result["made_by"] for result in results],
            [["Denis Villeneuve"], ["Apple TV"], ["Percival Everett"]],
        )
        self.assertEqual(again[0]["made_by"], ["Denis Villeneuve"])
        self.assertEqual(made_by.call_count, 2)

    @patch("api.services.media.tmdb.made_by", side_effect=RuntimeError("TMDB is down"))
    def test_a_failed_lookup_leaves_the_year_alone_and_waits_a_while_to_try_again(self, made_by):
        first = media_service.with_made_by([row(MediaTypes.MOVIE.value, "1")])
        second = media_service.with_made_by([row(MediaTypes.MOVIE.value, "1")])
        cache.clear()
        later = media_service.with_made_by([row(MediaTypes.MOVIE.value, "1")])

        self.assertEqual([first[0]["made_by"], second[0]["made_by"], later[0]["made_by"]], [[], [], []])
        # The second search doesn't ask TMDB again; once the failure is
        # forgotten (the cache cleared, as MADE_BY_FAILED_TTL would), it does.
        self.assertEqual(made_by.call_count, 2)

    @patch("api.services.media.MADE_BY_WAIT_SECONDS", 0.05)
    @patch("api.services.media.tmdb.made_by")
    def test_a_slow_lookup_still_names_them_on_the_next_search(self, made_by):
        answer = threading.Event()

        def slow(media_type, media_id, **_):
            answer.wait(5)
            return ["Ryan Coogler"]

        made_by.side_effect = slow

        first = media_service.with_made_by([row(MediaTypes.MOVIE.value, "1233413")])
        answer.set()
        for lookup in list(media_service._made_by_lookups.values()):
            lookup.result(timeout=5)
        second = media_service.with_made_by([row(MediaTypes.MOVIE.value, "1233413")])

        self.assertEqual(first[0]["made_by"], [])
        self.assertEqual(second[0]["made_by"], ["Ryan Coogler"])
        self.assertEqual(made_by.call_count, 1)

    @patch("api.services.media.tmdb.made_by", return_value=["Denis Villeneuve"])
    @patch("api.views.media.media_service.search_media")
    def test_search_answers_name_who_made_them(self, search_media, made_by):
        search_media.return_value = [{**row(MediaTypes.MOVIE.value, "438631"), "title": "Dune"}]
        client = APIClient()
        client.force_authenticate(get_user_model().objects.create_user(username="searcher"))

        response = client.get("/api/v1/media/search/", {"media_type": MediaTypes.MOVIE.value, "q": "dune"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"][0]["made_by"], ["Denis Villeneuve"])
