"""Apple Music catalog API client.

Raw catalog fetches that return Apple's JSON resources unmapped (see
docs/music/apple-music-catalog.md). Nothing outside the tests calls it yet.

The developer token is signed here from the ``APPLE_MUSIC_*`` key settings and kept
in this process's memory only: never logged, never written to the cache.
"""

import base64
import logging
import time
from urllib.parse import quote

import jwt
import requests
from cryptography.exceptions import UnsupportedAlgorithm
from django.conf import settings
from django.views.decorators.debug import sensitive_variables

from app.models import Sources
from app.providers import services

APPLE_MUSIC_URL = "https://api.music.apple.com"
TOKEN_LIFETIME = 12 * 60 * 60
# Re-sign once less than this is left, so a token never expires mid-request.
TOKEN_REFRESH_MARGIN = 60 * 60
# Requests someone is waiting on give up quickly, then pause Apple calls for a minute.
USER_TIMEOUT = 10
BACKOFF_KEY = "apple_music:backoff"
BACKOFF_SECONDS = 60
ALBUM_BATCH = 100
FILTER_BATCH = 25
SEARCH_LIMIT = 25
# Bounds a `next` chain, in case Apple ever repeats a link.
MAX_PAGES = 20
logger = logging.getLogger(__name__)

# (the key settings it was signed with, token, expiry)
_token = None


class SkippedRequestError(services.ProviderAPIError):
    """Raised instead of a request: the key isn't usable, or Apple failed a moment ago.

    Callers handle it like any other Apple Music failure. Nothing was sent, so it has
    no status code and, unlike its parent, logs nothing.
    """

    def __init__(self, message):
        self.provider = Sources.APPLE_MUSIC.value
        self.status_code = None
        Exception.__init__(self, message)


def is_configured():
    """Return whether all three Apple Music key settings are set."""
    return all(
        str(value or "").strip()
        for value in (
            settings.APPLE_MUSIC_TEAM_ID,
            settings.APPLE_MUSIC_KEY_ID,
            settings.APPLE_MUSIC_PRIVATE_KEY,
        )
    )


@sensitive_variables("signer", "private_key", "token")
def developer_token():
    """Return a signed developer token, re-signing when less than an hour is left."""
    global _token  # noqa: PLW0603
    if not is_configured():
        raise SkippedRequestError("Apple Music isn't configured")
    signer = (
        str(settings.APPLE_MUSIC_TEAM_ID).strip(),
        str(settings.APPLE_MUSIC_KEY_ID).strip(),
        str(settings.APPLE_MUSIC_PRIVATE_KEY),
    )
    now = int(time.time())
    if _token is not None and _token[0] == signer and _token[2] - now > TOKEN_REFRESH_MARGIN:
        return _token[1]
    try:
        private_key = base64.b64decode("".join(signer[2].split()), validate=True)
        token = jwt.encode(
            {"iss": signer[0], "iat": now, "exp": now + TOKEN_LIFETIME},
            private_key,
            algorithm="ES256",
            headers={"kid": signer[1]},
        )
    except (ValueError, TypeError, jwt.PyJWTError, UnsupportedAlgorithm):
        # Never chain or repeat the error: it could quote the key.
        logger.error("Apple Music's private key can't be read")  # noqa: TRY400
        raise SkippedRequestError("Apple Music's private key can't be read") from None
    _token = (signer, token, now + TOKEN_LIFETIME)
    return token


def search(term, *, types, next_path=None, background=False):
    """Return one page of up to 25 search results, with Apple's ``next`` link."""
    params = {"limit": SEARCH_LIMIT}
    if next_path:
        # Apple's next link keeps the term and offset but drops the limit.
        return _get(next_path, params, background=background)
    params.update(term=term, types=_csv(types))
    return _get("search", params, background=background)


def albums(ids, *, views=(), include=(), background=False):
    """Return album resources for ``ids``, 100 per request; unknown IDs are left out."""
    resources = []
    for batch in _batches(ids, ALBUM_BATCH):
        params = {"ids": ",".join(batch), **_options(views, include)}
        resources.extend(_get("albums", params, background=background).get("data") or [])
    return resources


def album(album_id, *, views=(), include=("tracks",), background=False):
    """Return one album resource, with every page of its tracks."""
    response = _get(
        f"albums/{_path_value(album_id)}",
        _options(views, include),
        background=background,
    )
    resource = _first(response)
    tracks = (resource.get("relationships") or {}).get("tracks")
    next_path = tracks.pop("next", None) if tracks else None
    for _ in range(MAX_PAGES):
        if not next_path:
            break
        page = _get(next_path, background=background)
        tracks.setdefault("data", []).extend(page.get("data") or [])
        next_path = page.get("next")
    return resource


