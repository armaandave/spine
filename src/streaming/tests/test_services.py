from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase, override_settings

from streaming import services
from streaming.models import UserStreamingService
from streaming.tests.helpers import HULU, NETFLIX, make_provider, reset_streaming_state


class RegionResolutionTests(TestCase):
    """ST-100 region resolution: saved country, header, then default."""

    def setUp(self):
        reset_streaming_state(self)
        self.user = get_user_model().objects.create_user(username="regions", password="pw-12345678")
        self.factory = RequestFactory()

    def _request(self, region=None):
        headers = {"HTTP_X_SPINE_REGION": region} if region is not None else {}
        return self.factory.get("/", **headers)

    def _save(self, value):
        self.user.watch_provider_region = value
        self.user.save(update_fields=["watch_provider_region"])

    def test_saved_country_wins_over_header(self):
        self._save("GB")

        self.assertEqual(services.effective_region(self.user, self._request("FR")), "GB")
        self.assertTrue(services.region_is_set(self.user))
        self.assertTrue(services.availability_enabled(self.user))

    def test_unset_uses_valid_header_then_default(self):
        self.assertEqual(services.effective_region(self.user, self._request("fr")), "FR")
        self.assertEqual(services.effective_region(self.user, self._request("France")), "US")
        self.assertEqual(services.effective_region(self.user, self._request()), "US")
        self.assertEqual(services.effective_region(self.user), "US")
        self.assertFalse(services.region_is_set(self.user))

    @override_settings(STREAMING_DEFAULT_REGION="ca")
    def test_default_region_setting_is_normalized(self):
        self.assertEqual(services.effective_region(self.user), "CA")

    def test_disabled_region_turns_availability_off(self):
        self._save("")

        self.assertIsNone(services.effective_region(self.user, self._request("FR")))
        self.assertFalse(services.availability_enabled(self.user))
        self.assertFalse(services.region_is_set(self.user))

    def test_anonymous_users_use_header_or_default(self):
        self.assertEqual(services.effective_region(AnonymousUser(), self._request("de")), "DE")
        self.assertEqual(services.effective_region(None, self._request()), "US")
        self.assertTrue(services.availability_enabled(None))
        self.assertEqual(services.user_provider_ids(AnonymousUser()), [])


class StreamingHelperTests(TestCase):
    """Title identity, logos, region names, services, and active regions."""

    def setUp(self):
        reset_streaming_state(self)
        self.user = get_user_model().objects.create_user(username="helpers", password="pw-12345678")

    def test_title_key_maps_shows_seasons_and_episodes_to_tv(self):
        self.assertEqual(services.title_key("tmdb", "movie", 603), ("movie", "603"))
        self.assertEqual(services.title_key("tmdb", "tv", "1396"), ("tv", "1396"))
        self.assertEqual(services.title_key("tmdb", "season", "1396"), ("tv", "1396"))
        self.assertEqual(services.title_key("tmdb", "episode", "1396"), ("tv", "1396"))
        self.assertIsNone(services.title_key("tmdb", "book", "1"))
        self.assertIsNone(services.title_key("mal", "anime", "1"))
        self.assertIsNone(services.title_key("tmdb", "movie", ""))

    def test_logo_url(self):
        self.assertEqual(services.logo_url("/abc.png"), "https://image.tmdb.org/t/p/w154/abc.png")
        self.assertIsNone(services.logo_url(""))

    def test_user_provider_ids_follow_display_priority(self):
        make_provider(HULU)
        make_provider(NETFLIX)
        UserStreamingService.objects.create(user=self.user, provider_id=HULU["provider_id"])
        UserStreamingService.objects.create(user=self.user, provider_id=NETFLIX["provider_id"])

        self.assertEqual(services.user_provider_ids(self.user), [8, 15])

    def test_active_regions_cover_saved_countries_of_users_with_services(self):
        make_provider(NETFLIX)
        with_services = get_user_model().objects.create_user(username="gb", password="pw-12345678")
        with_services.watch_provider_region = "GB"
        with_services.save(update_fields=["watch_provider_region"])
        UserStreamingService.objects.create(user=with_services, provider_id=8)
        without_services = get_user_model().objects.create_user(username="de", password="pw-12345678")
        without_services.watch_provider_region = "DE"
        without_services.save(update_fields=["watch_provider_region"])
        disabled = get_user_model().objects.create_user(username="off", password="pw-12345678")
        disabled.watch_provider_region = ""
        disabled.save(update_fields=["watch_provider_region"])
        UserStreamingService.objects.create(user=disabled, provider_id=8)

        self.assertEqual(services.active_regions(), {"GB", "US"})

        self.user.watch_provider_region = "CA"
        self.user.save(update_fields=["watch_provider_region"])
        UserStreamingService.objects.create(user=self.user, provider_id=8)
        self.assertEqual(services.active_regions(), {"GB", "US"})
        services.invalidate_active_regions()
        self.assertEqual(services.active_regions(), {"CA", "GB", "US"})

    def test_region_name_without_fetch_never_calls_tmdb(self):
        reset_streaming_state(self, region_names=None)

        with patch("app.providers.tmdb.watch_provider_regions") as regions_mock:
            self.assertEqual(services.region_name("XX", fetch=False), "XX")
            self.assertEqual(services.region_name("US", fetch=False), "United States")
        regions_mock.assert_not_called()

        reset_streaming_state(self)
        self.assertEqual(services.region_name("GB", fetch=False), "United Kingdom")
        self.assertIsNone(services.region_name(None))

    def test_region_names_fetch_once_and_cache(self):
        reset_streaming_state(self, region_names=None)

        with patch(
            "app.providers.tmdb.watch_provider_regions",
            return_value=[("", "Disabled"), ("ZZ", "Zedland")],
        ) as regions_mock:
            self.assertEqual(services.region_names(), {"ZZ": "Zedland"})
            self.assertEqual(services.region_name("ZZ"), "Zedland")
            services._region_names.update(expires=0.0, names={})
            self.assertEqual(services.cached_region_names(), {"ZZ": "Zedland"})
            self.assertEqual(services.region_name("XX"), "XX")
        regions_mock.assert_called_once()

    def test_region_name_falls_back_to_code_on_provider_errors(self):
        reset_streaming_state(self, region_names=None)

        with patch("app.providers.tmdb.watch_provider_regions", side_effect=RuntimeError("down")):
            self.assertEqual(services.region_name("XX"), "XX")
