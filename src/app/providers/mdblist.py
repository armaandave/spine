import logging

import requests
from django.conf import settings
from django.core.cache import cache

from app.providers import services

logger = logging.getLogger(__name__)
base_url = "https://api.mdblist.com"


def handle_error(error):
    """Handle MDBList API errors."""
    error_resp = error.response
    status_code = error_resp.status_code

    try:
        error_json = error_resp.json()
    except requests.exceptions.JSONDecodeError:
        # No traceback: the HTTPError being handled names the URL, apikey included.
        logger.warning("MDBList returned a non-JSON error body (HTTP %s)", status_code)
        raise services.ProviderAPIError("mdblist", error) from None

    # Handle authentication errors
    if status_code == requests.codes.unauthorized:
        details = error_json.get("message", "Invalid API key")
        raise services.ProviderAPIError("mdblist", error, details)

    raise services.ProviderAPIError("mdblist", error)


def get_media_ratings(tmdb_id, media_type):
    """Get ratings for a media item from MDBList using TMDB ID."""
    cache_key = f"mdblist_ratings_tmdb_{media_type}_{tmdb_id}"
    data = cache.get(cache_key)

    if data is None:
        # MDBList API expects 'show' for TV shows, not 'tv'
        mdblist_media_type = "show" if media_type == "tv" else media_type
        url = f"{base_url}/tmdb/{mdblist_media_type}/{tmdb_id}"
        params = {
            "apikey": settings.MDBLIST_API,
            "append_to_response": "keyword",
        }

        try:
            response = services.api_request(
                "mdblist",
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)
            return None

        # Extract ratings data
        ratings_data = {}
        if "ratings" in response:
            for rating in response["ratings"]:
                source = rating.get("source")
                if source in ["imdb", "tomatoes", "letterboxd"]:
                    ratings_data[source] = {
                        "value": rating.get("value"),
                        "score": rating.get("score"),
                        "votes": rating.get("votes"),
                        "url": rating.get("url"),
                    }

        data = ratings_data
        cache.set(cache_key, data, timeout=86400)  # Cache for 24 hours

    return data


def _get(path, params=None):
    try:
        return services.api_request(
            "mdblist",
            "GET",
            f"{base_url}{path}",
            params={"apikey": settings.MDBLIST_API, **(params or {})},
        )
    except requests.exceptions.HTTPError as error:
        handle_error(error)


def _first(data):
    return data[0] if isinstance(data, list) and data else data


def get_external_list(list_id):
    """Return one MDBList external-list record."""
    return _first(_get(f"/external/lists/{list_id}", {"append_to_response": "poster"}))


def get_user_external_lists():
    """Return the external lists (Letterboxd, Trakt mirrors) owned by the API key's account."""
    data = _get("/external/lists/user")
    return data if isinstance(data, list) else []


def get_list(list_id):
    """Return one public MDBList user list record."""
    return _first(_get(f"/lists/{list_id}"))


def _list_items(path, extra_params=None):
    """Follow MDBList's cursor pagination and tag each item with its Spine media type."""
    items = []
    cursor = None
    while True:
        params = {
            "append_to_response": "poster",
            "limit": 1000,
            **(extra_params or {}),
        }
        if cursor:
            params["cursor"] = cursor
        page = _get(path, params)

        for media_type, values in (("movie", page.get("movies")), ("tv", page.get("shows"))):
            items.extend({**item, "spine_media_type": media_type} for item in values or [])

        cursor = page.get("next_cursor") or (page.get("pagination") or {}).get("next_cursor")
        if not cursor:
            return items


def get_external_list_items(list_id):
    """Return every item in one MDBList external list."""
    return _list_items(f"/external/lists/{list_id}/items")


def get_list_items(list_id):
    """Return every item in one public MDBList user list (ranks as MDBList stores them)."""
    return _list_items(f"/lists/{list_id}/items")


def get_official_list_items(slug, mediatype):
    """Return every item in an MDBList official list for ``movie`` or ``show``."""
    return _list_items(f"/lists/official/{slug}/items", {"mediatype": mediatype})
