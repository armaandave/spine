import json
import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import unquote

import fakeredis
import requests
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from pyrate_limiter import Duration, Rate, RateItem, RedisBucket

from app.models import MediaTypes, MusicGenreAlbum, Sources
from app.providers import musicbrainz, services
from lists.featured.resolve import pick_title_match
from lists.featured.types import Entry

RUN_PROVIDER_TESTS = os.environ.get("RUN_PROVIDER_TESTS") == "1"
requires_provider_network = unittest.skipUnless(
    RUN_PROVIDER_TESTS,
    "Set RUN_PROVIDER_TESTS=1 to run live provider API tests.",
)

RELEASE_GROUP_MBID = "3bd76d40-7f0e-36b7-9348-91a33afee20e"
RELEASE_MBID = "2d0bad69-f735-484b-bc0b-2ea54c76225e"
RECORDING_MBID = "35518724-a25a-4627-a2cc-0786dd1d2272"
ARTIST_MBID = "b7ffd2af-418f-4be2-bdd1-22f8b48613da"
THRILLER_ALBUM_MBID = "f32fab67-77dd-3937-addc-9062e28e4c37"
OBSCURE_THRILLER_MBID = "d3d7a588-5113-4edb-9bcb-a6c9f2cd1a33"
STREAMING_RELATION_ID = "320adf26-96fa-4183-9045-1f5f32f833cb"
CORPUS_PATH = Path(__file__).parents[1] / "mock_data" / "musicbrainz_representative_releases.json"


def http_error(status_code):
    response = MagicMock(
        status_code=status_code,
        text=f"HTTP {status_code}",
        headers={},
    )
    return requests.exceptions.HTTPError(response=response)


def candidate(
    release_mbid,
    title="Album",
    *,
    country="US",
    release_date="2020-01-01",
    status="Official",
    formats=("Digital Media",),
    track_counts=(1,),
    streaming=True,
):
    return {
        "id": release_mbid,
        "title": title,
        "country": country,
        "date": release_date,
        "status": status,
        "media": [
            {"position": index, "format": media_format, "track-count": track_count}
            for index, (media_format, track_count) in enumerate(
                zip(formats, track_counts, strict=True),
                start=1,
            )
        ],
        "relations": (
            [
                {
                    "type-id": STREAMING_RELATION_ID,
                    "url": {"resource": "https://open.spotify.com/album/example"},
                },
            ]
            if streaming
            else []
        ),
    }


def full_release(release, *, complete=True, printed_number="1", isrcs=None):
    result = {**release, "artist-credit": [{"name": "Artist", "artist": {"id": "artist-1"}}]}
    result["media"] = []
    for medium in release["media"]:
        count = medium["track-count"]
        tracks = [
            {
                "id": f"track-{medium['position']}-{position}",
                "position": position,
                "number": printed_number if position == 1 else str(position),
                "title": f"Track {position}",
                "length": 180000,
                "recording": {
                    "id": f"recording-{medium['position']}-{position}",
                    "title": f"Track {position}",
                    "length": 180100,
                    "isrcs": isrcs or [],
                },
            }
            for position in range(1, count + 1)
        ]
        if not complete:
            tracks = tracks[:-1]
        result["media"].append({**medium, "tracks": tracks})
    return result


