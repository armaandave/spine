import logging
import math
import re

import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.cache import cache

from app import helpers
from app.models import MediaTypes, Sources
from app.providers import services
from app.providers.search_rank import normalize_search_text, rank_results

logger = logging.getLogger(__name__)
base_url = "https://comicvine.gamespot.com/api"
headers = {
    "User-Agent": "Mozilla/5.0",
}

COMIC_SEARCH_CACHE_VERSION = "v3"
# Comic Vine's search endpoint gives every volume this stand-in cover.
PLACEHOLDER_IMAGE_SUFFIX = "-blank.png"
LOCALIZED_EDITION_PENALTY = 50
LOCALIZED_RELATED_EDITION_PENALTY = 20
LOCALIZED_EDITION_WORDS = (
    "arabic",
    "brazilian",
    "bulgarian",
    "chinese",
    "croatian",
    "czech",
    "danish",
    "dutch",
    "finnish",
    "french",
    "german",
    "greek",
    "hebrew",
    "hungarian",
    "indonesian",
    "italian",
    "japanese",
    "korean",
    "mexican",
    "norwegian",
    "polish",
    "portuguese",
    "romanian",
    "russian",
    "serbian",
    "slovak",
    "slovenian",
    "spanish",
    "swedish",
    "thai",
    "turkish",
    "ukrainian",
    "vietnamese",
)
LOCALIZED_PUBLISHER_MARKERS = {
    "abril",
    "brasil",
    "ediciones",
    "editions",
    "editora",
    "editorial",
    "forlag",
    "verlag",
}
_localized_words = "|".join(LOCALIZED_EDITION_WORDS)
LOCALIZED_EDITION_PATTERN = re.compile(
    rf"\b(?:{_localized_words})\b(?:\s+\w+){{0,3}}\s+"
    r"\b(?:edition|language|publication|translated|translation)\b"
    rf"|\b(?:edition|language|publication|translated|translation)\b"
    rf"(?:\s+\w+){{0,3}}\s+\b(?:{_localized_words})\b",
)


def handle_error(error):
    """Handle ComicVine API errors."""
    error_resp = error.response
    status_code = error_resp.status_code

    try:
        error_json = error_resp.json()
    except requests.exceptions.JSONDecodeError as json_error:
        logger.exception("Failed to decode JSON response")
        raise services.ProviderAPIError(Sources.COMICVINE.value, error) from json_error

    # Handle invalid API key
    if status_code == requests.codes.unauthorized:
        details = error_json["error"]
        raise services.ProviderAPIError(Sources.COMICVINE.value, error, details)

    raise services.ProviderAPIError(Sources.COMICVINE.value, error)


