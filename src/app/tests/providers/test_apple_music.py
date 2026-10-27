import base64
import copy
import json
from http.client import RemoteDisconnected
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import jwt
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.conf import settings
from django.core.cache import cache
from django.test import TestCase, override_settings
from pyrate_limiter import Duration, RedisBucket
from urllib3.exceptions import ProtocolError

from app.models import (
    AppleAlbum,
    Item,
    ItemFilterFacet,
    MediaTypes,
    MusicAlbumLink,
    Sources,
)
from app.providers import apple_music, quota, services

# Recorded by scripts/music/record_apple_fixtures.py.
FIXTURES = Path(__file__).parents[1] / "mock_data" / "apple_music"
TEAM_ID = "TEAM123456"
KEY_ID = "KEY7890123"


def throwaway_key():
    """Return a fresh P-256 key and its setting value (base64 of the PEM, one line)."""
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return key, base64.b64encode(pem).decode()


SIGNING_KEY, SIGNING_KEY_SETTING = throwaway_key()


def fixture(name):
    return json.loads((FIXTURES / name).read_text())


def page(name, **attributes):
    """Return a recorded album page, with its album's attributes changed if asked."""
    data = fixture(name)
    data["data"][0]["attributes"].update(attributes)
    return data


def related_answers(editions="sos", artist="sza"):
    """Return the answers to an album page's related-section requests, in order.

    The songs of the album's discs, the batch of albums that share them, and the
    artist's albums.
    """
    return (
        (200, fixture(f"editions_songs_{editions}.json")),
        (200, fixture(f"editions_albums_{editions}.json")),
        (200, fixture(f"artist_albums_{artist}.json")),
    )


def apple_item(media_id, title="SOS", image="https://example.com/cover.jpg", **fields):
    return Item.objects.create(
        source=Sources.APPLE_MUSIC.value,
        media_type=MediaTypes.MUSIC.value,
        media_id=media_id,
        title=title,
        image=image,
        **fields,
    )


def genre_facets(item, *values):
    for value in values:
        ItemFilterFacet.objects.create(
            item=item,
            facet_type=ItemFilterFacet.FacetType.GENRE,
            value=value,
        )


def clear_pause():
    services.redis_db.delete(apple_music.backoff_key())


def pause_ttl():
    return services.redis_db.ttl(apple_music.backoff_key())


def query(request):
    return {key: values[0] for key, values in parse_qs(urlsplit(request.url).query).items()}


class FakeApple:
    """Stands in for one Apple session's ``send``: answers each request in turn.

    Each answer is ``(status, body)``, ``(status, b"raw bytes")`` or an exception.
    """

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests = []
        self.options = []

    def __call__(self, request, **options):
        self.requests.append(request)
        self.options.append(options)
        if not self.answers:
            msg = f"Unexpected Apple request: {request.method} {request.url}"
            raise AssertionError(msg)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        response = requests.Response()
        response.status_code = status
        response._content = body if isinstance(body, bytes) else json.dumps(body).encode()
        response.headers["Content-Type"] = "application/json"
        response.encoding = "utf-8"
        response.url = request.url
        response.request = request
        return response


