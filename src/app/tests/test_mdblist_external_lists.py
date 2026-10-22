from unittest.mock import call, patch

from django.test import SimpleTestCase, override_settings

from app.providers import mdblist, services


@override_settings(MDBLIST_API="test-key")
class MDBListExternalListsTests(SimpleTestCase):
    """MDBList external-list pagination contract."""

    @patch("app.providers.mdblist.services.api_request")
    def test_items_follow_cursor_and_preserve_media_types(self, api_request):
        api_request.side_effect = [
            {
                "movies": [{"id": 1, "title": "Movie"}],
                "shows": [],
                "pagination": {"next_cursor": "next-page"},
            },
            {
                "movies": [],
                "shows": [{"id": 2, "title": "Show"}],
            },
        ]

        items = mdblist.get_external_list_items(155837)

        self.assertEqual(
            [(item["id"], item["spine_media_type"]) for item in items],
            [(1, "movie"), (2, "tv")],
        )
        self.assertEqual(
            api_request.call_args_list,
            [
                call(
                    "mdblist",
                    "GET",
                    "https://api.mdblist.com/external/lists/155837/items",
                    params={
                        "apikey": "test-key",
                        "append_to_response": "poster",
                        "limit": 1000,
                    },
                ),
                call(
                    "mdblist",
                    "GET",
                    "https://api.mdblist.com/external/lists/155837/items",
                    params={
                        "apikey": "test-key",
                        "append_to_response": "poster",
                        "limit": 1000,
                        "cursor": "next-page",
                    },
                ),
            ],
        )

    @patch("app.providers.mdblist.services.api_request")
    def test_a_repeated_cursor_stops_pagination(self, api_request):
        api_request.return_value = {"movies": [{"id": 1}], "next_cursor": "same"}

        with self.assertRaisesMessage(services.ProviderAPIError, "repeated a page cursor"):
            mdblist.get_list_items(1)
        self.assertEqual(api_request.call_count, 2)

    @patch("app.providers.mdblist.services.api_request")
    def test_pagination_is_capped(self, api_request):
        api_request.side_effect = lambda *args, params, **kwargs: {
            "movies": [{"id": 1}],
            "next_cursor": f"page-{params.get('cursor', '0')}-next",
        }

        with self.assertRaisesMessage(services.ProviderAPIError, "more than 50 pages"):
            mdblist.get_list_items(1)
        self.assertEqual(api_request.call_count, mdblist.MAX_LIST_PAGES)

    @patch("app.providers.mdblist.services.api_request")
    def test_metadata_unwraps_live_singleton_shape(self, api_request):
        api_request.return_value = [{"id": 155837, "name": "Featured"}]

        self.assertEqual(mdblist.get_external_list(155837)["name"], "Featured")