class MusicBrainzTests(TestCase):
    def setUp(self):
        cache.clear()

    @override_settings(VERSION="1.2.3", MUSICBRAINZ_CONTACT="contact@example.com")
    @patch("app.providers.musicbrainz.services.api_request")
    def test_search_release_groups(self, api_request):
        response = {
            "count": 1,
            "release-groups": [
                {
                    "id": RELEASE_GROUP_MBID,
                    "title": "Beyoncé (Deluxe): Hits?",
                    "first-release-date": "2024-07",
                },
            ],
        }
        api_request.return_value = response

        result = musicbrainz.search_release_groups(
            "Beyoncé (Deluxe): Hits?",
            limit=12,
            offset=24,
        )

        self.assertEqual(result, response)
        api_request.assert_called_once_with(
            Sources.MUSICBRAINZ.value,
            "GET",
            f"{musicbrainz.MUSICBRAINZ_URL}/release-group",
            params={
                "fmt": "json",
                "query": (
                    r"((releasegroup:beyoncé OR artist:beyoncé) AND "
                    r"(releasegroup:\(deluxe\)\: OR artist:\(deluxe\)\:) AND "
                    r"(releasegroup:hits\? OR artist:hits\?))^10 OR "
                    r"(releasegroup:(beyoncé \(deluxe\)\: hits\?) OR "
                    r"artist:(beyoncé \(deluxe\)\: hits\?))"
                ),
                "limit": 12,
                "offset": 24,
            },
            headers={
                "Accept": "application/json",
                "User-Agent": "Spine/1.2.3 (contact@example.com)",
            },
            request_session=services.musicbrainz_session,
        )

    def test_search_query_ranks_albums_with_every_word_first(self):
        # Any word alone ranked "Dark Age" by Dark Age above My Beautiful Dark
        # Twisted Fantasy, past the 100 results search re-ranks.
        self.assertEqual(
            musicbrainz._release_group_query("my beautiful da"),
            "((releasegroup:my OR artist:my) AND "
            "(releasegroup:beautiful OR artist:beautiful) AND "
            "(releasegroup:da OR artist:da OR releasegroup:da* OR artist:da*))^10 OR "
            "(releasegroup:(my beautiful da) OR artist:(my beautiful da))",
        )

    def test_search_query_words(self):
        query = musicbrainz._release_group_query("Simon & Garfunkel AND Bridge Över")
        every_word, any_word = query.split(")^10 OR ")

        self.assertNotIn(":&", every_word)  # nothing to match
        # A typed AND is a word, not an operator.
        self.assertIn("(releasegroup:and OR artist:and)", every_word)
        self.assertIn("releasegroup:(simon & garfunkel and bridge över)", any_word)
        self.assertIn("releasegroup:över OR artist:över OR releasegroup:over* OR artist:over*", every_word)
        self.assertNotIn("*", musicbrainz._release_group_query("good kid m"))
        self.assertNotIn("*", musicbrainz._release_group_query("Hits?"))

    @override_settings(VERSION="1.2.3", MUSICBRAINZ_CONTACT="contact@example.com")
    @patch("app.providers.musicbrainz.lookup_release_group_popularity")
    @patch("app.providers.musicbrainz.services.api_request")
    def test_discover_genre_ranks_one_candidate_pool_before_pagination(
        self,
        api_request,
        lookup_popularity,
    ):
        api_request.return_value = {
            "count": "3",
            "release-groups": [
                {
                    "id": RELEASE_GROUP_MBID,
                    "title": "Year Zero",
                    "score": 100,
                    "first-release-date": "2007-04-13",
                    "primary-type": "Album",
                    "artist-credit": [{"name": "Nine Inch Nails"}],
                },
                {
                    "id": THRILLER_ALBUM_MBID,
                    "title": "Popular Album",
                    "score": 70,
                    "first-release-date": "1982",
                    "primary-type": "Album",
                    "artist-credit": [{"name": "Popular Artist"}],
                },
                {
                    "id": OBSCURE_THRILLER_MBID,
                    "title": "Cult Album",
                    "score": 90,
                    "first-release-date": "1990",
                    "primary-type": "Album",
                    "artist-credit": [{"name": "Cult Artist"}],
                },
            ],
        }
        lookup_popularity.return_value = {
            RELEASE_GROUP_MBID: {
                "total_user_count": 10,
                "total_listen_count": 100_000,
            },
            THRILLER_ALBUM_MBID: {
                "total_user_count": 1_000,
                "total_listen_count": 20_000,
            },
            OBSCURE_THRILLER_MBID: {
                "total_user_count": 10,
                "total_listen_count": 200_000,
            },
        }

        first = musicbrainz.discover(
            genre='  Industrial  Rock: "Heavy"  ',
            page=1,
            page_size=2,
        )
        second = musicbrainz.discover(
            genre='Industrial Rock: "Heavy"',
            page=2,
            page_size=2,
        )

        self.assertEqual(first["page"], 1)
        self.assertEqual(second["page"], 2)
        self.assertEqual(first["per_page"], 2)
        self.assertEqual(first["total_results"], 3)
        results = first["results"] + second["results"]
        self.assertEqual(
            [result["media_id"] for result in results],
            [THRILLER_ALBUM_MBID, OBSCURE_THRILLER_MBID, RELEASE_GROUP_MBID],
        )
        self.assertEqual(results[-1]["subtitle"], "Nine Inch Nails · 2007 · Album")
        self.assertEqual(results[-1]["poster_aspect_ratio"], 1.0)
        self.assertNotIn("total_user_count", results[0])
        self.assertNotIn("provider_rank_boost", results[0])
        lookup_popularity.assert_called_with(
            [RELEASE_GROUP_MBID, THRILLER_ALBUM_MBID, OBSCURE_THRILLER_MBID],
        )
        api_request.assert_called_once_with(
            Sources.MUSICBRAINZ.value,
            "GET",
            f"{musicbrainz.MUSICBRAINZ_URL}/release-group",
            params={
                "fmt": "json",
                "query": r'tag:"industrial rock\: \"heavy\"" AND status:official',
                "limit": musicbrainz.SEARCH_CANDIDATE_LIMIT,
                "offset": 0,
            },
            headers={
                "Accept": "application/json",
                "User-Agent": "Spine/1.2.3 (contact@example.com)",
            },
            request_session=services.musicbrainz_session,
        )

    @patch("app.providers.musicbrainz.lookup_release_group_popularity")
    @patch("app.providers.musicbrainz.services.api_request")
    def test_discover_genre_handles_empty_results_and_invalid_count(
        self,
        api_request,
        _lookup_popularity,
    ):
        api_request.return_value = {"count": None, "release-groups": []}

        result = musicbrainz.discover(genre="ambient", page=1, page_size=25)

        self.assertEqual(result["total_results"], 0)
        self.assertEqual(result["results"], [])

    def test_discover_genre_requires_nonblank_value(self):
        with self.assertRaisesMessage(
            ValueError,
            "genre is required for MusicBrainz discovery.",
        ):
            musicbrainz.discover(genre="   ")

    @patch("app.providers.musicbrainz.services.api_request")
    def test_discover_genre_pages_through_stored_albums_most_read_first(
        self,
        api_request,
    ):
        MusicGenreAlbum.objects.bulk_create(
            [
                MusicGenreAlbum(
                    genre="hip hop",
                    release_group_mbid=f"album-{index}",
                    title=f"Album {index}",
                    artist="Artist",
                    release_date="2012-10-22",
                    page_views=index // 2 + 1,
                    listeners=index % 2,
                    listens=index,
                )
                for index in range(musicbrainz.SEARCH_CANDIDATE_LIMIT)
            ]
            + [
                MusicGenreAlbum(
                    genre="hip hop",
                    release_group_mbid="unread-album",
                    title="Unread Album",
                    listeners=1_000_000,
                    listens=1_000_000,
                ),
                MusicGenreAlbum(
                    genre="rock",
                    release_group_mbid="rock-album",
                    title="Rock Album",
                    page_views=10_000,
                    listeners=10_000,
                    listens=10_000,
                ),
            ],
        )

        first = musicbrainz.discover(genre="  Hip  Hop ", page=1, page_size=3)
        second = musicbrainz.discover(genre="hip hop", page=2, page_size=3)
        last = musicbrainz.discover(genre="hip hop", page=34, page_size=3)

        self.assertEqual(first["total_results"], musicbrainz.SEARCH_CANDIDATE_LIMIT + 1)
        self.assertEqual(first["per_page"], 3)
        self.assertEqual(
            [result["media_id"] for result in first["results"] + second["results"]],
            ["album-99", "album-98", "album-97", "album-96", "album-95", "album-94"],
        )
        self.assertEqual(
            [result["media_id"] for result in last["results"]],
            ["album-0", "unread-album"],
        )
        self.assertEqual(
            first["results"][0],
            {
                "media_id": "album-99",
                "source": Sources.MUSICBRAINZ.value,
                "media_type": MediaTypes.MUSIC.value,
                "title": "Album 99",
                "release_date": "2012-10-22",
                "image": (
                    f"{musicbrainz.COVER_ART_URL}/release-group/album-99/front-500"
                ),
                "poster_width": 500,
                "poster_height": 500,
                "poster_aspect_ratio": 1.0,
                "subtitle": "Artist · 2012 · Album",
            },
        )
        api_request.assert_not_called()

    @patch("app.providers.musicbrainz.lookup_release_group_popularity")
    @patch("app.providers.musicbrainz.services.api_request")
    def test_discover_genre_lists_few_stored_albums_before_searched_ones(
        self,
        api_request,
        lookup_popularity,
    ):
        MusicGenreAlbum.objects.create(
            genre="jazz rap",
            release_group_mbid=THRILLER_ALBUM_MBID,
            title="Popular Album",
            artist="Popular Artist",
            release_date="1982",
            listeners=50_000,
            listens=900_000,
        )
        api_request.return_value = {
            "count": 98_000,
            "release-groups": [
                {"id": RELEASE_GROUP_MBID, "title": "Searched Album"},
                {"id": THRILLER_ALBUM_MBID, "title": "Popular Album"},
            ],
        }
        lookup_popularity.return_value = {}

        result = musicbrainz.discover(genre="jazz rap", page=1, page_size=24)

        self.assertEqual(result["total_results"], 2)
        self.assertEqual(
            [item["media_id"] for item in result["results"]],
            [THRILLER_ALBUM_MBID, RELEASE_GROUP_MBID],
        )
        self.assertEqual(
            result["results"][0]["subtitle"],
            "Popular Artist · 1982 · Album",
        )

    @override_settings(LISTENBRAINZ_TOKEN="lb-token")
    @patch("app.providers.musicbrainz.GENRE_ALBUMS_PER_ARTIST", 3)
    @patch("app.providers.musicbrainz.services.api_request")
    def test_refresh_genre_albums_files_top_artists_albums_under_their_genres(
        self,
        api_request,
    ):
        MusicGenreAlbum.objects.create(
            genre="stale genre",
            release_group_mbid="old-album",
            title="Old Album",
            listeners=1,
            listens=1,
        )

        def album(mbid, name, listeners, genres, *, release_type="Album"):
            return {
                "release_group_mbid": mbid,
                "release_group": {"name": name, "type": release_type, "date": "2012-10-22"},
                "artist": {"name": "Kendrick Lamar"},
                "total_user_count": listeners,
                "total_listen_count": listeners * 10,
                "tag": {
                    "artist": [{"tag": "jazz", "count": 50, "genre_mbid": "g-jazz"}],
                    "release_group": [
                        {"tag": tag, "count": count, **({"genre_mbid": f"g-{tag}"} if genre else {})}
                        for tag, count, genre in genres
                    ],
                },
            }

        artist_albums = {
            "kendrick": [
                album(
                    "gkmc",
                    "good kid, m.A.A.d city",
                    85_000,
                    [
                        ("hip hop", 9, True),
                        ("West Coast  Hip Hop", 5, True),
                        ("pop rap", 4, True),
                        ("gangsta rap", 2, True),
                        ("car on cover", 9, False),
                    ],
                ),
                album("single", "Not Like Us", 99_000, [("hip hop", 9, True)], release_type="Single"),
                album("bootleg", "Bootleg", 10, [("hip hop", 1, True)]),
                album("tpab", "To Pimp a Butterfly", 14_000, [("jazz rap", 10, True)]),
                album(
                    "gkmc-deluxe",
                    "good kid, m.A.A.d city (Deluxe)",
                    90_000,
                    [("hip hop", 9, True)],
                ),
            ],
            "sza": [album("tpab", "To Pimp a Butterfly", 1, [("r&b", 10, True)])],
            "beatles": [album("abbey-road", "Abbey Road", 60_000, [("rock", 3, True)])],
        }

        def respond(
            provider,
            method,
            url,
            params=None,
            data=None,
            headers=None,
            request_session=None,
        ):
            if url == musicbrainz.WIKIDATA_SPARQL_URL:
                self.assertEqual(data["format"], "json")
                self.assertIn('"gkmc-deluxe" "gkmc" "tpab" "abbey-road"', data["query"])
                rows = [
                    ("gkmc", "28", "good kid, m.A.A.d city"),
                    ("gkmc", "3", "good kid, m.A.A.d city (deluxe)"),
                    ("tpab", "29", "To Pimp a Butterfly"),
                    ("gkmc-deluxe", "28", "good kid, m.A.A.d city"),
                ]
                return {
                    "results": {
                        "bindings": [
                            {
                                "rg": {"value": rg},
                                "links": {"value": links},
                                "title": {"value": title},
                            }
                            for rg, links, title in rows
                        ],
                    },
                }
            if url.startswith(f"{musicbrainz.WIKIPEDIA_PAGEVIEWS_URL}/"):
                article = url.removeprefix(f"{musicbrainz.WIKIPEDIA_PAGEVIEWS_URL}/")
                article = unquote(article.split("/monthly/")[0])
                if article == "To_Pimp_a_Butterfly":
                    raise http_error(404)
                self.assertEqual(article, "good_kid,_m.A.A.d_city")
                return {"items": [{"views": 100}, {"views": 50}]}
            if url == musicbrainz.LISTENBRAINZ_TOP_ARTISTS_URL:
                self.assertEqual(headers["Authorization"], "Token lb-token")
                artists = {
                    "all_time": [
                        {"artist_mbid": "kendrick", "artist_name": "Kendrick Lamar"},
                        {"artist_mbid": None, "artist_name": "The Beatles"},
                    ],
                    "this_year": [
                        {"artist_mbid": "kendrick", "artist_name": "Kendrick Lamar"},
                        {"artist_mbid": "sza", "artist_name": "SZA"},
                        {
                            "artist_mbid": musicbrainz.VARIOUS_ARTISTS_MBID,
                            "artist_name": "Various Artists",
                        },
                    ],
                }[params["range"]]
                return {"payload": {"artists": artists}}
            if url == f"{musicbrainz.MUSICBRAINZ_URL}/artist":
                self.assertEqual(params["query"], 'artist:"The Beatles"')
                return {
                    "artists": [
                        {"id": "beatles-tribute", "name": "The Beatles Tribute"},
                        {"id": "beatles", "name": "The Beatles"},
                    ],
                }
            prefix = f"{musicbrainz.LISTENBRAINZ_ARTIST_ALBUMS_URL}/"
            self.assertTrue(url.startswith(prefix))
            return artist_albums[url.removeprefix(prefix)]

        api_request.side_effect = respond

        result = musicbrainz.refresh_genre_albums()

        self.assertEqual(
            result,
            {
                "artists": 3,
                "failed_artists": 0,
                "albums": 4,
                "albums_on_wikipedia": 1,
                "genre_albums": 5,
            },
        )
        self.assertEqual(
            set(
                MusicGenreAlbum.objects.values_list(
                    "genre",
                    "release_group_mbid",
                    "page_views",
                ),
            ),
            {
                ("hip hop", "gkmc", 150),
                ("west coast hip hop", "gkmc", 150),
                ("jazz rap", "tpab", 0),
                ("hip hop", "gkmc-deluxe", 0),
                ("rock", "abbey-road", 0),
            },
        )
        gkmc = MusicGenreAlbum.objects.get(genre="hip hop", release_group_mbid="gkmc")
        self.assertEqual(
            (gkmc.title, gkmc.artist, gkmc.release_date, gkmc.listeners, gkmc.listens),
            ("good kid, m.A.A.d city", "Kendrick Lamar", "2012-10-22", 85_000, 850_000),
        )
        self.assertEqual(gkmc.search_text, " good kid m a a d city kendrick lamar ")

    @override_settings(LISTENBRAINZ_TOKEN="lb-token")
    @patch("app.providers.musicbrainz.services.api_request")
    def test_refresh_genre_albums_keeps_old_albums_when_wikipedia_fails(
        self,
        api_request,
    ):
        MusicGenreAlbum.objects.create(
            genre="hip hop",
            release_group_mbid="old-album",
            title="Old Album",
            listeners=1,
            listens=1,
        )

        def respond(
            provider,
            method,
            url,
            params=None,
            data=None,
            headers=None,
            request_session=None,
        ):
            if url == musicbrainz.LISTENBRAINZ_TOP_ARTISTS_URL:
                return {"payload": {"artists": [{"artist_mbid": "kendrick"}]}}
            if url == musicbrainz.WIKIDATA_SPARQL_URL:
                return {
                    "results": {
                        "bindings": [
                            {
                                "rg": {"value": "gkmc"},
                                "links": {"value": "28"},
                                "title": {"value": "good kid, m.A.A.d city"},
                            },
                        ],
                    },
                }
            if url.startswith(musicbrainz.WIKIPEDIA_PAGEVIEWS_URL):
                raise http_error(503)
            return [
                {
                    "release_group_mbid": "gkmc",
                    "release_group": {"name": "good kid, m.A.A.d city", "type": "Album"},
                    "tag": {"release_group": [{"tag": "hip hop", "count": 9, "genre_mbid": "g"}]},
                },
            ]

        api_request.side_effect = respond

        with self.assertRaisesMessage(RuntimeError, "Wikipedia reads failed for 1 of 1 albums."):
            musicbrainz.refresh_genre_albums()

        self.assertEqual(
            list(MusicGenreAlbum.objects.values_list("release_group_mbid", flat=True)),
            ["old-album"],
        )

    @override_settings(LISTENBRAINZ_TOKEN="lb-token")
    @patch("app.providers.musicbrainz.services.api_request")
    def test_refresh_genre_albums_keeps_old_albums_when_listenbrainz_fails(
        self,
        api_request,
    ):
        MusicGenreAlbum.objects.create(
            genre="hip hop",
            release_group_mbid="gkmc",
            title="good kid, m.A.A.d city",
            listeners=85_000,
            listens=850_000,
        )

        def respond(provider, method, url, params=None, headers=None, request_session=None):
            if url == musicbrainz.LISTENBRAINZ_TOP_ARTISTS_URL:
                return {"payload": {"artists": [{"artist_mbid": "kendrick"}]}}
            raise requests.ConnectionError("ListenBrainz is down")

        api_request.side_effect = respond

        with self.assertRaisesMessage(RuntimeError, "ListenBrainz failed for 1 of 1 artists."):
            musicbrainz.refresh_genre_albums()

        self.assertTrue(MusicGenreAlbum.objects.filter(release_group_mbid="gkmc").exists())

    @override_settings(LISTENBRAINZ_TOKEN="lb-token")
    @patch("app.providers.musicbrainz.services.api_request")
    def test_refresh_genre_albums_keeps_old_albums_when_listenbrainz_is_empty(
        self,
        api_request,
    ):
        MusicGenreAlbum.objects.create(
            genre="hip hop",
            release_group_mbid="gkmc",
            title="good kid, m.A.A.d city",
            listeners=85_000,
            listens=850_000,
        )
        api_request.return_value = {"payload": {"artists": []}}

        with self.assertRaisesMessage(RuntimeError, "ListenBrainz returned no albums."):
            musicbrainz.refresh_genre_albums()

        self.assertTrue(MusicGenreAlbum.objects.filter(release_group_mbid="gkmc").exists())

    @override_settings(LISTENBRAINZ_TOKEN="")
    @patch("app.providers.musicbrainz.services.api_request")
    def test_refresh_genre_albums_needs_a_listenbrainz_token(self, api_request):
        self.assertIsNone(musicbrainz.refresh_genre_albums())
        api_request.assert_not_called()

    @override_settings(PER_PAGE=2)
    @patch("app.providers.musicbrainz.search_release_groups")
    def test_search_normalizes_release_groups_and_pagination(self, search_release_groups):
        search_release_groups.return_value = {
            "count": 1,
            "release-groups": [
                {
                    "id": RELEASE_GROUP_MBID,
                    "title": "Year Zero",
                    "score": 98,
                    "first-release-date": "2007-04-13",
                    "primary-type": "Album",
                    "secondary-types": ["Concept Album"],
                    "disambiguation": "Nine Inch Nails album",
                    "artist-credit": [
                        {
                            "name": "Nine Inch Nails",
                            "joinphrase": " feat. ",
                            "artist": {"id": "artist-1"},
                        },
                        {
                            "name": "Guest",
                            "artist": {"id": "artist-2"},
                        },
                    ],
                },
            ],
        }

        result = musicbrainz.search("Nine Inch Nails", 1)

        search_release_groups.assert_called_once_with(
            "Nine Inch Nails",
            limit=musicbrainz.SEARCH_CANDIDATE_LIMIT,
            offset=0,
        )
        album = result["results"][0]
        self.assertEqual(album["media_id"], RELEASE_GROUP_MBID)
        self.assertEqual(album["source"], Sources.MUSICBRAINZ.value)
        self.assertEqual(album["media_type"], MediaTypes.MUSIC.value)
        self.assertEqual(album["subtitle"], "Nine Inch Nails feat. Guest · 2007 · Album")
        self.assertEqual(album["search_score"], 98)
        self.assertEqual(album["poster_aspect_ratio"], 1.0)
        self.assertEqual(album["secondary_types"], ["Concept Album"])
        self.assertNotIn("provider_rank_boost", album)

    @patch("app.providers.musicbrainz.lookup_release_group_popularity")
    @patch("app.providers.musicbrainz.search_release_groups")
    def test_all_search_skips_optional_popularity_and_does_not_export_provider_boost(
        self,
        search_release_groups,
        lookup_popularity,
    ):
        search_release_groups.return_value = {
            "count": 1,
            "release-groups": [
                {
                    "id": RELEASE_GROUP_MBID,
                    "title": "Dune",
                    "score": 100,
                    "first-release-date": "1995",
                    "primary-type": "Album",
                    "artist-credit": [{"name": "Dune"}],
                },
            ],
        }

        result = musicbrainz.search(
            "dune",
            1,
            preserve_ranking_fields=True,
            timeout=8,
        )

        lookup_popularity.assert_not_called()
        search_release_groups.assert_called_once_with(
            "dune",
            limit=musicbrainz.SEARCH_CANDIDATE_LIMIT,
            offset=0,
            timeout=8,
        )
        self.assertNotIn("provider_rank_boost", result["results"][0])

    @override_settings(PER_PAGE=2)
    @patch("app.providers.musicbrainz.lookup_release_group_popularity")
    @patch("app.providers.musicbrainz.search_release_groups")
    def test_search_ranks_overfetched_candidates_by_listenbrainz_popularity(
        self,
        search_release_groups,
        lookup_popularity,
    ):
        search_release_groups.return_value = {
            "count": 3,
            "release-groups": [
                {
                    "id": OBSCURE_THRILLER_MBID,
                    "title": "Thriller",
                    "score": 100,
                    "first-release-date": "2007",
                    "primary-type": "EP",
                    "artist-credit": [{"name": "Thriller"}],
                },
                {
                    "id": "78b31ce7-95c4-403a-9c74-4f6606c07bc8",
                    "title": "Techno Thriller",
                    "score": 83,
                    "first-release-date": "2018",
                    "primary-type": "Album",
                    "artist-credit": [{"name": "Techno Thriller"}],
                },
                {
                    "id": THRILLER_ALBUM_MBID,
                    "title": "Thriller",
                    "score": 46,
                    "first-release-date": "1982-11-30",
                    "primary-type": "Album",
                    "artist-credit": [{"name": "Michael Jackson"}],
                },
            ],
        }
        lookup_popularity.return_value = {
            OBSCURE_THRILLER_MBID: {
                "total_listen_count": 30,
                "total_user_count": 2,
            },
            THRILLER_ALBUM_MBID: {
                "total_listen_count": 529156,
                "total_user_count": 25295,
            },
        }

        first_page = musicbrainz.search("thriller", 1)
        second_page = musicbrainz.search("thriller", 2)

        self.assertEqual(first_page["results"][0]["media_id"], THRILLER_ALBUM_MBID)
        self.assertEqual(
            [item["media_id"] for item in first_page["results"] + second_page["results"]],
            [
                THRILLER_ALBUM_MBID,
                OBSCURE_THRILLER_MBID,
                "78b31ce7-95c4-403a-9c74-4f6606c07bc8",
            ],
        )
        self.assertEqual(first_page["total_results"], 3)
        self.assertNotIn("total_user_count", first_page["results"][0])
        self.assertNotIn("total_listen_count", first_page["results"][0])
        search_release_groups.assert_called_with(
            "thriller",
            limit=musicbrainz.SEARCH_CANDIDATE_LIMIT,
            offset=0,
        )

    @patch("app.providers.musicbrainz.lookup_release_group_popularity")
    @patch("app.providers.musicbrainz.search_release_groups")
    def test_search_adds_popular_albums_musicbrainz_did_not_return(
        self,
        search_release_groups,
        lookup_popularity,
    ):
        # Over 1,000 albums are called "Renaissance", and MusicBrainz's 100 miss Beyoncé's.
        MusicGenreAlbum.objects.bulk_create(
            [
                MusicGenreAlbum(
                    genre=genre,
                    release_group_mbid="beyonce-renaissance",
                    title="RENAISSANCE",
                    artist="Beyoncé",
                    release_date="2022-07-29",
                    listeners=11_320,
                    listens=453_186,
                    search_text=" renaissance beyonce ",
                )
                for genre in ("dance-pop", "house")
            ]
            + [
                MusicGenreAlbum(
                    genre="dance-pop",
                    release_group_mbid="beyonce-lemonade",
                    title="Lemonade",
                    artist="Beyoncé",
                    listeners=9_000,
                    listens=200_000,
                    search_text=" lemonade beyonce ",
                ),
            ],
        )
        search_release_groups.return_value = {
            "count": 1242,
            "release-groups": [
                {
                    "id": "vanilla-fudge-renaissance",
                    "title": "Renaissance",
                    "score": 100,
                    "first-release-date": "1968",
                    "primary-type": "Album",
                    "artist-credit": [{"name": "Vanilla Fudge"}],
                },
            ],
        }
        lookup_popularity.return_value = {
            "vanilla-fudge-renaissance": {
                "total_user_count": 1_568,
                "total_listen_count": 8_021,
            },
        }

        response = musicbrainz.search("renaissance", 1)
        results = response["results"]

        self.assertEqual(
            [item["media_id"] for item in results],
            ["beyonce-renaissance", "vanilla-fudge-renaissance"],
        )
        self.assertEqual(response["total_results"], 2)
        self.assertEqual(results[0]["subtitle"], "Beyoncé · 2022 · Album")
        for key in ("total_user_count", "artist_name", "provider_rank_boost"):
            self.assertNotIn(key, results[0])
        lookup_popularity.assert_called_once_with(
            ["vanilla-fudge-renaissance", "beyonce-renaissance"],
        )
        # Featured lists still check the artist: another artist's "Renaissance" isn't it.
        self.assertIsNone(
            pick_title_match(Entry(1, "Renaissance", creator="Marcus Miller"), results[:1]),
        )

    @patch("app.providers.musicbrainz.lookup_release_group_popularity")
    @patch("app.providers.musicbrainz.search_release_groups")
    def test_all_search_takes_listening_counts_from_popular_albums(
        self,
        search_release_groups,
        lookup_popularity,
    ):
        MusicGenreAlbum.objects.create(
            genre="dance-pop",
            release_group_mbid="beyonce-renaissance",
            title="RENAISSANCE",
            artist="Beyoncé",
            listeners=11_320,
            listens=453_186,
            search_text=" renaissance beyonce ",
        )
        search_release_groups.return_value = {
            "count": 2,
            "release-groups": [
                {
                    "id": "vanilla-fudge-renaissance",
                    "title": "Renaissance",
                    "score": 100,
                    "artist-credit": [{"name": "Vanilla Fudge"}],
                },
                {
                    "id": "beyonce-renaissance",
                    "title": "RENAISSANCE",
                    "score": 100,
                    "artist-credit": [{"name": "Beyoncé"}],
                },
            ],
        }

        results = musicbrainz.search("renaissance", 1, preserve_ranking_fields=True)["results"]

        lookup_popularity.assert_not_called()
        self.assertEqual(
            [item["media_id"] for item in results],
            ["beyonce-renaissance", "vanilla-fudge-renaissance"],
        )
        self.assertEqual(results[0]["total_user_count"], 11_320)

    def test_popular_album_matches_need_every_word_in_title_or_artist(self):
        MusicGenreAlbum.objects.bulk_create(
            [
                MusicGenreAlbum(
                    genre=genre,
                    release_group_mbid="renaissance",
                    title="RENAISSANCE",
                    artist="Beyoncé",
                    listeners=11_320,
                    listens=453_186,
                    search_text=" renaissance beyonce ",
                )
                for genre in ("dance-pop", "house")
            ]
            + [
                MusicGenreAlbum(
                    genre="dance-pop",
                    release_group_mbid="lemonade",
                    title="Lemonade",
                    artist="Beyoncé",
                    listeners=9_000,
                    listens=200_000,
                    search_text=" lemonade beyonce ",
                ),
                MusicGenreAlbum(
                    genre="metal",
                    release_group_mbid="darkness",
                    title="Darkness Ages",
                    artist="Band",
                    listeners=5,
                    listens=5,
                    search_text=" darkness ages band ",
                ),
            ],
        )

        def matches(query):
            return [
                album.release_group_mbid
                for album in musicbrainz._popular_album_matches(query)
            ]

        self.assertEqual(matches("BEYONCÉ"), ["renaissance", "lemonade"])
        self.assertEqual(matches("Renaissance beyonce"), ["renaissance"])
        self.assertEqual(matches("beyonce renaiss"), ["renaissance"])
        self.assertEqual(matches("renaiss beyonce"), [])
        self.assertEqual(matches("dark ages"), [])
        self.assertEqual(matches("ages d"), [])
        self.assertEqual(matches("!!"), [])

    @override_settings(
        LISTENBRAINZ_TOKEN="listenbrainz-token",
        VERSION="1.2.3",
        MUSICBRAINZ_CONTACT="contact@example.com",
    )
    @patch("app.providers.musicbrainz.services.api_request")
    def test_release_group_popularity_is_batched_cached_and_validated(self, api_request):
        api_request.return_value = [
            {
                "release_group_mbid": THRILLER_ALBUM_MBID,
                "total_listen_count": 529156,
                "total_user_count": 25295,
            },
            {
                "release_group_mbid": OBSCURE_THRILLER_MBID,
                "total_listen_count": None,
                "total_user_count": -1,
            },
            {
                "release_group_mbid": "not-requested",
                "total_listen_count": 999999,
                "total_user_count": 999999,
            },
        ]

        first = musicbrainz.lookup_release_group_popularity(
            [THRILLER_ALBUM_MBID, OBSCURE_THRILLER_MBID, THRILLER_ALBUM_MBID],
        )
        second = musicbrainz.lookup_release_group_popularity(
            [THRILLER_ALBUM_MBID, OBSCURE_THRILLER_MBID, THRILLER_ALBUM_MBID],
        )

        self.assertEqual(first, second)
        self.assertEqual(
            first,
            {
                THRILLER_ALBUM_MBID: {
                    "total_listen_count": 529156,
                    "total_user_count": 25295,
                },
                OBSCURE_THRILLER_MBID: {
                    "total_listen_count": None,
                    "total_user_count": None,
                },
            },
        )
        api_request.assert_called_once_with(
            "listenbrainz",
            "POST",
            musicbrainz.LISTENBRAINZ_URL,
            params={
                "release_group_mbids": [
                    THRILLER_ALBUM_MBID,
                    OBSCURE_THRILLER_MBID,
                ],
            },
            headers={
                "Accept": "application/json",
                "User-Agent": "Spine/1.2.3 (contact@example.com)",
                "Authorization": "Token listenbrainz-token",
                "Content-Type": "application/json",
            },
            request_session=services.session,
            timeout=musicbrainz.LISTENBRAINZ_TIMEOUT,
        )

    @override_settings(LISTENBRAINZ_TOKEN="listenbrainz-token")
    @patch("app.providers.musicbrainz.services.api_request")
    def test_release_group_popularity_failure_falls_back_and_is_short_cached(
        self,
        api_request,
    ):
        api_request.side_effect = requests.exceptions.Timeout("timed out")

        with self.assertLogs(musicbrainz.logger, level="WARNING"):
            first = musicbrainz.lookup_release_group_popularity(
                [THRILLER_ALBUM_MBID],
            )
        second = musicbrainz.lookup_release_group_popularity(
            [THRILLER_ALBUM_MBID],
        )

        self.assertEqual(first, {})
        self.assertEqual(second, {})
        api_request.assert_called_once()

    @override_settings(LISTENBRAINZ_TOKEN="")
    @patch("app.providers.musicbrainz.services.api_request")
    def test_release_group_popularity_without_token_skips_provider(self, api_request):
        self.assertEqual(
            musicbrainz.lookup_release_group_popularity([THRILLER_ALBUM_MBID]),
            {},
        )
        api_request.assert_not_called()

    @patch(
        "app.providers.musicbrainz.resolve_representative_release",
        return_value={"release": None, "reason": None, "release_mbid": None},
    )
    @patch("app.providers.musicbrainz.lookup_cover_art")
    @patch("app.providers.musicbrainz.lookup_release_group")
    def test_music_normalizes_release_group_detail(
        self,
        lookup_release_group,
        lookup_cover_art,
        _resolve,
    ):
        lookup_release_group.return_value = {
            "id": RELEASE_GROUP_MBID,
            "title": "Year Zero",
            "first-release-date": "2007-04-13",
            "primary-type": "Album",
            "secondary-types": ["Concept Album"],
            "disambiguation": "Nine Inch Nails album",
            "release-count": 13,
            "annotation": "  A concept album.  ",
            "artist-credit": [
                {
                    "name": "Nine Inch Nails",
                    "joinphrase": "",
                    "artist": {"id": "artist-1"},
                },
            ],
            "genres": [{"name": "industrial rock"}, {"name": "electronic"}],
            "rating": {"value": 4.25, "votes-count": 20},
            "relations": [
                {
                    "target-type": "url",
                    "type": "other databases",
                    "url": {"resource": "http://www.discogs.com/master/123"},
                },
                {
                    "target-type": "url",
                    "type": "other databases",
                    "url": {"resource": "https://discogs.com/master/456"},
                },
                {
                    "target-type": "url",
                    "type": "streaming music",
                    "url": {"resource": "https://example.com/listen"},
                },
            ],
        }
        result = musicbrainz.music(RELEASE_GROUP_MBID)

        lookup_cover_art.assert_not_called()
        self.assertEqual(
            result["image"],
            f"https://coverartarchive.org/release-group/{RELEASE_GROUP_MBID}/front-500",
        )
        self.assertFalse(result["music"]["cover_art"]["fallback_used"])
        self.assertEqual(result["title"], "Year Zero")
        self.assertEqual(result["release_date"], "2007-04-13")
        self.assertEqual(result["max_progress"], 1)
        self.assertEqual(result["genres"], ["industrial rock", "electronic"])
        self.assertEqual(result["score"], 4.25)
        self.assertEqual(result["score_count"], 20)
        self.assertEqual(result["details"]["artist"], "Nine Inch Nails")
        self.assertEqual(result["details"]["release_count"], 13)
        self.assertEqual(result["details"]["annotation"], "A concept album.")
        self.assertEqual(result["poster_width"], 500)
        self.assertEqual(
            result["external_links"],
            {
                "MusicBrainz": f"https://musicbrainz.org/release-group/{RELEASE_GROUP_MBID}",
                "discogs.com": "https://www.discogs.com/master/123",
            },
        )
        self.assertNotIn("synopsis", result)
        self.assertNotIn("overview", result)

    @patch(
        "app.providers.musicbrainz.resolve_representative_release",
        return_value={"release": None, "reason": None, "release_mbid": None},
    )
    @patch("app.providers.musicbrainz.lookup_cover_art", return_value=None)
    @patch("app.providers.musicbrainz.lookup_release_group")
    def test_music_missing_optional_fields_stay_empty(
        self,
        lookup_release_group,
        _lookup_cover_art,
        _resolve,
    ):
        lookup_release_group.return_value = {"id": RELEASE_GROUP_MBID, "title": "Elbentanz", "annotation": "  "}

        result = musicbrainz.music(RELEASE_GROUP_MBID)

        self.assertIsNone(result["release_date"])
        self.assertEqual(result["genres"], [])
        self.assertIsNone(result["score"])
        self.assertIsNone(result["score_count"])
        self.assertEqual(result["details"]["artist_credits"], [])
        self.assertEqual(result["details"]["secondary_types"], [])
        self.assertIsNone(result["details"]["primary_type"])
        self.assertIsNone(result["details"]["disambiguation"])
        self.assertIsNone(result["details"]["release_count"])
        self.assertIsNone(result["details"]["annotation"])
        self.assertIsNone(result["music"]["representative_release"])

    @patch("app.providers.musicbrainz.resolve_representative_release")
    @patch("app.providers.musicbrainz.lookup_cover_art")
    @patch("app.providers.musicbrainz.lookup_release_group")
    def test_basic_music_detail_skips_deferred_enrichment(
        self,
        lookup_release_group,
        lookup_cover_art,
        resolve_release,
    ):
        lookup_release_group.return_value = {
            "id": RELEASE_GROUP_MBID,
            "title": "Year Zero",
        }

        result = musicbrainz.music(RELEASE_GROUP_MBID, include_enrichment=False)

        self.assertEqual(result["title"], "Year Zero")
        self.assertEqual(
            result["image"],
            f"https://coverartarchive.org/release-group/{RELEASE_GROUP_MBID}/front-500",
        )
        self.assertIsNone(result["music"]["representative_release"])
        lookup_cover_art.assert_not_called()
        resolve_release.assert_not_called()

    @patch("app.providers.musicbrainz.resolve_representative_release")
    @patch("app.providers.musicbrainz.lookup_cover_art")
    def test_music_enrichment_only_resolves_the_tracklist(
        self,
        lookup_cover_art,
        resolve_release,
    ):
        resolve_release.return_value = {"release": None, "reason": None, "release_mbid": None}

        self.assertIsNone(musicbrainz._music_enrichment(RELEASE_GROUP_MBID, "Year Zero"))

        lookup_cover_art.assert_not_called()
        resolve_release.assert_called_once_with(RELEASE_GROUP_MBID, "Year Zero")

    def test_lucene_escaping_preserves_unicode_and_escapes_reserved_tokens(self):
        self.assertEqual(musicbrainz.escape_lucene("Beyoncé 千と千尋"), "Beyoncé 千と千尋")
        for token in (
            "+",
            "-",
            "&&",
            "||",
            "!",
            "(",
            ")",
            "{",
            "}",
            "[",
            "]",
            "^",
            '"',
            "~",
            "*",
            "?",
            ":",
            "\\",
            "/",
        ):
            with self.subTest(token=token):
                self.assertEqual(musicbrainz.escape_lucene(token), f"\\{token}")

    @patch("app.providers.musicbrainz.services.api_request")
    def test_lookup_release_group_preserves_partial_date(self, api_request):
        response = {
            "id": RELEASE_GROUP_MBID,
            "title": "Elbentanz",
            "first-release-date": "2003",
        }
        api_request.return_value = response

        result = musicbrainz.lookup_release_group(RELEASE_GROUP_MBID)

        self.assertEqual(result["first-release-date"], "2003")
        self.assertEqual(
            api_request.call_args.kwargs["params"],
            {
                "fmt": "json",
                "inc": (
                    "artist-credits+releases+genres+ratings+annotation+url-rels"
                ),
            },
        )

    @override_settings(VERSION="1.2.3", MUSICBRAINZ_CONTACT="contact@example.com")
    @patch("app.providers.musicbrainz.services.api_request")
    def test_lookup_artist_requests_profile_fields_and_caches(self, api_request):
        api_request.return_value = {"id": ARTIST_MBID, "name": "Nine Inch Nails"}

        result = musicbrainz.lookup_artist(ARTIST_MBID)
        cached = musicbrainz.lookup_artist(ARTIST_MBID)

        self.assertEqual(result, cached)
        api_request.assert_called_once_with(
            Sources.MUSICBRAINZ.value,
            "GET",
            f"{musicbrainz.MUSICBRAINZ_URL}/artist/{ARTIST_MBID}",
            params={"fmt": "json", "inc": "annotation+url-rels"},
            headers={
                "Accept": "application/json",
                "User-Agent": "Spine/1.2.3 (contact@example.com)",
            },
            request_session=services.musicbrainz_session,
        )

    @patch("app.providers.musicbrainz.browse_artist_release_groups")
    @patch("app.providers.musicbrainz.lookup_artist")
    def test_person_page_normalizes_artist_and_paginated_release_groups(
        self,
        lookup_artist,
        browse_release_groups,
    ):
        lookup_artist.return_value = {
            "id": ARTIST_MBID,
            "name": "Nine Inch Nails",
            "annotation": {"text": "  Industrial rock band.  "},
            "life-span": {"begin": "1988", "end": None},
            "begin-area": {"name": "Cleveland"},
            "area": {"name": "United States"},
            "relations": [
                {
                    "target-type": "url",
                    "type": "image",
                    "url": {"resource": "http://example.com/artist.jpg"},
                },
            ],
        }
        duplicate = {
            "id": "release-group-2",
            "title": "The Fragile",
            "first-release-date": "1999-09-21",
            "genres": [{"name": "industrial rock"}],
            "rating": {"value": 4.4, "votes-count": 20},
        }
        browse_release_groups.side_effect = [
            {
                "release-group-count": 4,
                "release-groups": [
                    {
                        "id": "release-group-1",
                        "title": "With Teeth",
                        "first-release-date": "2005",
                    },
                    duplicate,
                ],
            },
            {
                "release-group-count": 4,
                "release-groups": [
                    duplicate,
                    {"id": "release-group-3", "title": "Undated"},
                ],
            },
        ]

        result = musicbrainz.person_page(ARTIST_MBID)
        cached = musicbrainz.person_page(ARTIST_MBID)

        self.assertEqual(result, cached)
        self.assertEqual(result["name"], "Nine Inch Nails")
        self.assertEqual(result["image"], "https://example.com/artist.jpg")
        self.assertIsNone(result["biography"])
        self.assertEqual(result["birth_date"], "1988")
        self.assertEqual(result["place_of_birth"], "Cleveland")
        self.assertEqual(
            [credit["title"] for credit in result["credits"]],
            ["With Teeth", "The Fragile", "Undated"],
        )
        fragile = result["credits"][1]
        self.assertEqual(fragile["media_type"], MediaTypes.MUSIC.value)
        self.assertEqual(fragile["source"], Sources.MUSICBRAINZ.value)
        self.assertEqual(fragile["genres"], ["industrial rock"])
        self.assertEqual(fragile["vote_average"], 4.4)
        self.assertEqual(fragile["vote_count"], 20)
        self.assertEqual(fragile["roles"], ["Artist"])
        self.assertEqual(browse_release_groups.call_count, 2)
        self.assertEqual(browse_release_groups.call_args_list[0].kwargs["offset"], 0)
        self.assertEqual(browse_release_groups.call_args_list[1].kwargs["offset"], 2)
        lookup_artist.assert_called_once_with(ARTIST_MBID)

    def test_release_group_categories_separate_studio_albums_from_other_releases(self):
        cases = (
            ({"primary-type": "Album"}, "Albums"),
            ({"primary-type": "EP"}, "EPs"),
            ({"primary-type": "Single"}, "Singles"),
            ({"primary-type": "Album", "secondary-types": ["Compilation"]}, "Compilations"),
            ({"primary-type": "Album", "secondary-types": ["Live"]}, "Live releases"),
            ({"primary-type": "Album", "secondary-types": ["Mixtape/Street"]}, "Mixtapes"),
            ({}, "Other"),
        )

        for group, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(musicbrainz._release_group_category(group), expected)

    @patch("app.providers.musicbrainz.browse_artist_release_groups")
    @patch("app.providers.musicbrainz.lookup_artist")
    def test_person_page_uses_area_and_image_fallback_for_group(
        self,
        lookup_artist,
        browse_release_groups,
    ):
        lookup_artist.return_value = {
            "id": "group-artist",
            "name": "Example Band",
            "life-span": {"begin": "2001", "end": "2010"},
            "area": {"name": "Canada"},
        }
        browse_release_groups.return_value = {
            "release-group-count": 0,
            "release-groups": [],
        }

        result = musicbrainz.person_page("group-artist")

        self.assertEqual(result["image"], settings.IMG_NONE)
        self.assertEqual(result["birth_date"], "2001")
        self.assertEqual(result["death_date"], "2010")
        self.assertEqual(result["place_of_birth"], "Canada")

    @patch("app.providers.musicbrainz.browse_artist_release_groups")
    @patch("app.providers.musicbrainz.lookup_artist")
    def test_person_page_caps_release_groups_at_500(
        self,
        lookup_artist,
        browse_release_groups,
    ):
        lookup_artist.return_value = {"id": "prolific-artist", "name": "Prolific"}

        def page(_artist_mbid, *, limit, offset):
            return {
                "release-group-count": 900,
                "release-groups": [
                    {"id": f"group-{index}", "title": f"Release {index}"}
                    for index in range(offset, offset + limit)
                ],
            }

        browse_release_groups.side_effect = page

        result = musicbrainz.person_page("prolific-artist")

        self.assertEqual(len(result["credits"]), 500)
        self.assertEqual(browse_release_groups.call_count, 5)
        self.assertEqual(browse_release_groups.call_args_list[-1].kwargs["offset"], 400)

    @patch("app.providers.musicbrainz.services.api_request")
    def test_browse_releases_requests_selection_inputs(self, api_request):
        api_request.return_value = {"release-count": 0, "releases": []}

        musicbrainz.browse_releases(RELEASE_GROUP_MBID, limit=100, offset=200)

        self.assertEqual(
            api_request.call_args.kwargs["params"],
            {
                "fmt": "json",
                "release-group": RELEASE_GROUP_MBID,
                "inc": "artist-credits+labels+media+url-rels",
                "limit": 100,
                "offset": 200,
            },
        )

    @patch("app.providers.musicbrainz.services.api_request")
    def test_lookup_release_requests_media_and_recordings(self, api_request):
        api_request.return_value = {"id": RELEASE_MBID, "media": []}

        result = musicbrainz.lookup_release(RELEASE_MBID)

        self.assertEqual(result["id"], RELEASE_MBID)
        self.assertEqual(
            api_request.call_args.kwargs["params"],
            {
                "fmt": "json",
                "inc": (
                    "artist-credits+labels+media+recordings+isrcs+url-rels"
                ),
            },
        )

    @patch("app.providers.musicbrainz.services.api_request")
    def test_lookup_recording_requests_core_relationships(self, api_request):
        api_request.return_value = {"id": RECORDING_MBID}

        result = musicbrainz.lookup_recording(RECORDING_MBID)
        cached = musicbrainz.lookup_recording(RECORDING_MBID)

        self.assertEqual(result["id"], RECORDING_MBID)
        self.assertEqual(cached, result)
        self.assertEqual(api_request.call_count, 1)
        self.assertEqual(
            api_request.call_args.kwargs["params"],
            {
                "fmt": "json",
                "inc": (
                    "artist-credits+isrcs+genres+ratings+annotation+url-rels+"
                    "work-rels+work-level-rels+artist-rels+recording-rels"
                ),
            },
        )

    @patch("app.providers.musicbrainz.services.api_request")
    def test_browse_recording_releases_requests_album_identity(self, api_request):
        api_request.return_value = {"release-count": 0, "releases": []}

        musicbrainz.browse_recording_releases(RECORDING_MBID, limit=100, offset=200)

        self.assertEqual(
            api_request.call_args.kwargs["params"],
            {
                "fmt": "json",
                "recording": RECORDING_MBID,
                "inc": "artist-credits+release-groups",
                "limit": 100,
                "offset": 200,
            },
        )

    @patch("app.providers.musicbrainz.browse_recording_releases")
    @patch("app.providers.musicbrainz.lookup_recording")
    def test_recording_normalizes_relationships_and_all_release_pages(
        self,
        lookup_recording,
        browse_recording_releases,
    ):
        lookup_recording.return_value = {
            "id": RECORDING_MBID,
            "title": "HYPERPOWER!",
            "length": 102000,
            "first-release-date": "2007-04-13",
            "artist-credit": [
                {
                    "name": "Nine Inch Nails",
                    "artist": {"id": "artist-1"},
                },
            ],
            "isrcs": ["USUM70727128", "USUM70727128"],
            "genres": [{"name": "industrial rock"}],
            "rating": {"value": 4.5, "votes-count": 8},
            "annotation": {"text": "  Instrumental opener.  "},
            "relations": [
                {
                    "target-type": "work",
                    "type": "performance",
                    "work": {
                        "id": "work-1",
                        "title": "HYPERPOWER!",
                        "iswcs": ["T-073.350.911-9"],
                        "language": "zxx",
                        "relations": [
                            {
                                "type": "composer",
                                "artist": {"id": "writer-1", "name": "Trent Reznor"},
                            },
                            {
                                "type": "lyricist",
                                "artist": {"id": "writer-1", "name": "Trent Reznor"},
                            },
                            {
                                "type": "producer",
                                "artist": {"id": "producer-1", "name": "Producer"},
                            },
                        ],
                    },
                },
                {
                    "target-type": "recording",
                    "type": "remaster",
                    "direction": "forward",
                    "recording": {
                        "id": "recording-alt",
                        "title": "HYPERPOWER! (remaster)",
                        "length": 103000,
                        "artist-credit": [
                            {
                                "name": "Nine Inch Nails",
                                "artist": {"id": "artist-1"},
                            },
                        ],
                    },
                },
                {
                    "target-type": "url",
                    "type": "other databases",
                    "url": {"resource": "http://www.discogs.com/recording/example"},
                },
            ],
        }
        releases = [
            {
                "id": "release-1",
                "title": "Year Zero",
                "date": "2007-04-13",
                "status": "Official",
                "country": "US",
                "artist-credit": [
                    {
                        "name": "Nine Inch Nails",
                        "artist": {"id": "artist-1"},
                    },
                ],
                "release-group": {
                    "id": RELEASE_GROUP_MBID,
                    "title": "Year Zero",
                    "first-release-date": "2007-04-13",
                },
            },
            {
                "id": "release-2",
                "title": "Year Zero",
                "date": "2016-09-02",
                "release-group": {
                    "id": RELEASE_GROUP_MBID,
                    "title": "Year Zero",
                    "first-release-date": "2007-04-13",
                },
            },
        ]
        browse_recording_releases.side_effect = [
            {"release-count": 2, "releases": [releases[0]]},
            {"release-count": 2, "releases": [releases[1]]},
        ]

        result = musicbrainz.recording(RECORDING_MBID)

        self.assertEqual(result["recording_mbid"], RECORDING_MBID)
        self.assertEqual(result["isrcs"], ["USUM70727128"])
        self.assertEqual(result["genres"], ["industrial rock"])
        self.assertEqual(result["rating"], {"value": 4.5, "votes_count": 8, "max_value": 5})
        self.assertEqual(result["annotation"], "Instrumental opener.")
        self.assertEqual(
            result["works"][0]["credits"],
            [
                {
                    "artist_mbid": "writer-1",
                    "name": "Trent Reznor",
                    "roles": ["composer", "lyricist"],
                },
            ],
        )
        self.assertEqual(result["alternative_recordings"][0]["recording_mbid"], "recording-alt")
        self.assertEqual(len(result["releases"]), 2)
        self.assertEqual(len(result["albums"]), 1)
        self.assertEqual(
            [call.kwargs["offset"] for call in browse_recording_releases.call_args_list],
            [0, 1],
        )

    @patch("app.providers.musicbrainz.browse_recording_releases")
    @patch("app.providers.musicbrainz.lookup_recording")
    def test_recording_variants_keep_distinct_mbids(
        self,
        lookup_recording,
        browse_recording_releases,
    ):
        variants = [
            ("97d09e1b-8812-45fd-830f-0200a3c0e3b8", "Survivalism", 264000),
            ("61e7a5b8-5fdb-4524-8983-bdb7ca243a52", "Survivalism", 301000),
            ("04bd34a4-bb7c-4acc-885b-0a643e7e3d4b", "Survivalism (deadmau5 remix)", 217500),
        ]
        lookup_recording.side_effect = [
            {"id": mbid, "title": title, "length": length}
            for mbid, title, length in variants
        ]
        browse_recording_releases.return_value = {"release-count": 0, "releases": []}

        results = [musicbrainz.recording(mbid) for mbid, _title, _length in variants]

        self.assertEqual(
            [(item["recording_mbid"], item["title"], item["length_ms"]) for item in results],
            variants,
        )
        self.assertTrue(all(item["isrcs"] == [] for item in results))
        self.assertTrue(all(item["works"] == [] for item in results))
        self.assertTrue(all(item["rating"] is None for item in results))

    @patch("app.providers.musicbrainz.services.api_request")
    def test_http_404_is_translated(self, api_request):
        api_request.side_effect = http_error(requests.codes.not_found)

        with self.assertRaises(services.ProviderAPIError) as raised:
            musicbrainz.lookup_release_group(RELEASE_GROUP_MBID)

        self.assertEqual(raised.exception.provider, Sources.MUSICBRAINZ.value)
        self.assertEqual(raised.exception.status_code, requests.codes.not_found)

    @patch("app.providers.musicbrainz.services.api_request")
    def test_503_is_retried_then_succeeds(self, api_request):
        response = {"id": RELEASE_GROUP_MBID}
        api_request.side_effect = [
            http_error(requests.codes.service_unavailable),
            response,
        ]

        self.assertEqual(
            musicbrainz.lookup_release_group(RELEASE_GROUP_MBID),
            response,
        )
        self.assertEqual(api_request.call_count, 2)

    @patch("app.providers.musicbrainz.services.api_request")
    def test_503_retry_exhaustion_stops_after_three_attempts(self, api_request):
        api_request.side_effect = http_error(requests.codes.service_unavailable)

        with self.assertRaises(services.ProviderAPIError) as raised:
            musicbrainz.lookup_release_group(RELEASE_GROUP_MBID)

        self.assertEqual(raised.exception.status_code, requests.codes.service_unavailable)
        self.assertEqual(api_request.call_count, musicbrainz.MAX_ATTEMPTS)

    @patch("app.providers.musicbrainz.services.api_request")
    def test_transient_connection_error_is_retried(self, api_request):
        response = {"id": RELEASE_GROUP_MBID}
        api_request.side_effect = [
            requests.exceptions.SSLError("unexpected EOF"),
            response,
        ]

        self.assertEqual(
            musicbrainz.lookup_release_group(RELEASE_GROUP_MBID),
            response,
        )
        self.assertEqual(api_request.call_count, 2)

    @patch("app.providers.musicbrainz.services.api_request")
    def test_invalid_json_is_translated(self, api_request):
        api_request.side_effect = requests.exceptions.JSONDecodeError(
            "invalid JSON",
            "not-json",
            0,
        )

        with self.assertRaisesRegex(services.ProviderAPIError, "invalid JSON response"):
            musicbrainz.lookup_release_group(RELEASE_GROUP_MBID)

    @patch("app.providers.musicbrainz.services.api_request")
    def test_timeout_is_translated_without_retry(self, api_request):
        api_request.side_effect = requests.exceptions.Timeout("timed out")

        with self.assertRaises(services.ProviderAPIError) as raised:
            musicbrainz.lookup_release_group(RELEASE_GROUP_MBID)

        self.assertIn("network error", str(raised.exception))
        api_request.assert_called_once()

    @patch("app.providers.musicbrainz.services.api_request")
    def test_positive_cache_avoids_provider(self, api_request):
        response = {"count": 0, "release-groups": []}
        api_request.return_value = response

        first = musicbrainz.search_release_groups("cache me")
        second = musicbrainz.search_release_groups("cache me")

        self.assertEqual(first, response)
        self.assertEqual(second, response)
        api_request.assert_called_once()

    def test_cache_single_flight_runs_one_cold_fetch(self):
        class FakeLock:
            def __init__(self, lock):
                self.lock = lock

            def acquire(self, *, blocking, blocking_timeout):
                return self.lock.acquire(blocking, blocking_timeout)

            def release(self):
                self.lock.release()

        class FakeCache:
            def __init__(self):
                self.data = {}
                self.flight = threading.Lock()

            def get(self, key, default=None):
                return self.data.get(key, default)

            def set(self, key, value, _timeout):
                self.data[key] = value

            def lock(self, _key, **_kwargs):
                return FakeLock(self.flight)

        provider_cache = FakeCache()
        fetch_count = 0
        fetch_lock = threading.Lock()

        def fetch():
            nonlocal fetch_count
            with fetch_lock:
                fetch_count += 1
            time.sleep(0.05)
            return {"id": RELEASE_GROUP_MBID}

        with (
            patch.object(musicbrainz, "cache", provider_cache),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            results = list(executor.map(lambda _index: musicbrainz._cached("cold", 60, fetch), range(2)))

        self.assertEqual(fetch_count, 1)
        self.assertEqual(results, [{"id": RELEASE_GROUP_MBID}] * 2)

    @patch("app.providers.musicbrainz.services.api_request")
    def test_cover_art_success_uses_general_session_and_cache(self, api_request):
        response = {"images": [{"front": True}]}
        api_request.return_value = response

        first = musicbrainz.lookup_cover_art(RELEASE_GROUP_MBID)
        second = musicbrainz.lookup_cover_art(RELEASE_GROUP_MBID)

        self.assertEqual(first, response)
        self.assertEqual(second, response)
        api_request.assert_called_once()
        self.assertIs(api_request.call_args.kwargs["request_session"], services.session)

    @patch("app.providers.musicbrainz.services.api_request")
    def test_cover_art_404_is_a_cached_miss(self, api_request):
        api_request.side_effect = http_error(requests.codes.not_found)

        self.assertIsNone(musicbrainz.lookup_cover_art(RELEASE_GROUP_MBID))
        self.assertIsNone(musicbrainz.lookup_cover_art(RELEASE_GROUP_MBID))

        api_request.assert_called_once()

    @patch("app.providers.musicbrainz.services.api_request")
    def test_cache_timeouts(self, api_request):
        api_request.return_value = {"id": RELEASE_GROUP_MBID}
        with patch.object(musicbrainz, "cache") as provider_cache:
            provider_cache.get.return_value = musicbrainz._CACHE_MISS

            musicbrainz.search_release_groups("ttl")
            self.assertEqual(provider_cache.set.call_args.args[2], musicbrainz.SEARCH_CACHE_TTL)

            provider_cache.reset_mock()
            provider_cache.get.return_value = musicbrainz._CACHE_MISS
            musicbrainz.lookup_release_group(RELEASE_GROUP_MBID)
            self.assertEqual(provider_cache.set.call_args.args[2], musicbrainz.DETAIL_CACHE_TTL)

            provider_cache.reset_mock()
            provider_cache.get.return_value = musicbrainz._CACHE_MISS
            musicbrainz.lookup_cover_art(RELEASE_GROUP_MBID)
            self.assertEqual(provider_cache.set.call_args.args[2], musicbrainz.DETAIL_CACHE_TTL)

    @patch("app.providers.musicbrainz.services.api_request")
    def test_missing_cover_art_uses_short_cache_timeout(self, api_request):
        api_request.side_effect = http_error(requests.codes.not_found)
        with patch.object(musicbrainz, "cache") as provider_cache:
            provider_cache.get.return_value = musicbrainz._CACHE_MISS

            self.assertIsNone(musicbrainz.lookup_cover_art(RELEASE_GROUP_MBID))

            self.assertEqual(
                provider_cache.set.call_args.args,
                (
                    f"coverartarchive_v2_release_group_{RELEASE_GROUP_MBID}",
                    False,
                    musicbrainz.MISSING_COVER_CACHE_TTL,
                ),
            )

    @requires_provider_network
    def test_live_release_group_normalization(self):
        search = musicbrainz.search("Year Zero Nine Inch Nails", 1)
        detail = musicbrainz.music(RELEASE_GROUP_MBID)

        self.assertIn(RELEASE_GROUP_MBID, [item["media_id"] for item in search["results"]])
        self.assertEqual(detail["media_id"], RELEASE_GROUP_MBID)
        self.assertEqual(detail["media_type"], MediaTypes.MUSIC.value)


@override_settings(MUSIC_DEFAULT_MARKET="US")
class RepresentativeReleaseTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch("app.providers.musicbrainz.lookup_release")
    @patch("app.providers.musicbrainz.browse_releases")
    def test_market_standard_release_beats_deluxe_and_worldwide(
        self,
        browse_releases,
        lookup_release,
    ):
        standard = candidate("00000000-0000-0000-0000-000000000002")
        deluxe = candidate(
            "00000000-0000-0000-0000-000000000001",
            "Album (D.L.X.)",
            release_date="2019-01-01",
        )
        worldwide = candidate(
            "00000000-0000-0000-0000-000000000003",
            country="XW",
        )
        browse_releases.return_value = {
            "release-count": 3,
            "releases": [deluxe, worldwide, standard],
        }
        lookup_release.side_effect = lambda release_mbid: full_release(
            {release["id"]: release for release in [standard, deluxe, worldwide]}[
                release_mbid
            ],
        )

        result = musicbrainz.resolve_representative_release(
            RELEASE_GROUP_MBID,
            "Album",
        )

        self.assertEqual(result["release_mbid"], standard["id"])
        self.assertEqual(result["reason"], "streaming_market_match")

    @patch("app.providers.musicbrainz.lookup_release")
    @patch("app.providers.musicbrainz.browse_releases")
    def test_worldwide_standard_release_reason(self, browse_releases, lookup_release):
        release = candidate(
            "00000000-0000-0000-0000-000000000004",
            country="XW",
        )
        browse_releases.return_value = {"release-count": 1, "releases": [release]}
        lookup_release.return_value = full_release(release)

        result = musicbrainz.resolve_representative_release(
            RELEASE_GROUP_MBID,
            "Album",
        )

        self.assertEqual(result["reason"], "streaming_standard_edition")

    @patch("app.providers.musicbrainz.lookup_release")
    @patch("app.providers.musicbrainz.browse_releases")
    def test_missing_streaming_uses_earliest_official(
        self,
        browse_releases,
        lookup_release,
    ):
        later = candidate(
            "00000000-0000-0000-0000-000000000006",
            release_date="2003",
            formats=("CD",),
            streaming=False,
        )
        earlier = candidate(
            "00000000-0000-0000-0000-000000000005",
            release_date="2003-01-01",
            formats=("CD",),
            streaming=False,
        )
        browse_releases.return_value = {
            "release-count": 2,
            "releases": [later, earlier],
        }
        lookup_release.side_effect = lambda release_mbid: full_release(
            {later["id"]: later, earlier["id"]: earlier}[release_mbid],
        )

        result = musicbrainz.resolve_representative_release(
            RELEASE_GROUP_MBID,
            "Album",
        )

        self.assertEqual(result["release_mbid"], earlier["id"])
        self.assertEqual(result["reason"], "earliest_official")

    @patch("app.providers.musicbrainz.lookup_release")
    @patch("app.providers.musicbrainz.browse_releases")
    def test_nonofficial_complete_release_is_final_fallback(
        self,
        browse_releases,
        lookup_release,
    ):
        official = candidate(
            "00000000-0000-0000-0000-000000000012",
            formats=("CD",),
            streaming=False,
        )
        bootleg = candidate(
            "00000000-0000-0000-0000-000000000013",
            status="Bootleg",
            formats=("CD",),
            streaming=False,
        )
        browse_releases.return_value = {
            "release-count": 2,
            "releases": [official, bootleg],
        }
        lookup_release.side_effect = lambda release_mbid: (
            full_release(official, complete=False)
            if release_mbid == official["id"]
            else full_release(bootleg)
        )

        result = musicbrainz.resolve_representative_release(
            RELEASE_GROUP_MBID,
            "Album",
        )

        self.assertEqual(result["release_mbid"], bootleg["id"])
        self.assertEqual(result["reason"], "complete_tracklist_fallback")

    @patch("app.providers.musicbrainz.lookup_release")
    @patch("app.providers.musicbrainz.browse_releases")
    def test_incomplete_candidate_is_removed_and_all_incomplete_degrades(
        self,
        browse_releases,
        lookup_release,
    ):
        first = candidate("00000000-0000-0000-0000-000000000007")
        second = candidate("00000000-0000-0000-0000-000000000008")
        browse_releases.return_value = {
            "release-count": 2,
            "releases": [first, second],
        }
        lookup_release.side_effect = [
            full_release(first, complete=False),
            full_release(second),
            full_release(second),
        ]

        selected = musicbrainz.resolve_representative_release(
            RELEASE_GROUP_MBID,
            "Album",
        )
        self.assertEqual(selected["release_mbid"], second["id"])

        cache.clear()
        lookup_release.side_effect = [
            full_release(first, complete=False),
            full_release(second, complete=False),
        ]
        degraded = musicbrainz.resolve_representative_release(
            "10000000-0000-0000-0000-000000000000",
            "Album",
        )
        self.assertIsNone(degraded["release"])
        self.assertIsNone(degraded["reason"])

    @patch("app.providers.musicbrainz.browse_releases")
    def test_pagination_advances_by_actual_batch_size(self, browse_releases):
        releases = [
            candidate(f"00000000-0000-0000-0000-{index:012d}")
            for index in range(30)
        ]
        browse_releases.side_effect = [
            {"release-count": 30, "releases": releases[:20]},
            {"release-count": 30, "releases": releases[20:]},
        ]

        self.assertEqual(len(musicbrainz._all_releases(RELEASE_GROUP_MBID)), 30)
        self.assertEqual(
            [call.kwargs["offset"] for call in browse_releases.call_args_list],
            [0, 20],
        )

    @patch("app.providers.musicbrainz.lookup_recording")
    def test_multidisc_payload_preserves_printed_number_and_isrcs(
        self,
        lookup_recording,
    ):
        release = candidate(
            RELEASE_MBID,
            formats=("Digital Media", "Digital Media"),
            track_counts=(1, 1),
        )
        payload = musicbrainz._representative_release(
            full_release(
                release,
                printed_number="A1",
                isrcs=["USAAA0000001"],
            ),
            "streaming_market_match",
        )

        self.assertEqual(payload["disc_count"], 2)
        self.assertEqual(payload["track_count"], 2)
        self.assertEqual(payload["media"][1]["tracks"][0]["disc_number"], 2)
        self.assertEqual(payload["media"][0]["tracks"][0]["number"], "A1")
        self.assertEqual(
            payload["media"][0]["tracks"][0]["recording"]["isrcs"],
            ["USAAA0000001"],
        )
        lookup_recording.assert_not_called()

    @patch("app.providers.musicbrainz.lookup_release")
    @patch("app.providers.musicbrainz.browse_releases")
    def test_mbid_tie_break_is_deterministic(self, browse_releases, lookup_release):
        first = candidate("00000000-0000-0000-0000-000000000009")
        second = candidate("00000000-0000-0000-0000-000000000010")
        lookup_release.side_effect = lambda release_mbid: full_release(
            {first["id"]: first, second["id"]: second}[release_mbid],
        )

        selected = []
        for releases in ([second, first], [first, second]):
            browse_releases.return_value = {
                "release-count": 2,
                "releases": releases,
            }
            selected.append(
                musicbrainz._select_representative_release(
                    RELEASE_GROUP_MBID,
                    "Album",
                )["release_mbid"],
            )

        self.assertEqual(selected, [first["id"], first["id"]])

    @patch("app.providers.musicbrainz.lookup_release")
    @patch("app.providers.musicbrainz.browse_releases")
    def test_selection_is_cached_by_release_group_and_version(
        self,
        browse_releases,
        lookup_release,
    ):
        release = candidate("00000000-0000-0000-0000-000000000011")
        browse_releases.return_value = {"release-count": 1, "releases": [release]}
        lookup_release.return_value = full_release(release)

        musicbrainz.resolve_representative_release(RELEASE_GROUP_MBID, "Album")
        musicbrainz.resolve_representative_release(RELEASE_GROUP_MBID, "Album")

        browse_releases.assert_called_once_with(RELEASE_GROUP_MBID, limit=100, offset=0)
        self.assertEqual(
            cache.get(
                f"musicbrainz_representative_release_{musicbrainz.RESOLVER_VERSION}_{RELEASE_GROUP_MBID}",
            )["release_mbid"],
            release["id"],
        )

    @patch("app.providers.musicbrainz.resolve_representative_release")
    @patch("app.providers.musicbrainz.lookup_cover_art", return_value=None)
    @patch("app.providers.musicbrainz.lookup_release_group")
    def test_resolution_failure_keeps_basic_album_metadata(
        self,
        lookup_release_group,
        _lookup_cover_art,
        resolve,
    ):
        lookup_release_group.return_value = {
            "id": RELEASE_GROUP_MBID,
            "title": "Year Zero",
            "first-release-date": "2007-04-13",
        }
        resolve.side_effect = services.ProviderAPIError(
            Sources.MUSICBRAINZ.value,
            requests.exceptions.Timeout("timed out"),
        )

        with self.assertLogs(musicbrainz.logger, level="ERROR"):
            result = musicbrainz.music(RELEASE_GROUP_MBID)

        self.assertEqual(result["title"], "Year Zero")
        self.assertEqual(result["release_date"], "2007-04-13")
        self.assertIsNone(result["music"]["representative_release"])

    @patch("app.providers.musicbrainz.lookup_release")
    @patch("app.providers.musicbrainz.browse_releases")
    def test_phase_zero_fixture_corpus(self, browse_releases, lookup_release):
        fixtures = json.loads(CORPUS_PATH.read_text())
        for fixture in fixtures:
            with self.subTest(release_group=fixture["release_group_mbid"]):
                cache.clear()
                release = fixture["release"]
                browse_releases.return_value = {
                    "release-count": 1,
                    "releases": [release],
                }
                lookup_release.return_value = full_release(release)

                result = musicbrainz.resolve_representative_release(
                    fixture["release_group_mbid"],
                    fixture["title"],
                )

                self.assertEqual(
                    result["release_mbid"],
                    fixture["expected_release_mbid"],
                )


class MusicBrainzRateLimitTests(TestCase):
    def test_musicbrainz_session_uses_dedicated_redis_bucket(self):
        factory = services.musicbrainz_session.limiter.bucket_factory

        self.assertIs(factory.bucket_class, RedisBucket)
        self.assertEqual(
            factory.bucket_init_kwargs["bucket_key"],
            services.musicbrainz_bucket_key,
        )
        self.assertEqual(factory.rates[0].limit, 1)
        self.assertEqual(factory.rates[0].interval, Duration.SECOND)
        self.assertNotEqual(services.musicbrainz_bucket_key, services.bucket_key)

    def test_separate_workers_share_one_atomic_bucket(self):
        fake_server = fakeredis.FakeServer()
        worker_one_redis = fakeredis.FakeRedis(server=fake_server)
        worker_two_redis = fakeredis.FakeRedis(server=fake_server)
        rates = [Rate(1, Duration.SECOND)]
        worker_one = RedisBucket.init(rates, worker_one_redis, "musicbrainz_test")
        worker_two = RedisBucket.init(rates, worker_two_redis, "musicbrainz_test")

        self.assertTrue(worker_one.put(RateItem("worker-one", 1000)))
        self.assertFalse(worker_two.put(RateItem("worker-two", 1000)))