@override_settings(
    APPLE_MUSIC_TEAM_ID=TEAM_ID,
    APPLE_MUSIC_KEY_ID=KEY_ID,
    APPLE_MUSIC_PRIVATE_KEY=SIGNING_KEY_SETTING,
    MUSIC_DEFAULT_MARKET="US",
    MUSIC_CATALOG_SOURCE="musicbrainz",
)
class AppleMusicTestCase(TestCase):
    def setUp(self):
        cache.clear()
        clear_pause()
        apple_music._token = None
        self.addCleanup(setattr, apple_music, "_token", None)
        # A request that misses its fake fails instead of reaching Apple.
        network = patch(
            "requests.adapters.HTTPAdapter.send",
            side_effect=AssertionError("A test tried to reach the network"),
        )
        network.start()
        self.addCleanup(network.stop)
        sleep = patch("app.providers.services.time.sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def apple(self, *answers, background=False):
        fake = FakeApple(*answers)
        session = (
            services.apple_music_background_session
            if background
            else services.apple_music_session
        )
        patcher = patch.object(session, "send", fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        return fake


class DeveloperTokenTests(AppleMusicTestCase):
    def test_token_is_es256_with_key_id_team_and_twelve_hours(self):
        token = apple_music.developer_token()

        header = jwt.get_unverified_header(token)
        claims = jwt.decode(token, SIGNING_KEY.public_key(), algorithms=["ES256"])
        self.assertEqual(header["alg"], "ES256")
        self.assertEqual(header["kid"], KEY_ID)
        self.assertEqual(claims["iss"], TEAM_ID)
        self.assertEqual(claims["exp"] - claims["iat"], 12 * 60 * 60)

    def test_token_is_kept_until_less_than_an_hour_is_left(self):
        start = 1_800_000_000
        with patch("app.providers.apple_music.time.time", return_value=start):
            first = apple_music.developer_token()
        with patch("app.providers.apple_music.time.time", return_value=start + 11 * 60 * 60 - 1):
            self.assertEqual(apple_music.developer_token(), first)
        with patch("app.providers.apple_music.time.time", return_value=start + 11 * 60 * 60 + 1):
            second = apple_music.developer_token()

        self.assertNotEqual(second, first)
        claims = jwt.decode(
            second,
            SIGNING_KEY.public_key(),
            algorithms=["ES256"],
            options={"verify_exp": False, "verify_iat": False},
        )
        self.assertEqual(claims["iat"], start + 11 * 60 * 60 + 1)

    def test_new_key_settings_sign_a_new_token(self):
        first = apple_music.developer_token()
        new_key, new_setting = throwaway_key()

        with override_settings(APPLE_MUSIC_PRIVATE_KEY=new_setting):
            second = apple_music.developer_token()

        self.assertNotEqual(second, first)
        jwt.decode(second, new_key.public_key(), algorithms=["ES256"])

    def test_missing_settings_raise_before_any_request(self):
        fake = self.apple()
        for setting in ("APPLE_MUSIC_TEAM_ID", "APPLE_MUSIC_KEY_ID", "APPLE_MUSIC_PRIVATE_KEY"):
            with self.subTest(setting=setting), override_settings(**{setting: ""}):
                self.assertFalse(apple_music.is_configured())
                with self.assertRaisesMessage(
                    services.ProviderAPIError,
                    "Apple Music isn't configured",
                ) as raised:
                    apple_music.album("1657869377")
                self.assertIsNone(raised.exception.status_code)
                self.assertEqual(raised.exception.provider, "apple_music")

        self.assertEqual(fake.requests, [])

    def test_unreadable_key_fails_without_quoting_it(self):
        bad_setting = base64.b64encode(b"-----BEGIN PRIVATE KEY-----\nnot a key\n").decode()
        fake = self.apple()

        with (
            override_settings(APPLE_MUSIC_PRIVATE_KEY=bad_setting),
            self.assertLogs("app.providers.apple_music", level="ERROR") as logs,
            self.assertRaises(services.ProviderAPIError) as raised,
        ):
            apple_music.album("1657869377")

        self.assertIn("private key can't be read", str(raised.exception))
        self.assertIsNone(raised.exception.__cause__)
        self.assertTrue(raised.exception.__suppress_context__)
        self.assertNotIn(bad_setting, "\n".join(logs.output))
        self.assertNotIn("not a key", "\n".join(logs.output) + str(raised.exception))
        self.assertEqual(fake.requests, [])

    def test_token_is_never_logged_cached_or_shown_in_errors(self):
        token = apple_music.developer_token()
        self.apple(
            (200, fixture("album_thriller.json")),
            (401, b""),
            (200, b"<html>not json</html>"),
            requests.exceptions.ReadTimeout("read timed out"),
        )
        errors = []

        with self.assertLogs(level="DEBUG") as logs:
            apple_music.album("269572838")
            for _ in range(3):
                try:
                    apple_music.album("269572838")
                except services.ProviderAPIError as error:
                    errors.append(error)
                clear_pause()

        self.assertEqual(len(errors), 3)
        records = "\n".join(logs.output + [record.getMessage() for record in logs.records])
        self.assertNotIn(token, records)
        self.assertNotIn(token, " ".join(str(error) for error in errors))
        for key in cache.keys("*"):
            self.assertNotIn(token, f"{key} {cache.get(key)!r}")


class RequestTests(AppleMusicTestCase):
    def test_album_request_uses_the_storefront_token_and_tracks(self):
        fake = self.apple((200, fixture("album_thriller.json")))

        album = apple_music.album("269572838")

        request = fake.requests[0]
        self.assertEqual(
            urlsplit(request.url).path,
            "/v1/catalog/us/albums/269572838",
        )
        self.assertEqual(query(request), {"include": "tracks"})
        self.assertEqual(
            request.headers["Authorization"],
            f"Bearer {apple_music.developer_token()}",
        )
        self.assertEqual(album["attributes"]["name"], "Thriller")
        self.assertEqual(len(album["relationships"]["tracks"]["data"]), 9)

    @override_settings(MUSIC_DEFAULT_MARKET="GB")
    def test_storefront_comes_from_the_music_market(self):
        fake = self.apple((200, fixture("album_thriller.json")))

        apple_music.album("269572838")

        self.assertTrue(urlsplit(fake.requests[0].url).path.startswith("/v1/catalog/gb/"))

    def test_user_calls_use_their_session_a_short_timeout_and_no_429_sleep(self):
        background = self.apple(background=True)
        fake = self.apple((429, {"errors": [{"status": "429"}]}))

        with self.assertRaises(services.ProviderAPIError) as raised:
            apple_music.album("269572838")

        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(len(fake.requests), 1)
        self.assertEqual(fake.options[0]["timeout"], apple_music.USER_TIMEOUT)
        self.assertEqual(background.requests, [])
        self.sleep.assert_not_called()

    def test_429_5xx_and_timeouts_pause_user_calls_for_a_minute(self):
        failures = (
            (429, {"errors": []}),
            (503, b""),
            requests.exceptions.ConnectTimeout("connect timed out"),
            requests.exceptions.ReadTimeout("read timed out"),
        )
        for failure in failures:
            with self.subTest(failure=failure):
                clear_pause()
                fake = self.apple(failure)
                with self.assertRaises(services.ProviderAPIError):
                    apple_music.album("269572838")
                self.assertTrue(apple_music.is_paused())
                self.assertLessEqual(pause_ttl(), 60)
                self.assertGreater(pause_ttl(), 0)

                with self.assertRaisesMessage(services.ProviderAPIError, "paused") as raised:
                    apple_music.album("269572838")
                self.assertIsInstance(raised.exception, apple_music.SkippedRequestError)
                self.assertEqual(len(fake.requests), 1)

    @override_settings(
        CACHES={"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}},
    )
    def test_the_pause_holds_when_the_django_cache_is_a_dummy(self):
        # Local dev runs with DEBUG on, where the Django cache stores nothing.
        fake = self.apple((429, {"errors": []}))
        with self.assertRaises(services.ProviderAPIError):
            apple_music.album("269572838")

        with self.assertRaisesMessage(services.ProviderAPIError, "paused"):
            apple_music.album("269572838")
        self.assertEqual(len(fake.requests), 1)

    def test_client_errors_do_not_pause_user_calls(self):
        for status in (401, 404):
            with self.subTest(status=status):
                self.apple((status, b""))
                with self.assertRaises(services.ProviderAPIError):
                    apple_music.album("269572838")
                self.assertFalse(apple_music.is_paused())

    def test_background_calls_retry_on_their_own_session_and_ignore_the_pause(self):
        user = self.apple()
        fake = self.apple(
            (429, {"errors": []}),
            (200, fixture("album_thriller.json")),
            background=True,
        )
        apple_music._back_off()

        album = apple_music.album("269572838", background=True)

        self.assertEqual(album["id"], "269572838")
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(fake.options[0]["timeout"], settings.REQUEST_TIMEOUT)
        self.sleep.assert_called_once()
        self.assertEqual(user.requests, [])

    def test_featured_syncs_use_the_background_session_and_never_pause_user_calls(self):
        # Featured syncs run inside a quota scope, and reach Apple through search and
        # album pages, which don't say they are background work.
        user = self.apple()
        fake = self.apple(
            requests.exceptions.ReadTimeout("read timed out"),
            (200, fixture("page_sos.json")),
            background=True,
        )

        with quota.scope("featured", {}):
            with self.assertRaises(services.ProviderAPIError):
                services.search("music", "sos", 1, source="apple_music", preserve_ranking_fields=True)
            album = services.get_media_metadata("music", "1657869377", "apple_music", include_music_enrichment=False)

        self.assertEqual(album["media_id"], "1657869377")
        self.assertEqual(len(fake.requests), 2)
        self.assertEqual(user.requests, [])
        self.assertFalse(apple_music.is_paused())

    def test_a_dropped_connection_is_tried_once_more(self):
        # What requests raises when Apple has closed an idle keep-alive connection.
        dropped = requests.exceptions.ConnectionError(
            ProtocolError(
                "Connection aborted.",
                RemoteDisconnected("Remote end closed connection without response"),
            ),
        )
        for background in (False, True):
            with self.subTest(background=background):
                fake = self.apple(dropped, (200, fixture("album_thriller.json")), background=background)

                album = apple_music.album("269572838", background=background)

                self.assertEqual(album["id"], "269572838")
                self.assertEqual(len(fake.requests), 2)
                self.assertFalse(apple_music.is_paused())

        fake = self.apple(dropped, dropped)
        with self.assertRaisesMessage(services.ProviderAPIError, "network error"):
            apple_music.album("269572838")
        self.assertEqual(len(fake.requests), 2, "Only once more")
        self.assertFalse(apple_music.is_paused())

    def test_refused_connections_and_timeouts_are_not_tried_again(self):
        for failure in (
            requests.exceptions.ConnectionError("refused"),
            requests.exceptions.ConnectTimeout("connect timed out"),
            requests.exceptions.ReadTimeout("read timed out"),
        ):
            with self.subTest(failure=failure):
                clear_pause()
                fake = self.apple(failure)
                with self.assertRaises(services.ProviderAPIError):
                    apple_music.album("269572838")
                self.assertEqual(len(fake.requests), 1)

    def test_errors_become_provider_errors_with_their_status(self):
        cases = (
            ((401, b""), 401, None),
            ((404, fixture("error_404.json")["body"]), 404, None),
            ((429, {"errors": []}), 429, None),
            ((502, b""), 502, None),
            (requests.exceptions.ReadTimeout("read timed out"), None, "network error"),
            (requests.exceptions.ConnectionError("refused"), None, "network error"),
            ((200, b"<html>not json</html>"), None, "invalid JSON response"),
            ((200, {"data": []}), None, "response has no data"),
        )
        for answer, status_code, message in cases:
            with self.subTest(answer=answer):
                clear_pause()
                self.apple(answer)
                with self.assertRaises(services.ProviderAPIError) as raised:
                    apple_music.album("269572838")
                self.assertEqual(raised.exception.provider, "apple_music")
                self.assertEqual(raised.exception.status_code, status_code)
                self.assertIn("Apple Music", str(raised.exception))
                if message:
                    self.assertIn(message, str(raised.exception))

    def test_recorded_error_fixtures(self):
        self.assertEqual(fixture("error_401.json"), {"status": 401, "body": ""})
        self.assertEqual(
            fixture("error_404.json")["body"]["errors"][0]["code"],
            "40400",
        )


class CatalogFetchTests(AppleMusicTestCase):
    def test_search_returns_a_page_with_apples_next_link(self):
        first_page = fixture("search_sza_sos_page_1.json")
        second_page = fixture("search_sza_sos_page_2.json")
        fake = self.apple((200, first_page), (200, second_page))

        first = apple_music.search("sza sos", types=("albums",))
        second = apple_music.search(
            "sza sos",
            types=("albums",),
            next_path=first["results"]["albums"]["next"],
        )

        self.assertEqual(first, first_page)
        self.assertEqual(second, second_page)
        self.assertEqual(urlsplit(fake.requests[0].url).path, "/v1/catalog/us/search")
        self.assertEqual(
            query(fake.requests[0]),
            {"term": "sza sos", "types": "albums", "limit": "25"},
        )
        # Apple's next link drops the page size, so it is added back.
        self.assertEqual(urlsplit(fake.requests[1].url).path, "/v1/catalog/us/search")
        self.assertEqual(
            query(fake.requests[1]),
            {"offset": "25", "term": "sza sos", "types": "albums", "limit": "25"},
        )
        self.assertEqual(len(first["results"]["albums"]["data"]), 25)
        self.assertEqual(first["results"]["albums"]["data"][0]["id"], "1657869377")

    def test_only_apple_paths_are_followed(self):
        fake = self.apple()
        for next_path in (
            "https://example.com/v1/catalog/us/search?offset=25",
            "//example.com/v1/catalog/us/search",
            "/v2/../../steal",
        ):
            with self.subTest(next_path=next_path), self.assertRaises(ValueError):
                apple_music.search("sza sos", types="albums", next_path=next_path)

        self.assertEqual(fake.requests, [])

    def test_album_follows_the_tracks_next_link(self):
        thriller = fixture("album_thriller.json")
        tracks = thriller["data"][0]["relationships"]["tracks"]["data"]
        first_page = copy.deepcopy(thriller)
        first_page["data"][0]["relationships"]["tracks"] = {
            "data": tracks[:5],
            "next": "/v1/catalog/us/albums/269572838/tracks?offset=5",
        }
        fake = self.apple((200, first_page), (200, {"data": tracks[5:]}))

        album = apple_music.album("269572838")

        self.assertEqual(album["relationships"]["tracks"], {"data": tracks})
        self.assertEqual(
            urlsplit(fake.requests[1].url).path,
            "/v1/catalog/us/albums/269572838/tracks",
        )
        self.assertEqual(query(fake.requests[1]), {"offset": "5"})

    def test_albums_with_views_returns_every_resource(self):
        sos = fixture("albums_sos_lana.json")
        album_ids = ["1657869377", "1658650093", "1794644675", "1786643044", "1796270264"]
        fake = self.apple((200, sos))

        resources = apple_music.albums(album_ids, views=("other-versions",))

        self.assertEqual(resources, sos["data"])
        self.assertEqual(
            query(fake.requests[0]),
            {"ids": ",".join(album_ids), "views": "other-versions"},
        )

    def test_albums_asks_for_100_ids_at_a_time(self):
        album_ids = [str(1_000_000 + number) for number in range(150)]
        fake = self.apple(
            (200, {"data": [{"id": album_ids[0]}]}),
            (200, {"data": [{"id": album_ids[100]}]}),
        )

        resources = apple_music.albums([*album_ids, album_ids[0], "", None])

        self.assertEqual([resource["id"] for resource in resources], [album_ids[0], album_ids[100]])
        self.assertEqual(
            [query(request)["ids"].split(",") for request in fake.requests],
            [album_ids[:100], album_ids[100:]],
        )

    def test_no_ids_means_no_request(self):
        fake = self.apple()

        self.assertEqual(apple_music.albums([]), [])
        self.assertEqual(apple_music.albums_by_upc([]), [])
        self.assertEqual(apple_music.songs_by_isrc([]), [])
        self.assertEqual(fake.requests, [])

    def test_albums_by_upc_asks_for_25_codes_at_a_time(self):
        upcs = ["196589564931", *(f"0000000000{number:03d}" for number in range(29))]
        fake = self.apple((200, fixture("albums_by_upc_sos.json")), (200, {"data": []}))

        resources = apple_music.albums_by_upc(upcs)

        # A barcode returns its whole family: SOS gives the explicit and clean albums.
        self.assertEqual([resource["id"] for resource in resources], ["1657869377", "1658650093"])
        self.assertEqual(
            [query(request)["filter[upc]"].split(",") for request in fake.requests],
            [upcs[:25], upcs[25:]],
        )
        self.assertEqual(urlsplit(fake.requests[0].url).path, "/v1/catalog/us/albums")

    def test_songs_by_isrc_asks_for_25_codes_at_a_time(self):
        isrcs = ["USUM70727128", "USRC12204584", *(f"USXX1{number:07d}" for number in range(24))]
        fake = self.apple((200, fixture("songs_by_isrc.json")), (200, {"data": []}))

        songs = apple_music.songs_by_isrc(isrcs)

        self.assertEqual(urlsplit(fake.requests[0].url).path, "/v1/catalog/us/songs")
        self.assertEqual(
            [query(request)["filter[isrc]"].split(",") for request in fake.requests],
            [isrcs[:25], isrcs[25:]],
        )
        kill_bill_albums = {
            song["relationships"]["albums"]["data"][0]["id"]
            for song in songs
            if song["attributes"]["isrc"] == "USRC12204584"
        }
        self.assertTrue({"1657869377", "1658650093", "1794644675"} <= kill_bill_albums)

    def test_artist_returns_the_requested_views(self):
        views = ("full-albums", "singles", "live-albums", "compilation-albums", "appears-on-albums")
        fake = self.apple((200, fixture("artist_sza.json")))

        artist = apple_music.artist("605800394", views=views)

        self.assertEqual(urlsplit(fake.requests[0].url).path, "/v1/catalog/us/artists/605800394")
        self.assertEqual(query(fake.requests[0]), {"views": ",".join(views)})
        self.assertEqual(artist["attributes"]["name"], "SZA")
        self.assertEqual(artist["views"]["full-albums"]["attributes"]["title"], "Albums")
        self.assertIn(
            "1657869377",
            [album["id"] for album in artist["views"]["full-albums"]["data"]],
        )


class AppleMusicRateLimitTests(TestCase):
    def test_apple_sessions_have_their_own_redis_buckets(self):
        cases = (
            (services.apple_music_session, services.apple_music_bucket_key, 5),
            (
                services.apple_music_background_session,
                services.apple_music_background_bucket_key,
                2,
            ),
        )
        for session, bucket_key, per_second in cases:
            with self.subTest(bucket_key=bucket_key):
                factory = session.limiter.bucket_factory
                self.assertIs(factory.bucket_class, RedisBucket)
                self.assertEqual(factory.bucket_init_kwargs["bucket_key"], bucket_key)
                self.assertEqual(factory.rates[0].limit, per_second)
                self.assertEqual(factory.rates[0].interval, Duration.SECOND)
        self.assertEqual(
            len(
                {
                    services.bucket_key,
                    services.musicbrainz_bucket_key,
                    services.apple_music_bucket_key,
                    services.apple_music_background_bucket_key,
                },
            ),
            4,
        )


SOS_URL = "https://music.apple.com/us/album/sos/1657869377"
SOS_CREDIT = {
    "artist_mbid": None,
    "name": "SZA",
    "join_phrase": "",
    "artist_source": "apple_music",
    "artist_id": "605800394",
}


class AlbumPageTests(AppleMusicTestCase):
    def test_album_page_has_todays_music_shape(self):
        fake = self.apple((200, fixture("page_sos.json")))

        album = apple_music.music("1657869377")

        self.assertEqual(urlsplit(fake.requests[0].url).path, "/v1/catalog/us/albums/1657869377")
        self.assertEqual(
            query(fake.requests[0]),
            {"include": "tracks,artists", "views": "other-versions,related-albums"},
        )
        self.assertEqual(
            {key: album[key] for key in ("media_id", "source", "media_type", "title", "subtitle")},
            {
                "media_id": "1657869377",
                "source": "apple_music",
                "media_type": "music",
                "title": "SOS",
                "subtitle": "SZA",
            },
        )
        self.assertEqual(album["max_progress"], 1)
        self.assertEqual(album["release_date"], "2022-12-09")
        self.assertEqual(album["details"]["first_release_date"], "2022-12-09")
        self.assertEqual(album["details"]["primary_type"], "Album")
        self.assertIsNone(album["score"])
        self.assertIsNone(album["details"]["annotation"])
        self.assertEqual(album["external_links"], {"Apple Music": SOS_URL})
        self.assertEqual(album["related"], {})
        self.assertEqual(
            album["image"],
            "https://is1-ssl.mzstatic.com/image/thumb/Music122/v4/bd/3b/a9/"
            "bd3ba9fb-9609-144f-bcfe-ead67b5f6ab3/196589564931.jpg/600x600bb.jpg",
        )
        music = album["music"]
        self.assertEqual(music["release_group_mbid"], "1657869377")
        self.assertEqual(
            music["cover_art"],
            {"source": "apple_music", "release_group_mbid": "1657869377", "fallback_used": True},
        )
        self.assertEqual(music["content_rating"], "explicit")
        self.assertEqual(music["artist_credit"], [SOS_CREDIT])
        release = music["representative_release"]
        self.assertEqual(
            {key: value for key, value in release.items() if key not in {"media", "streaming_links"}},
            {
                "release_mbid": "1657869377",
                "title": "SOS",
                "status": "Official",
                "date": "2022-12-09",
                "country": "US",
                "barcode": "196589564931",
                "selection_basis": "apple_music",
                "labels": [
                    {
                        "label_mbid": None,
                        "name": "Top Dawg Entertainment/RCA Records",
                        "catalog_number": None,
                    },
                ],
                "format": "Digital Media",
                "is_deluxe_or_remastered": False,
                "disc_count": 1,
                "track_count": 23,
            },
        )

    def test_tracks_map_to_todays_track_shape(self):
        self.apple((200, fixture("page_sos.json")))

        medium = apple_music.music("1657869377")["music"]["representative_release"]["media"][0]

        self.assertEqual(
            {key: value for key, value in medium.items() if key != "tracks"},
            {
                "medium_mbid": None,
                "position": 1,
                "title": None,
                "format": "Digital Media",
                "track_count": 23,
            },
        )
        self.assertEqual(
            medium["tracks"][1],
            {
                "track_mbid": "1657869393",
                "disc_number": 1,
                "position": 2,
                "number": "2",
                "title": "Kill Bill",
                "length_ms": 153947,
                "artist_credit": [SOS_CREDIT],
                "content_rating": None,
                "recording": {
                    "recording_mbid": "1657869393",
                    "title": "Kill Bill",
                    "length_ms": 153947,
                    "disambiguation": None,
                    "first_release_date": "2022-12-09",
                    "is_video": False,
                    "isrcs": ["USRC12204584"],
                    "composer_name": "SZA, Rob Bisel & Carter Lang",
                    "source_url": "https://music.apple.com/us/album/kill-bill/1657869377?i=1657869393",
                },
            },
        )
        self.assertEqual(medium["tracks"][0]["content_rating"], "explicit")

    def test_a_song_on_the_album_twice_gets_a_unique_track_id(self):
        sos = fixture("page_sos.json")
        tracks = sos["data"][0]["relationships"]["tracks"]["data"]
        repeat = copy.deepcopy(tracks[0])
        repeat["attributes"]["trackNumber"] = 24
        tracks.append(repeat)
        self.apple((200, sos))

        tracks = apple_music.music("1657869377")["music"]["representative_release"]["media"][0]["tracks"]

        self.assertEqual([track["track_mbid"] for track in tracks].count("1657869385"), 1)
        self.assertEqual(tracks[-1]["track_mbid"], "1657869385-2")
        self.assertEqual(tracks[-1]["recording"]["recording_mbid"], "1657869385")

    def test_a_featured_song_credits_its_own_artists_as_text(self):
        sos = fixture("page_sos.json")
        sos["data"][0]["relationships"]["tracks"]["data"][0]["attributes"]["artistName"] = "SZA & Travis Scott"
        self.apple((200, sos))

        track = apple_music.music("1657869377")["music"]["representative_release"]["media"][0]["tracks"][0]

        self.assertEqual(
            track["artist_credit"],
            [{"artist_mbid": None, "name": "SZA & Travis Scott", "join_phrase": ""}],
        )

    def test_several_album_artists_join_as_a_list(self):
        sos = fixture("page_sos.json")
        sos["data"][0]["relationships"]["artists"]["data"] = [
            {"id": "1", "type": "artists", "attributes": {"name": "Metro Boomin"}},
            {"id": "2", "type": "artists", "attributes": {"name": "Future"}},
            {"id": "3", "type": "artists", "attributes": {"name": "Travis Scott"}},
        ]
        self.apple((200, sos))

        credits = apple_music.music("1657869377")["music"]["artist_credit"]

        self.assertEqual(
            [(credit["name"], credit["join_phrase"], credit["artist_id"]) for credit in credits],
            [("Metro Boomin", ", ", "1"), ("Future", " & ", "2"), ("Travis Scott", "", "3")],
        )
        self.assertTrue(all(credit["artist_mbid"] is None for credit in credits))

    def test_ep_and_single_suffixes_become_the_album_type(self):
        self.apple((200, fixture("page_blood_bank_ep.json")))

        album = apple_music.music("789755317")

        self.assertEqual(album["title"], "Blood Bank")
        self.assertEqual(album["music"]["primary_type"], "EP")
        self.assertEqual(album["details"]["primary_type"], "EP")
        self.assertEqual(album["music"]["representative_release"]["title"], "Blood Bank")
        cases = (
            ({"name": "Kill Bill - Single"}, ("Kill Bill", "Single")),
            ({"name": "Kill Bill", "isSingle": True}, ("Kill Bill", "Single")),
            ({"name": "SOS"}, ("SOS", "Album")),
            ({"name": "Blood Bank - ep "}, ("Blood Bank", "EP")),
        )
        for attributes, expected in cases:
            with self.subTest(attributes=attributes):
                self.assertEqual(apple_music.album_title(attributes), expected)

    def test_two_disc_deluxe_keeps_its_discs(self):
        self.apple((200, fixture("page_lana.json")))

        release = apple_music.music("1794644675")["music"]["representative_release"]

        self.assertEqual(release["disc_count"], 2)
        self.assertEqual(release["track_count"], 42)
        self.assertTrue(release["is_deluxe_or_remastered"])
        self.assertEqual([medium["position"] for medium in release["media"]], [1, 2])
        self.assertEqual([medium["track_count"] for medium in release["media"]], [19, 23])
        second_disc = release["media"][1]["tracks"]
        self.assertEqual([track["number"] for track in second_disc[:3]], ["1", "2", "3"])
        self.assertTrue(all(track["disc_number"] == 2 for track in second_disc))
        track_ids = [track["track_mbid"] for medium in release["media"] for track in medium["tracks"]]
        self.assertEqual(len(set(track_ids)), 42)

    def test_video_album_marks_its_videos(self):
        self.apple((200, fixture("page_multitude_video.json")))

        tracks = apple_music.music("1621698801")["music"]["representative_release"]["media"][0]["tracks"]

        self.assertEqual(
            [track["position"] for track in tracks if track["recording"]["is_video"]],
            [13, 14, 15],
        )

    def test_a_pre_order_gets_its_release_date(self):
        self.apple((200, fixture("page_weathered_preorder.json")))

        album = apple_music.music("6803287133")

        self.assertEqual(album["media_id"], "6803287133")
        self.assertEqual(album["release_date"], "2026-11-20")
        self.assertEqual(album["details"]["first_release_date"], "2026-11-20")
        self.assertEqual(album["max_progress"], 1)
        self.assertTrue(album["music"]["representative_release"]["is_deluxe_or_remastered"])

    def test_artwork_is_filled_at_600_and_never_longer_than_an_item_holds(self):
        artwork = {"url": "https://is1-ssl.mzstatic.com/image/thumb/a.jpg/{w}x{h}{c}.{f}"}
        self.assertEqual(
            apple_music.artwork_url(artwork),
            "https://is1-ssl.mzstatic.com/image/thumb/a.jpg/600x600bb.jpg",
        )
        self.assertIsNone(apple_music.artwork_url({"url": "https://example.com/" + "a" * 190 + "/{w}x{h}bb.jpg"}))
        self.assertIsNone(apple_music.artwork_url(None))

        too_long = {"url": "https://example.com/" + "a" * 190 + "/{w}x{h}bb.jpg"}
        self.apple((200, page("page_sos.json", artwork=too_long)))

        self.assertEqual(apple_music.music("1657869377")["image"], settings.IMG_NONE)

    def test_the_album_page_is_cached(self):
        fake = self.apple((200, fixture("page_sos.json")))

        first = apple_music.music("1657869377")
        second = apple_music.music("1657869377")

        self.assertEqual(first, second)
        self.assertEqual(len(fake.requests), 1)

    def test_a_404_without_an_item_raises(self):
        fake = self.apple((404, fixture("error_404.json")["body"]))

        with self.assertRaises(services.ProviderAPIError) as raised:
            apple_music.music("1")

        self.assertEqual(raised.exception.status_code, 404)
        self.assertEqual(len(fake.requests), 1)


class AlbumIdentityTests(AppleMusicTestCase):
    def test_a_re_upload_opens_as_its_listing_with_the_best_members_tracks(self):
        fake = self.apple((200, fixture("page_lana_38.json")), (200, fixture("page_lana.json")))

        album = apple_music.music("1786643044")

        self.assertEqual(album["media_id"], "1794644675")
        self.assertEqual(album["music"]["release_group_mbid"], "1794644675")
        self.assertEqual(album["music"]["representative_release"]["track_count"], 42)
        self.assertEqual(
            album["external_links"],
            {"Apple Music": "https://music.apple.com/us/album/sos-deluxe-lana/1794644675"},
        )
        self.assertEqual(
            [urlsplit(request.url).path.rsplit("/", 1)[-1] for request in fake.requests],
            ["1786643044", "1794644675"],
        )
        rows = dict(AppleAlbum.objects.values_list("apple_album_id", "canonical_id"))
        self.assertEqual(
            rows,
            {"1786643044": "1794644675", "1794644675": "1794644675", "1796270264": "1794644675"},
        )
        self.assertEqual(AppleAlbum.objects.get(apple_album_id="1786643044").upc, "196871766890")

    def test_an_item_keeps_its_identity_and_still_shows_the_best_tracks(self):
        apple_item("1786643044", title="SOS Deluxe: LANA")
        self.apple((200, fixture("page_lana_38.json")), (200, fixture("page_lana.json")))

        album = apple_music.music("1786643044")

        self.assertEqual(album["media_id"], "1786643044")
        self.assertEqual(album["music"]["representative_release"]["track_count"], 42)
        self.assertEqual(
            set(AppleAlbum.objects.values_list("canonical_id", flat=True)),
            {"1786643044"},
        )

    def test_the_best_member_falls_back_to_the_album_asked_for(self):
        self.apple((200, fixture("page_lana_38.json")), (503, {"errors": []}))

        album = apple_music.music("1786643044")

        self.assertEqual(album["media_id"], "1794644675")
        self.assertEqual(album["music"]["representative_release"]["track_count"], 38)
        # Apple failed for a moment, so the music caches don't keep this page.
        self.assertTrue(album[services.PARTIAL_PAGE])

    def test_a_best_member_apple_no_longer_has_isnt_a_failure_to_wait_out(self):
        self.apple((200, fixture("page_lana_38.json")), (404, fixture("error_404.json")["body"]))

        album = apple_music.music("1786643044")

        self.assertEqual(album["music"]["representative_release"]["track_count"], 38)
        self.assertNotIn(services.PARTIAL_PAGE, album)

    def test_every_family_member_gets_a_row_and_keeps_a_known_barcode(self):
        AppleAlbum.objects.create(apple_album_id="1658650093", canonical_id="1658650093", upc="196589564955")
        sos = fixture("page_sos.json")
        del sos["data"][0]["views"]["other-versions"]["data"][0]["attributes"]["upc"]
        self.apple((200, sos))

        apple_music.music("1657869377")

        self.assertEqual(
            {row.apple_album_id: (row.canonical_id, row.upc) for row in AppleAlbum.objects.all()},
            {
                "1657869377": ("1657869377", "196589564931"),
                "1658650093": ("1657869377", "196589564955"),
            },
        )

    def test_a_taken_down_album_with_an_item_opens_its_successor_by_barcode(self):
        apple_item("1111111111", title="SOS")
        AppleAlbum.objects.create(apple_album_id="1111111111", canonical_id="1111111111", upc="196589564931")
        fake = self.apple(
            (404, fixture("error_404.json")["body"]),
            (200, fixture("albums_by_upc_sos.json")),
            (200, fixture("page_sos.json")),
        )

        album = apple_music.music("1111111111")

        self.assertEqual(album["media_id"], "1111111111")
        self.assertEqual(album["music"]["representative_release"]["track_count"], 23)
        self.assertEqual(query(fake.requests[1]), {"filter[upc]": "196589564931"})
        self.assertEqual(urlsplit(fake.requests[2].url).path, "/v1/catalog/us/albums/1657869377")
        self.assertEqual(
            dict(AppleAlbum.objects.values_list("apple_album_id", "canonical_id")),
            {"1111111111": "1111111111", "1657869377": "1111111111", "1658650093": "1111111111"},
        )

    def test_no_successor_without_a_matching_title(self):
        apple_item("1111111111", title="Ctrl")
        AppleAlbum.objects.create(apple_album_id="1111111111", canonical_id="1111111111", upc="196589564931")
        self.apple((404, fixture("error_404.json")["body"]), (200, fixture("albums_by_upc_sos.json")))

        with self.assertRaises(services.ProviderAPIError) as raised:
            apple_music.music("1111111111")

        self.assertEqual(raised.exception.status_code, 404)


class AlbumLinkTests(AppleMusicTestCase):
    def setUp(self):
        super().setUp()
        self.apple((200, fixture("page_sos.json")))

    def links(self):
        return apple_music.music("1657869377")["music"]["representative_release"]["streaming_links"]

    def test_apple_music_first_then_a_spotify_search(self):
        self.assertEqual(
            self.links(),
            [
                {"service": "music.apple.com", "url": SOS_URL, "kind": "album"},
                {
                    "service": "open.spotify.com",
                    "url": "https://open.spotify.com/search/SZA%20SOS/albums",
                    "kind": "search",
                },
            ],
        )

    def test_the_exact_spotify_album_when_the_bridge_or_a_link_knows_it(self):
        spotify = "https://open.spotify.com/album/07w0rG5TETcyihsEIZR3qG"
        link = MusicAlbumLink.objects.create(
            release_group_mbid="b5a2f2f3-1111-4b5b-9ad1-07d3c3a5f8e4",
            apple_album_id="1657869377",
            status=MusicAlbumLink.Status.REVIEW,
            spotify_url=spotify,
        )
        self.assertEqual(self.links()[1]["kind"], "search")

        link.status = MusicAlbumLink.Status.APPLIED
        link.save()
        self.assertEqual(
            self.links()[1],
            {"service": "open.spotify.com", "url": spotify, "kind": "album"},
        )

        link.delete()
        AppleAlbum.objects.filter(apple_album_id="1657869377").update(spotify_url=spotify, spotify_exact=True)
        self.assertEqual(self.links()[1], {"service": "open.spotify.com", "url": spotify, "kind": "album"})


class AlbumGenreAndDateTests(AppleMusicTestCase):
    def bridge(self, **fields):
        AppleAlbum.objects.update_or_create(
            apple_album_id="1657869377",
            defaults={"canonical_id": "1657869377", "release_group_mbid": "rg", **fields},
        )

    def test_apple_labels_without_the_music_catch_all(self):
        self.apple((200, fixture("page_sos.json")), (200, fixture("page_whos_next_deluxe.json")))

        self.assertEqual(apple_music.music("1657869377")["genres"], ["R&B/Soul"])
        self.assertEqual(
            apple_music.music("1440850317")["genres"],
            ["Hard Rock", "Rock", "Arena Rock", "Rock & Roll"],
        )

    def test_apples_genres_win_over_old_item_and_bridge_genres(self):
        self.apple((200, fixture("page_sos.json")))
        self.assertEqual(apple_music.music("1657869377")["genres"], ["R&B/Soul"])

        item = apple_item("1657869377")
        genre_facets(item, "contemporary r&b", "alternative r&b")
        self.assertEqual(apple_music.music("1657869377")["genres"], ["R&B/Soul"])

        # A MusicBrainz bridge must not replace Apple labels.
        self.bridge(genres=["contemporary r&b"])
        self.assertEqual(apple_music.music("1657869377")["genres"], ["R&B/Soul"])

        self.bridge(genres=["r&b", "soul"])
        self.assertEqual(apple_music.music("1657869377")["genres"], ["R&B/Soul"])

    def test_an_album_keeps_apples_genres_after_its_bridge_arrives(self):
        self.apple((200, fixture("page_whos_next_deluxe.json")))
        item = apple_item("1440850317", title="Who's Next (Deluxe Edition)")
        album = apple_music.music("1440850317")
        self.assertEqual(album["genres"], ["Hard Rock", "Rock", "Arena Rock", "Rock & Roll"])
        genre_facets(item, *album["genres"])

        AppleAlbum.objects.filter(apple_album_id="1440850317").update(release_group_mbid="rg", genres=["hard rock", "rock"])

        self.assertEqual(apple_music.music("1440850317")["genres"], ["Hard Rock", "Rock", "Arena Rock", "Rock & Roll"])
        bridge = AppleAlbum.objects.get(apple_album_id="1440850317")
        self.assertEqual(
            apple_music.filter_metadata("1440850317", {}, bridge)["genres"],
            ["Arena Rock", "Hard Rock", "Rock", "Rock & Roll"],
        )

    def test_the_release_date_is_musicbrainzs_when_surely_earlier_for_the_same_title(self):
        self.apple((200, fixture("page_sos.json")))
        cases = (
            ({"same_title": True, "first_release_date": "2022-12-08"}, "2022-12-08"),
            ({"same_title": False, "first_release_date": "2022-12-08"}, "2022-12-09"),
            ({"same_title": True, "first_release_date": "2022"}, "2022-12-09"),
            ({"same_title": True, "first_release_date": "2021-05"}, "2021-05"),
            ({"same_title": True, "first_release_date": "2023-01-01"}, "2022-12-09"),
            ({"same_title": True, "first_release_date": ""}, "2022-12-09"),
        )
        for fields, expected in cases:
            with self.subTest(fields=fields):
                self.bridge(**fields)
                album = apple_music.music("1657869377")
                self.assertEqual(album["release_date"], expected)
                self.assertEqual(album["details"]["first_release_date"], expected)

    def test_a_partial_apple_date_gives_way_to_an_earlier_musicbrainz_date(self):
        self.apple((200, page("page_sos.json", releaseDate="2022")))
        self.bridge(same_title=True, first_release_date="2021-12-31")

        self.assertEqual(apple_music.music("1657869377")["release_date"], "2021-12-31")


class SongPageTests(AppleMusicTestCase):
    def test_a_song_comes_from_its_albums_payload(self):
        self.apple((200, fixture("page_sos.json")))
        album = apple_music.music("1657869377")

        song = apple_music.recording(album, "1657869393")

        track = album["music"]["representative_release"]["media"][0]["tracks"][1]
        self.assertEqual(song["recording_mbid"], "1657869393")
        self.assertEqual(song["title"], "Kill Bill")
        self.assertFalse(song["is_video"])
        self.assertEqual(song["isrcs"], ["USRC12204584"])
        self.assertEqual(song["artist_credit"], [SOS_CREDIT])
        self.assertEqual(
            song["credits"],
            [{"artist_mbid": None, "name": "SZA, Rob Bisel & Carter Lang", "roles": ["composer"]}],
        )
        self.assertEqual((song["works"], song["alternative_recordings"]), ([], []))
        self.assertEqual(
            song["context_release"],
            {"release_mbid": "1657869377", "medium_mbid": None, "track": track},
        )
        self.assertEqual(
            [(release["release_mbid"], release["release_group_mbid"]) for release in song["releases"]],
            [("1657869377", "1657869377")],
        )
        self.assertEqual(
            song["external_links"],
            {"Apple Music": "https://music.apple.com/us/album/kill-bill/1657869377?i=1657869393"},
        )
        self.assertEqual(song["genres"], ["R&B/Soul"])

    def test_a_song_not_on_the_album_is_none(self):
        self.apple((200, fixture("page_sos.json")))
        album = apple_music.music("1657869377")

        self.assertIsNone(apple_music.recording(album, "999"))
        self.assertIsNone(apple_music.recording({"music": None}, "1657869393"))


class AlbumSourceTests(AppleMusicTestCase):
    def test_apple_refs_go_to_apple_and_others_to_musicbrainz(self):
        self.apple((200, fixture("page_sos.json")))

        album = services.get_media_metadata("music", "1657869377", "apple_music")

        self.assertEqual(album["source"], "apple_music")
        with patch("app.providers.services.musicbrainz.music", return_value={"source": "musicbrainz"}) as music:
            services.get_media_metadata("music", "3bd76d40-7f0e-36b7-9348-91a33afee20e", "musicbrainz")
            services.get_media_metadata(
                "music",
                "3bd76d40-7f0e-36b7-9348-91a33afee20e",
                "musicbrainz",
                include_music_enrichment=False,
            )
        self.assertEqual(
            [call.kwargs for call in music.call_args_list],
            [{}, {"include_enrichment": False}],
        )
