"""Provider fields that stats store on Items: IGDB dates, album length, book first
publication, the Hardcover metadata batch and TMDB's original language."""

from datetime import UTC, datetime
from unittest.mock import patch

import requests
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from app.providers import hardcover, igdb, musicbrainz, quota, services, tmdb

RELEASE_GROUP_MBID = "3bd76d40-7f0e-36b7-9348-91a33afee20e"
RELEASE_MBID = "2d0bad69-f735-484b-bc0b-2ea54c76225e"


class IgdbDateTests(SimpleTestCase):
    @override_settings(TIME_ZONE="America/New_York")
    def test_release_timestamp_is_read_in_utc(self):
        # IGDB stores a 1 January 2020 release as 2020-01-01T00:00:00Z; read in New York
        # it used to become 31 December 2019.
        timestamp = int(datetime(2020, 1, 1, tzinfo=UTC).timestamp())

        self.assertEqual(igdb.get_start_date({"first_release_date": timestamp}), "2020-01-01")

    def test_missing_release_timestamp_is_none(self):
        self.assertIsNone(igdb.get_start_date({}))


def _release(*track_lengths):
    """A MusicBrainz release with one track per length (None leaves it out)."""
    return {
        "id": RELEASE_MBID,
        "title": "Year Zero",
        "media": [
            {
                "position": 1,
                "format": "CD",
                "tracks": [
                    {
                        "id": f"track-{index}",
                        "position": index,
                        "title": f"Track {index}",
                        "length": length,
                        "recording": {"id": f"recording-{index}"},
                    }
                    for index, length in enumerate(track_lengths, start=1)
                ],
            },
        ],
    }


@patch("app.providers.musicbrainz.lookup_release_group", return_value={"id": RELEASE_GROUP_MBID, "title": "Year Zero"})
class AlbumLengthTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def _music(self, release):
        with patch(
            "app.providers.musicbrainz.resolve_representative_release",
            return_value={"release": release, "reason": "test", "release_mbid": RELEASE_MBID},
        ):
            return musicbrainz.music(RELEASE_GROUP_MBID)

    def test_runtime_is_the_rounded_sum_of_track_lengths(self, _lookup):
        # 3:20 + 4:10 + 2:59.5 = 10:29.5, which rounds to 10 minutes.
        result = self._music(_release(200_000, 250_000, 179_500))

        self.assertEqual(result["runtime"], 10)

    def test_runtime_adds_every_disc(self, _lookup):
        release = _release(1_800_000)
        release["media"].append({**_release(1_860_000)["media"][0], "position": 2})

        self.assertEqual(self._music(release)["runtime"], 61)

    def test_no_runtime_when_a_track_has_no_length(self, _lookup):
        result = self._music(_release(200_000, None, 250_000))

        self.assertNotIn("runtime", result)

    def test_a_recording_length_stands_in_for_a_missing_track_length(self, _lookup):
        release = _release(120_000, None)
        release["media"][0]["tracks"][1]["recording"]["length"] = 60_000

        self.assertEqual(self._music(release)["runtime"], 3)

    def test_no_runtime_without_a_tracklist(self, _lookup):
        self.assertNotIn("runtime", self._music(None))

    def test_no_runtime_without_enrichment(self, _lookup):
        with patch("app.providers.musicbrainz.resolve_representative_release") as resolve:
            result = musicbrainz.music(RELEASE_GROUP_MBID, include_enrichment=False)

        resolve.assert_not_called()
        self.assertNotIn("runtime", result)


def _hardcover_book(**overrides):
    book = {
        "id": 644,
        "title": "Dune",
        "cached_image": "https://example.com/dune.jpg",
        "description": "Spice.",
        "cached_tags": [{"tag": "Science fiction"}, {"tag": "Classics"}],
        "rating": 4.3,
        "ratings_count": 100,
        "pages": 612,
        "release_date": "1965-08-01",
        "slug": "dune",
        "canonical_id": None,
        "cached_featured_series": None,
        "cached_contributors": None,
        "contributions": [],
        "default_cover_edition": {
            "edition_format": "Paperback",
            "isbn_13": "9780441172719",
            "isbn_10": None,
            "release_date": "2005-08-02",
            "publisher": {"name": "Ace"},
        },
    }
    book.update(overrides)
    return book


class HardcoverBookDateTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    @patch("app.providers.hardcover.services.api_request")
    def test_book_keeps_the_first_publication_apart_from_the_edition_date(self, api_request):
        api_request.return_value = {"data": {"books_by_pk": _hardcover_book()}}

        details = hardcover.book("644")["details"]

        self.assertEqual(details["release_date"], "2005-08-02")
        self.assertEqual(details["original_release_date"], "1965-08-01")

    @patch("app.providers.hardcover.services.api_request")
    def test_book_without_its_own_date_has_no_first_publication(self, api_request):
        api_request.return_value = {"data": {"books_by_pk": _hardcover_book(release_date=None)}}

        details = hardcover.book("644")["details"]

        self.assertEqual(details["release_date"], "2005-08-02")
        self.assertIsNone(details["original_release_date"])


def _series_row(series_id, name, book_ids):
    return {
        "id": series_id,
        "name": name,
        "primary_books_count": len(book_ids),
        "book_series": [
            {
                "position": position,
                "compilation": False,
                "book": {
                    "id": book_id,
                    "title": f"Book {book_id}",
                    "slug": f"book-{book_id}",
                    "users_read_count": 10,
                    "cached_image": None,
                },
            }
            for position, book_id in enumerate(book_ids, start=1)
        ],
    }


class FakeHardcover:
    """Answers batch queries from a table of book rows and series rows."""

    def __init__(self, books, series=()):
        self.books = {row["id"]: row for row in books}
        self.series = {row["id"]: row for row in series}
        self.calls = []

    def __call__(self, provider, method, url, params=None, headers=None, **kwargs):
        ids = params["variables"]["ids"]
        if "BookMetadataBatch" in params["query"]:
            self.calls.append(("books", ids))
            return {"data": {"books": [self.books[i] for i in ids if i in self.books]}}
        self.calls.append(("series", ids))
        return {"data": {"series": [self.series[i] for i in ids if i in self.series]}}


class HardcoverBatchTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_batch_returns_book_metadata_with_series_members(self):
        fake = FakeHardcover(
            books=[
                {
                    "id": 1,
                    "title": "Dune",
                    "cached_tags": [{"tag": "Science fiction"}],
                    "pages": 612,
                    "release_date": "1965-08-01",
                    "cached_featured_series": {"id": 9, "position": 1, "series": {"id": 70, "name": "Dune"}},
                    "default_cover_edition": {"release_date": "2005-08-02"},
                },
                {
                    "id": 2,
                    "title": "Standalone",
                    "cached_tags": None,
                    "pages": None,
                    "release_date": "1999-01-01",
                    "cached_featured_series": None,
                    "default_cover_edition": None,
                },
            ],
            series=[_series_row(70, "Dune", [1, 3])],
        )

        with patch("app.providers.hardcover.services.api_request", side_effect=fake):
            results = hardcover.book_metadata_batch(["1", "2", "404"])

        self.assertEqual(set(results), {"1", "2"})
        dune = results["1"]
        self.assertEqual(dune["media_type"], "book")
        self.assertEqual(dune["genres"], ["Science fiction"])
        self.assertEqual(dune["max_progress"], 612)
        self.assertEqual(dune["details"]["number_of_pages"], 612)
        self.assertEqual(dune["details"]["release_date"], "2005-08-02")
        self.assertEqual(dune["details"]["original_release_date"], "1965-08-01")
        self.assertEqual(dune["details"]["series_id"], 70)
        self.assertEqual(dune["details"]["series_name"], "Dune")
        self.assertEqual(dune["details"]["series_position"], 1)
        self.assertEqual([book["media_id"] for book in dune["related"]["Dune"]], [1, 3])
        standalone = results["2"]
        self.assertIsNone(standalone["genres"])
        # A 1 January date is a bare year, as in book().
        self.assertEqual(standalone["details"]["release_date"], "1999")
        self.assertEqual(standalone["details"]["original_release_date"], "1999")
        self.assertIsNone(standalone["details"]["series_id"])
        self.assertEqual(standalone["related"], {})
        self.assertEqual(fake.calls, [("books", [1, 2, 404]), ("series", [70])])

    def test_batch_sends_at_most_fifty_ids_per_request(self):
        fake = FakeHardcover(books=[])

        with patch("app.providers.hardcover.services.api_request", side_effect=fake):
            results = hardcover.book_metadata_batch([str(i) for i in range(1, 121)] + ["1"])

        self.assertEqual(results, {})
        self.assertEqual([len(ids) for _kind, ids in fake.calls], [50, 50, 20])

    def test_batch_reuses_cached_series(self):
        cache.set(
            hardcover._series_cache_key(70),
            hardcover._series_page_data(_series_row(70, "Dune", [1, 5]), 70),
        )
        fake = FakeHardcover(
            books=[
                {
                    "id": 1,
                    "title": "Dune",
                    "cached_tags": [],
                    "pages": 1,
                    "release_date": None,
                    "cached_featured_series": {"id": 9, "position": 1, "series": {"id": 70, "name": "Dune"}},
                    "default_cover_edition": None,
                },
            ],
        )

        with patch("app.providers.hardcover.services.api_request", side_effect=fake):
            results = hardcover.book_metadata_batch(["1"])

        self.assertEqual(fake.calls, [("books", [1])])
        self.assertEqual([book["media_id"] for book in results["1"]["related"]["Dune"]], [1, 5])

    def test_batch_fetches_series_once_and_caches_them_for_series_page(self):
        fake = FakeHardcover(
            books=[
                {
                    "id": book_id,
                    "title": f"Book {book_id}",
                    "cached_tags": [],
                    "pages": 1,
                    "release_date": None,
                    "cached_featured_series": {"id": book_id, "position": book_id, "series": {"id": 70, "name": "Saga"}},
                    "default_cover_edition": None,
                }
                for book_id in (1, 2)
            ],
            series=[_series_row(70, "Saga", [1, 2])],
        )

        with patch("app.providers.hardcover.services.api_request", side_effect=fake):
            hardcover.book_metadata_batch(["1", "2"])
            page = hardcover.series_page(70)

        self.assertEqual(fake.calls, [("books", [1, 2]), ("series", [70])])
        self.assertEqual(page["name"], "Saga")

    def test_graphql_errors_raise_a_provider_error(self):
        with (
            patch(
                "app.providers.hardcover.services.api_request",
                return_value={"errors": [{"message": "field not found"}]},
            ),
            self.assertRaises(services.ProviderAPIError),
        ):
            hardcover.book_metadata_batch(["1"])


class FakeResponse:
    status_code = 200
    headers = {}
    text = ""

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.calls = 0

    def post(self, **kwargs):
        self.calls += 1
        return FakeResponse({"data": {"books": []}})


class HardcoverBatchQuotaTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        quota.clear()

    def test_batch_spends_the_quota_scope_budget_then_defers(self):
        session = FakeSession()

        with (
            patch.object(services, "session", session),
            quota.scope("metadata_fill", {"hardcover": 2}),
        ):
            hardcover.book_metadata_batch([str(i) for i in range(1, 101)])
            with self.assertRaises(quota.Deferred):
                hardcover.book_metadata_batch(["101"])

        self.assertEqual(session.calls, 2)

    def test_batch_does_not_call_a_blocked_provider(self):
        session = FakeSession()
        response = requests.Response()
        response.status_code = 429
        response.headers["Retry-After"] = "86400"
        quota.rate_limited("hardcover", hardcover.base_url, response, max_sleep=60)

        with (
            patch.object(services, "session", session),
            quota.scope("metadata_fill", {"hardcover": 500}),
            self.assertRaises(quota.Deferred),
        ):
            hardcover.book_metadata_batch(["1"])

        self.assertEqual(session.calls, 0)


