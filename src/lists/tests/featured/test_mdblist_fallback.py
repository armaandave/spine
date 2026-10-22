import requests
from django.test import SimpleTestCase, override_settings

from app.providers import mdblist as provider_mdblist
from app.providers import services
from lists.featured import engine, registry
from lists.featured.adapters import ADAPTERS, mdblist
from lists.featured.registry import get
from lists.featured.types import AdapterError
from lists.models import FeaturedListSync
from lists.tests.featured.helpers import FeaturedTestCase, api_routes, load_fixture, make_definition


def public_list(list_id):
    return {"id": int(list_id), "name": f"List {list_id}", "items": 3}


@override_settings(MDBLIST_API="test-mdblist-key")
class MDBListFallbackTests(SimpleTestCase):
    def test_falls_back_when_the_primary_list_fails(self):
        routes = {
            "/lists/101881": services.ProviderAPIError("mdblist", None, "Not found"),
            "/lists/12306/items": load_fixture("mdblist", "public_list_items.json"),
            "/lists/12306": public_list("12306"),
        }
        with api_routes(routes):
            result = ADAPTERS["mdblist"].fetch(get("imdb-top-250-movies"))
        self.assertEqual((result.name, len(result.entries), result.expected_count), ("List 12306", 3, 3))

    def test_falls_back_when_the_primary_list_is_empty(self):
        routes = {
            "/lists/101881/items": {"movies": [], "shows": []},
            "/lists/101881": public_list("101881"),
            "/lists/12306/items": load_fixture("mdblist", "public_list_items.json"),
            "/lists/12306": public_list("12306"),
        }
        with api_routes(routes):
            result = ADAPTERS["mdblist"].fetch(get("imdb-top-250-movies"))
        self.assertEqual(result.name, "List 12306")

    def test_every_list_failing_names_each(self):
        routes = {"/lists/": services.ProviderAPIError("mdblist", None, "Not found")}
        with api_routes(routes), self.assertRaisesMessage(AdapterError, "list 101881") as raised:
            ADAPTERS["mdblist"].fetch(get("imdb-top-250-movies"))
        self.assertIn("list 12306", str(raised.exception))

    def test_without_fallbacks_an_empty_list_reaches_the_guards(self):
        definition = make_definition(adapter="mdblist", params={"list_id": "5"})
        with api_routes({"/lists/5/items": {"movies": []}, "/lists/5": public_list("5")}):
            self.assertEqual(ADAPTERS["mdblist"].fetch(definition).entries, [])

    def test_missing_external_list_says_how_to_add_it(self):
        url = "https://letterboxd.com/official/list/top-250-horror-films/"
        with api_routes({"/external/lists/user": load_fixture("mdblist", "external_lists_user.json")}):
            with self.assertRaisesMessage(AdapterError, f"Add {url} as an MDBList external list"):
                ADAPTERS["mdblist"].fetch(get("letterboxd-top-250-horror"))

    def test_validate_fallback_ids(self):
        for params in ({"list_id": "1", "fallback_ids": "2"}, {"list_id": "1", "fallback_ids": ["x"]}, {"official": "moviemeter", "mediatype": "movie", "fallback_ids": ["2"]}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                mdblist.validate(make_definition(adapter="mdblist", params=params))


KEYED_URL_ERROR = "HTTPSConnectionPool(host='api.mdblist.com', port=443): Max retries exceeded with url: /lists/101881/items?apikey=test-mdblist-key"


@override_settings(MDBLIST_API="test-mdblist-key")
class MDBListNetworkErrorTests(FeaturedTestCase):
    def test_a_connection_error_falls_back(self):
        routes = {
            "/lists/101881": requests.ConnectionError(KEYED_URL_ERROR),
            "/lists/12306/items": load_fixture("mdblist", "public_list_items.json"),
            "/lists/12306": public_list("12306"),
        }
        with api_routes(routes):
            result = ADAPTERS["mdblist"].fetch(get("imdb-top-250-movies"))
        self.assertEqual(result.name, "List 12306")

    def test_network_errors_keep_the_key_out_of_logs_and_errors(self):
        definition = make_definition(adapter="mdblist", params={"list_id": "101881"})
        with (
            api_routes({"/lists/": requests.ConnectTimeout(KEYED_URL_ERROR)}),
            registry.override_definitions([definition]),
            self.assertLogs(level="DEBUG") as logs,
        ):
            result = engine.run(definition, self.owner)
        self.assertEqual(result.status, "failed")
        self.assertIn("network error (ConnectTimeout)", result.message)
        self.assertNotIn("test-mdblist-key", result.message)
        self.assertNotIn("test-mdblist-key", FeaturedListSync.objects.get().last_error)
        self.assertNotIn("test-mdblist-key", "\n".join(logs.output))
        self.assertTrue(all(record.exc_info is None for record in logs.records))

    def test_external_and_official_modes_wrap_network_errors(self):
        for definition in (get("letterboxd-top-500"), make_definition(adapter="mdblist", params={"official": "moviemeter", "mediatype": "movie"})):
            with self.subTest(key=definition.key), api_routes({"api.mdblist.com": requests.ConnectionError(KEYED_URL_ERROR)}):
                with self.assertRaises(AdapterError) as raised:
                    ADAPTERS["mdblist"].fetch(definition)
                self.assertNotIn("test-mdblist-key", str(raised.exception))


@override_settings(MDBLIST_API="test-mdblist-key")
class MDBListProviderErrorTests(SimpleTestCase):
    def test_a_non_json_error_body_is_logged_without_the_keyed_url(self):
        response = requests.Response()
        response.status_code = 502
        response._content = b"<html>Bad gateway</html>"
        error = requests.HTTPError("502 Server Error for url: https://api.mdblist.com/lists/1?apikey=test-mdblist-key", response=response)
        with self.assertLogs(level="DEBUG") as logs, self.assertRaises(services.ProviderAPIError):
            try:
                raise error
            except requests.HTTPError as handled:
                provider_mdblist.handle_error(handled)
        self.assertNotIn("test-mdblist-key", "\n".join(logs.output))
        self.assertTrue(all(record.exc_info is None for record in logs.records))
