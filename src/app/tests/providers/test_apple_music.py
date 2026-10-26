import base64
import copy
import json
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

from app.providers import apple_music, services

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