def _tmdb_movie_response(**overrides):
    response = {
        "id": 129,
        "title": "Spirited Away",
        "poster_path": "/poster.jpg",
        "backdrop_path": None,
        "overview": "A girl.",
        "genres": [{"id": 16, "name": "Animation"}],
        "vote_average": 8.5,
        "vote_count": 100,
        "release_date": "2001-07-20",
        "status": "Released",
        "runtime": 125,
        "production_companies": [],
        "production_countries": [],
        "original_language": "ja",
        "spoken_languages": [
            {"iso_639_1": "ja", "english_name": "Japanese", "name": "日本語"},
        ],
        "credits": {"cast": [], "crew": []},
        "recommendations": {"results": []},
        "release_dates": {"results": []},
        "external_ids": {},
    }
    response.update(overrides)
    return response


def _tmdb_tv_response(**overrides):
    response = {
        "id": 1396,
        "name": "Dark",
        "number_of_episodes": 26,
        "number_of_seasons": 3,
        "poster_path": "/poster.jpg",
        "backdrop_path": None,
        "overview": "A town.",
        "genres": [{"id": 18, "name": "Drama"}],
        "vote_average": 8.4,
        "vote_count": 100,
        "first_air_date": "2017-12-01",
        "last_air_date": "2020-06-27",
        "status": "Ended",
        "episode_run_time": [60],
        "production_companies": [],
        "production_countries": [],
        "original_language": "de",
        "spoken_languages": [{"iso_639_1": "de", "english_name": "German", "name": "Deutsch"}],
        "seasons": [],
        "external_ids": {},
        "credits": {"cast": [], "crew": []},
        "recommendations": {"results": []},
        "content_ratings": {"results": []},
    }
    response.update(overrides)
    return response


class TmdbOriginalLanguageTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_spoken_languages_name_the_original_language(self):
        self.assertEqual(
            tmdb.get_original_language(
                "ja",
                [
                    {"iso_639_1": "en", "english_name": "English"},
                    {"iso_639_1": "ja", "english_name": "Japanese"},
                ],
            ),
            "Japanese",
        )

    def test_static_table_names_a_language_missing_from_spoken_languages(self):
        self.assertEqual(
            tmdb.get_original_language("ko", [{"iso_639_1": "en", "english_name": "English"}]),
            "Korean",
        )
        self.assertEqual(tmdb.get_original_language("cn", []), "Cantonese")
        self.assertEqual(tmdb.get_original_language("FR", None), "French")

    def test_spoken_language_without_an_english_name_falls_back_to_the_table(self):
        self.assertEqual(
            tmdb.get_original_language("es", [{"iso_639_1": "es", "english_name": ""}]),
            "Spanish",
        )

    def test_an_unknown_code_is_kept_as_the_code(self):
        self.assertEqual(tmdb.get_original_language("qq", []), "qq")

    def test_no_language_and_missing_codes_are_none(self):
        self.assertIsNone(tmdb.get_original_language("xx", [{"iso_639_1": "xx", "english_name": "No Language"}]))
        self.assertIsNone(tmdb.get_original_language("", []))
        self.assertIsNone(tmdb.get_original_language(None, []))

    @patch("app.providers.tmdb.services.api_request")
    def test_movie_metadata_has_the_original_language(self, api_request):
        api_request.return_value = _tmdb_movie_response()

        self.assertEqual(tmdb.movie("129")["original_language"], "Japanese")

    def test_tv_metadata_has_the_original_language(self):
        self.assertEqual(tmdb.process_tv(_tmdb_tv_response())["original_language"], "German")

    def test_seasons_inherit_no_original_language(self):
        tv_data = tmdb.process_tv(_tmdb_tv_response())
        season = tmdb.enrich_season_with_tv_data(
            {"synopsis": "No synopsis available."},
            tv_data,
            "1396",
            1,
        )

        self.assertNotIn("original_language", season)