def search(query, page, *, preserve_ranking_fields=False, timeout=None):
    """Search for comics on Comic Vine."""
    rank_suffix = "_rank" if preserve_ranking_fields else ""
    cache_key = (
        f"search_{COMIC_SEARCH_CACHE_VERSION}_{Sources.COMICVINE.value}_"
        f"{MediaTypes.COMIC.value}_{query}_{page}{rank_suffix}"
    )
    data = cache.get(cache_key)

    if data is None:
        params = {
            "api_key": settings.COMICVINE_API,
            "format": "json",
            "query": query,
            "resources": "volume",
            "field_list": (
                "id,name,image,deck,publisher,start_year,count_of_issues"
            ),
            "limit": settings.PER_PAGE,
            "page": page,
        }

        try:
            response = services.api_request(
                Sources.COMICVINE.value,
                "GET",
                f"{base_url}/search/",
                params=params,
                headers=headers,
                timeout=timeout,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        _fill_placeholder_covers(response["results"], timeout=timeout)
        results = build_search_results(response["results"], query=query)
        results = rank_results(
            query,
            results,
            MediaTypes.COMIC.value,
            preserve_ranking_fields=preserve_ranking_fields,
        )

        total_results = response["number_of_total_results"]
        data = helpers.format_search_response(
            page,
            settings.PER_PAGE,
            total_results,
            results,
        )

        cache.set(cache_key, data)

    return data


def _fill_placeholder_covers(items, timeout=None):
    """Swap search's stand-in covers for the real ones from the volumes list."""
    ids = [
        str(item["id"])
        for item in items
        if get_image(item).endswith(PLACEHOLDER_IMAGE_SUFFIX)
    ]
    if not ids:
        return

    params = {
        "api_key": settings.COMICVINE_API,
        "format": "json",
        "filter": f"id:{'|'.join(ids)}",
        "field_list": "id,image",
        "limit": len(ids),
    }
    try:
        response = services.api_request(
            Sources.COMICVINE.value,
            "GET",
            f"{base_url}/volumes/",
            params=params,
            headers=headers,
            timeout=timeout,
        )
    except requests.exceptions.RequestException:
        # Placeholder covers beat failing the whole search.
        logger.warning("Failed to fetch Comic Vine covers", exc_info=True)
        return

    images = {volume["id"]: volume.get("image") for volume in response["results"]}
    for item in items:
        if images.get(item["id"]):
            item["image"] = images[item["id"]]


def build_search_results(items, query=None):
    """Build rankable comic search results without dropping distinct volumes."""
    localized_flags = [_is_likely_localized_edition(item) for item in items]
    has_primary_by_title = {}
    query_key = normalize_search_text(query)
    has_unlocalized_query_match = False

    for item, is_localized in zip(items, localized_flags, strict=True):
        title_key = normalize_search_text(item.get("name"))
        if title_key and not is_localized:
            has_primary_by_title[title_key] = True
            if title_key == query_key:
                has_unlocalized_query_match = True

    results = []
    for item, is_localized in zip(items, localized_flags, strict=True):
        title_key = normalize_search_text(item.get("name"))
        issue_count = _positive_int(item.get("count_of_issues"))
        rank_boost = _issue_count_rank_boost(issue_count)
        if is_localized and has_primary_by_title.get(title_key, False):
            rank_boost -= LOCALIZED_EDITION_PENALTY
        elif is_localized and has_unlocalized_query_match:
            rank_boost -= LOCALIZED_RELATED_EDITION_PENALTY

        results.append(
            {
                "media_id": str(item["id"]),
                "source": Sources.COMICVINE.value,
                "media_type": MediaTypes.COMIC.value,
                "title": item["name"],
                "subtitle": _search_subtitle(item, issue_count),
                "image": get_image(item),
                "first_publish_year": item.get("start_year"),
                "provider_rank_boost": rank_boost,
            },
        )

    return results


def _is_likely_localized_edition(item):
    """Return whether Comic Vine explicitly identifies a localized edition."""
    deck = normalize_search_text(item.get("deck"))
    if deck and LOCALIZED_EDITION_PATTERN.search(deck):
        return True

    publisher_tokens = set(normalize_search_text(_publisher_name(item)).split())
    return bool(publisher_tokens & LOCALIZED_PUBLISHER_MARKERS)


def _publisher_name(item):
    publisher = item.get("publisher")
    if isinstance(publisher, dict):
        return publisher.get("name") or ""
    return ""


def _positive_int(value):
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _issue_count_rank_boost(issue_count):
    if not issue_count:
        return 0
    return min(14, math.log2(issue_count + 1) * 2)


def _search_subtitle(item, issue_count):
    parts = []
    if item.get("start_year"):
        parts.append(str(item["start_year"]))
    if publisher := _publisher_name(item):
        parts.append(publisher)
    if issue_count:
        unit = "issue" if issue_count == 1 else "issues"
        parts.append(f"{issue_count} {unit}")
    return " · ".join(parts) or None


def comic(media_id):
    """Return the metadata for the selected comic volume from Comic Vine."""
    cache_key = f"{Sources.COMICVINE.value}_{MediaTypes.COMIC.value}_{media_id}"
    data = cache.get(cache_key)

    if data is None:
        params = {
            "api_key": settings.COMICVINE_API,
            "format": "json",
            "field_list": (
                "publisher,site_detail_url,name,last_issue,image,description,"
                "concepts,start_year,count_of_issues,people,date_last_updated"
            ),
        }

        try:
            response = services.api_request(
                Sources.COMICVINE.value,
                "GET",
                f"{base_url}/volume/4050-{media_id}/",
                params=params,
                headers=headers,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        response = response.get("results", {})

        # Check if response is empty (no results found)
        if not response:
            services.raise_not_found_error(
                Sources.COMICVINE.value,
                media_id,
                "comic",
            )

        publisher_id = response.get("publisher", {}).get("id")
        publisher_comics = []
        if publisher_id:
            publisher_comics = get_publisher_comics(publisher_id, media_id)

        data = {
            "media_id": media_id,
            "source": Sources.COMICVINE.value,
            "source_url": response["site_detail_url"],
            "media_type": MediaTypes.COMIC.value,
            "title": response["name"],
            "max_progress": None,
            "max_issue_number": get_issue_number(
                response["last_issue"]["issue_number"],
            ),
            "image": get_image(response),
            "synopsis": get_synopsis(response),
            "genres": get_genres(response),
            "score": None,
            "score_count": None,
            "details": {
                "start_date": get_start_year(response),
                "publisher": get_publisher_name(response),
                "issues_count": get_issues_count(response),
                "last_issue_name": get_last_issue_name(response),
                "last_issue_number": get_last_issue_number(response),
                "people": get_people(response),
                "last_updated": response.get("date_last_updated").split()[0],
            },
            "related": {
                "recommendations": publisher_comics,
            },
            # used for events fetching
            "last_issue_id": response["last_issue"]["id"],
        }

        cache.set(cache_key, data)

    return data


def get_image(response):
    """Return the image URL."""
    if "image" in response:
        return response["image"]["medium_url"]
    return settings.IMG_NONE


def get_synopsis(response):
    """Return the synopsis."""
    if not response.get("description"):
        return "No synopsis available"

    soup = BeautifulSoup(response["description"], "html.parser")
    text = soup.get_text(separator=" ")
    return " ".join(text.split())


def get_genres(response):
    """Return the list of genres."""
    if "concepts" in response:
        return [concept["name"] for concept in response["concepts"][:5]]
    return None


def get_start_year(response):
    """Return the start year of the comic volume."""
    return response.get("start_year")


def get_publisher_name(response):
    """Return the publisher name of the comic volume."""
    publisher = response.get("publisher")
    if publisher and isinstance(publisher, dict):
        return publisher.get("name")
    return None


def get_issues_count(response):
    """Return the count of issues in the comic volume."""
    return response.get("count_of_issues")


def get_last_issue_name(response):
    """Return the name of the last issue in the comic volume."""
    last_issue = response.get("last_issue")
    if last_issue and isinstance(last_issue, dict):
        return last_issue.get("name")
    return None


def get_issue_number(issue_number):
    """Return the last issue number as an integer if possible.

    For compound issue numbers (like "463-464"), returns the highest number.
    Returns None if no valid issue number can be extracted.
    """
    try:
        return int(issue_number)

    except ValueError:
        # Handle compound issue numbers like "463-464"
        try:
            # Split by hyphen and get the highest number
            parts = [int(part.strip()) for part in issue_number.split("-")]
            return max(parts)

        except (ValueError, AttributeError):
            return None


def get_last_issue_number(response):
    """Return the last issue number."""
    last_issue = response.get("last_issue")
    if last_issue and isinstance(last_issue, dict):
        return last_issue.get("issue_number")
    return None


def get_people(response):
    """Return the people associated with the comic volume."""
    people = response.get("people", [])
    return [person["name"] for person in people[:5] if isinstance(person, dict)]


def get_publisher_comics(publisher_id, current_id, limit=15):
    """Get comics from the same publisher."""
    cache_key = f"{Sources.COMICVINE.value}_publisher_{publisher_id}_{current_id}"
    data = cache.get(cache_key)

    if data is None:
        params = {
            "api_key": settings.COMICVINE_API,
            "format": "json",
            "field_list": "id,name,image,start_year,publisher",
            "filter": f"publisher:{publisher_id}",
            "limit": limit + 1,  # Get one extra to account for current comic
        }

        try:
            response = services.api_request(
                Sources.COMICVINE.value,
                "GET",
                f"{base_url}/volumes/",
                params=params,
                headers=headers,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        # Filter out the current comic and format the response
        data = [
            {
                "media_id": str(item["id"]),
                "source": Sources.COMICVINE.value,
                "media_type": MediaTypes.COMIC.value,
                "title": item["name"],
                "image": get_image(item),
            }
            for item in response["results"]
            if str(item["id"]) != current_id
        ][:limit]

        cache.set(cache_key, data)

    return data


def issue(media_id):
    """Return the metadata for the selected comic issue from Comic Vine."""
    cache_key = f"{Sources.COMICVINE.value}_issue_{media_id}"
    data = cache.get(cache_key)

    if data is None:
        params = {
            "api_key": settings.COMICVINE_API,
            "format": "json",
            "field_list": ("cover_date,store_date"),
        }

        try:
            response = services.api_request(
                Sources.COMICVINE.value,
                "GET",
                f"{base_url}/issue/4000-{media_id}/",
                params=params,
                headers=headers,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        response = response.get("results", {})

        data = {
            "cover_date": response.get("cover_date"),
            "store_date": response.get("store_date"),
        }

        cache.set(cache_key, data)

    return data