def albums_by_upc(upcs, *, background=False):
    """Return the albums for these barcodes, 25 per request.

    Apple returns each barcode's whole product family, not just the exact album.
    """
    resources = []
    for batch in _batches(upcs, FILTER_BATCH):
        params = {"filter[upc]": ",".join(batch)}
        resources.extend(_get("albums", params, background=background).get("data") or [])
    return resources


def songs_by_isrc(isrcs, *, background=False):
    """Return every song with these ISRCs, 25 per request: one per album it is on."""
    resources = []
    for batch in _batches(isrcs, FILTER_BATCH):
        params = {"filter[isrc]": ",".join(batch)}
        resources.extend(_get("songs", params, background=background).get("data") or [])
    return resources


def artist(artist_id, *, views=(), background=False):
    """Return one artist resource, with the requested views."""
    response = _get(
        f"artists/{_path_value(artist_id)}",
        _options(views, ()),
        background=background,
    )
    return _first(response)


@sensitive_variables("token", "headers")
def _get(path, params=None, *, background=False):
    """GET a catalog path (``albums/1440826538``) or an Apple ``next`` link.

    Requests someone is waiting on use the user-facing bucket, a 10-second timeout and
    no 429 retries; a 429, 5xx or timeout pauses them for a minute. ``background``
    requests use the slower bucket and the usual retries, and ignore the pause.
    """
    url = _url(path)
    token = developer_token()
    if not background and is_paused():
        raise SkippedRequestError("Apple Music is paused for a minute after a failed request")
    headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}
    if background:
        request_session, timeout = services.apple_music_background_session, None
    else:
        request_session, timeout = services.apple_music_session, USER_TIMEOUT
    try:
        return services.api_request(
            Sources.APPLE_MUSIC.value,
            "GET",
            url,
            params=params,
            headers=headers,
            request_session=request_session,
            timeout=timeout,
            retry_rate_limits=background,
        )
    except requests.exceptions.JSONDecodeError as error:
        raise services.ProviderAPIError(
            Sources.APPLE_MUSIC.value,
            error,
            "invalid JSON response",
        ) from error
    except requests.exceptions.HTTPError as error:
        status_code = getattr(error.response, "status_code", None) or 0
        if not background and (
            status_code == requests.codes.too_many_requests
            or status_code >= requests.codes.internal_server_error
        ):
            _back_off()
        raise services.ProviderAPIError(Sources.APPLE_MUSIC.value, error) from error
    except requests.exceptions.Timeout as error:
        if not background:
            _back_off()
        raise services.ProviderAPIError(Sources.APPLE_MUSIC.value, error) from error
    except requests.RequestException as error:
        raise services.ProviderAPIError(Sources.APPLE_MUSIC.value, error) from error


def is_paused():
    """Return whether user-facing Apple calls are paused after a failed request."""
    return bool(services.redis_db.exists(backoff_key()))


def backoff_key():
    # In Redis rather than the Django cache, which is a dummy cache under DEBUG.
    prefix = f"{settings.REDIS_PREFIX}:" if settings.REDIS_PREFIX else ""
    return prefix + BACKOFF_KEY


def _back_off():
    services.redis_db.set(backoff_key(), 1, ex=BACKOFF_SECONDS)


def _url(path):
    # Only paths on Apple's API host are requested, so the token never goes elsewhere.
    if path.startswith("/v1/"):
        return f"{APPLE_MUSIC_URL}{path}"
    if path.startswith("/") or "://" in path:
        msg = f"Not an Apple Music API path: {path}"
        raise ValueError(msg)
    return f"{APPLE_MUSIC_URL}/v1/catalog/{_storefront()}/{path}"


def _storefront():
    return str(settings.MUSIC_DEFAULT_MARKET or "us").strip().lower()


def _options(views, include):
    options = {}
    if views:
        options["views"] = _csv(views)
    if include:
        options["include"] = _csv(include)
    return options


def _csv(values):
    if isinstance(values, str):
        return values
    return ",".join(values)


def _batches(values, size):
    unique = list(dict.fromkeys(str(value).strip() for value in values if value))
    unique = [value for value in unique if value]
    return [unique[start : start + size] for start in range(0, len(unique), size)]


def _first(response):
    data = response.get("data") if isinstance(response, dict) else None
    if not data:
        raise services.ProviderAPIError(
            Sources.APPLE_MUSIC.value,
            ValueError("no data in response"),
            "response has no data",
        )
    return data[0]


def _path_value(value):
    return quote(str(value), safe="")
