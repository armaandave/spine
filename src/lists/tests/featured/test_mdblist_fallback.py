from django.test import SimpleTestCase, override_settings

from app.providers import services
from lists.featured.adapters import ADAPTERS, mdblist
from lists.featured.registry import get
from lists.featured.types import AdapterError
from lists.tests.featured.helpers import api_routes, load_fixture, make_definition


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
