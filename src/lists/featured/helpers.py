"""HTTP and error helpers for featured-list adapters.

Adapters fetch through these so that every request carries Spine's User-Agent,
goes through the shared rate-limited provider session, can be mocked in tests by
patching ``app.providers.services.api_request``, and fails with a sanitized
``AdapterError``.
"""

import re

import requests
from django.conf import settings

from app.providers import services
from lists.featured.types import AdapterError

USER_AGENT = "Spine/1.0 (+https://github.com/armaandave/spine)"
SECRET_SETTINGS = (
    "TMDB_API",
    "TVDB_API",
    "MAL_API",
    "IGDB_ID",
    "IGDB_SECRET",
    "STEAM_API_KEY",
    "STEAMGRIDDB_API_KEY",
    "HARDCOVER_API",
    "NYT_BOOKS_API_KEY",
    "COMICVINE_API",
    "TRAKT_API",
    "TRAKT_API_SECRET",
    "ANILIST_ID",
    "ANILIST_SECRET",
    "SIMKL_ID",
    "SIMKL_SECRET",
    "MDBLIST_API",
    "IMDB_API_KEY",
    "LISTENBRAINZ_TOKEN",
    "GOOGLE_BOOKS_API_KEY",
    "FEATURED_REPORT_TOKEN",
)
_QUERY_SECRET = re.compile(
    r"((?:api[-_]?key|apikey|key|token|secret|client_secret|client_id|access_token)=)[^&\s'\"]+",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+")
MIN_SECRET_LENGTH = 6
MAX_ERROR_LENGTH = 500


def sanitize(message, limit=MAX_ERROR_LENGTH):
    """Return an error message with every configured secret and keyed URL removed."""
    text = str(message or "")
    for name in SECRET_SETTINGS:
        value = getattr(settings, name, "")
        if isinstance(value, str) and len(value) >= MIN_SECRET_LENGTH:
            text = text.replace(value, "<redacted>")
    text = _QUERY_SECRET.sub(r"\1<redacted>", text)
    text = _BEARER.sub(r"\1<redacted>", text)
    return text[:limit]


def _headers(headers):
    return {"User-Agent": USER_AGENT, **(headers or {})}


def _request(provider, method, url, *, response_format="json", **kwargs):
    try:
        return services.api_request(
            provider,
            method,
            url,
            response_format=response_format,
            **kwargs,
        )
    except requests.RequestException as error:
        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None)
        suffix = f" (HTTP {status_code})" if status_code is not None else " (network error)"
        message = f"{provider} request failed{suffix}."
        raise AdapterError(message) from None
    except services.ProviderAPIError as error:
        raise AdapterError(sanitize(error)) from None
    except ValueError as error:  # malformed JSON or XML
        message = f"{provider} returned an unreadable response: {sanitize(error, 120)}"
        raise AdapterError(message) from None


def get_json(provider, url, *, params=None, headers=None, timeout=None):
    """GET a JSON document."""
    return _request(
        provider,
        "GET",
        url,
        params=params,
        headers=_headers(headers),
        timeout=timeout,
    )


def get_text(provider, url, *, params=None, headers=None, timeout=None):
    """GET a text or HTML document."""
    return _request(
        provider,
        "GET",
        url,
        params=params,
        headers=_headers(headers),
        timeout=timeout,
        response_format="text",
    )


def post_json(provider, url, *, json=None, data=None, headers=None, timeout=None):
    """POST a JSON body (``json``) or raw ``data`` and return the JSON response."""
    return _request(
        provider,
        "POST",
        url,
        params=json,
        data=data,
        headers=_headers(headers),
        timeout=timeout,
    )
