"""MDBList lists: external mirrors, public user lists, and MDBList's official lists.

Params (exactly one mode):

- ``{"external": "155837"}`` or ``{"external": "<MDBList or Letterboxd/Trakt URL>"}``:
  an external list owned by our MDBList account. A source URL is matched against
  ``GET /external/lists/user``.
- ``{"list_id": "101881"}``: anyone's public MDBList list, via ``/lists/{id}/items``.
  Optional ``"fallback_ids": ["12306"]``: other public lists tried in order when
  this one fails or comes back empty (user lists can vanish).
- ``{"official": "moviemeter", "mediatype": "movie" | "show"}``: an MDBList
  official list via ``/lists/official/{slug}/items``.

Optional: ``"limit": N`` keeps only the top N entries.
"""

import re
from urllib.parse import urlparse

import requests
from django.conf import settings

from app.providers import mdblist, services
from lists.featured.helpers import sanitize
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

MODES = ("external", "list_id", "official")
MEDIATYPES = {"movie": "movie", "show": "tv"}
_TMDB_SIZE = re.compile(r"^(https://image\.tmdb\.org/t/p/)w\d+(/.+)$")


def poster_url(payload):
    """Return a usable poster URL from MDBList's optional poster fields."""
    value = payload.get("poster") or payload.get("poster_path")
    if isinstance(value, dict):
        value = value.get("url") or value.get("large") or value.get("medium")
    if isinstance(value, str) and value.startswith("/"):
        return f"https://image.tmdb.org/t/p/original{value}"
    if isinstance(value, str):
        # MDBList returns small w200 TMDB posters; store the size Spine uses elsewhere.
        return _TMDB_SIZE.sub(r"\1w500\2", value)
    return settings.IMG_NONE


def external_list_id(value):
    """Return an external-list ID from an ID or an ``mdblist.com/…/external/<id>`` URL."""
    raw = str(value).strip().rstrip("/")
    candidate = urlparse(raw).path.rsplit("/", 1)[-1] if "://" in raw else raw
    return candidate if candidate.isdigit() else None


def _source_path(url):
    parsed = urlparse(str(url).strip())
    return parsed.path.strip("/").lower()


def find_external_list(url):
    """Return our account's external list that mirrors ``url`` (Letterboxd, Trakt…)."""
    wanted = _source_path(url)
    for remote in mdblist.get_user_external_lists():
        if _source_path(remote.get("url") or "") == wanted:
            return remote
    msg = f"No MDBList external list on our account mirrors {wanted!r}. Add {url} as an MDBList external list."
    raise AdapterError(msg)


def _mode(params):
    modes = [mode for mode in MODES if params.get(mode)]
    if len(modes) != 1:
        msg = f"set exactly one of {', '.join(MODES)}"
        raise ValueError(msg)
    return modes[0]


def validate(definition):
    """Check the params without network."""
    params = definition.params
    mode = _mode(params)
    if mode == "external":
        value = str(params["external"])
        if external_list_id(value) is None and "://" not in value:
            msg = "external must be an MDBList list ID or a URL"
            raise ValueError(msg)
    if mode == "list_id" and not str(params["list_id"]).isdigit():
        msg = "list_id must be numeric"
        raise ValueError(msg)
    fallback_ids = params.get("fallback_ids")
    if fallback_ids is not None:
        if mode != "list_id":
            msg = "fallback_ids only applies to list_id"
            raise ValueError(msg)
        if isinstance(fallback_ids, str) or not all(str(value).isdigit() for value in fallback_ids):
            msg = "fallback_ids must be a list of numeric list IDs"
            raise ValueError(msg)
    if mode == "official" and params.get("mediatype") not in MEDIATYPES:
        msg = "official lists need mediatype movie or show"
        raise ValueError(msg)
    if "limit" in params and int(params["limit"]) < 1:
        msg = "limit must be positive"
        raise ValueError(msg)
    unsupported = set(definition.media_types) - {"movie", "tv"}
    if unsupported:
        msg = f"MDBList lists hold movies and TV only, not {sorted(unsupported)}"
        raise ValueError(msg)


def _rank_note(item):
    change = item.get("rank_change")
    if not isinstance(change, int) or change == 0:
        return ""
    return f"▲{change}" if change > 0 else f"▼{-change}"


def entries_from_items(items, limit=None):
    """Turn MDBList item payloads into entries ranked 1..n in MDBList's order."""
    ordered = sorted(
        items,
        key=lambda item: (item.get("rank") is None, item.get("rank") or 0),
    )
    entries = []
    for index, item in enumerate(ordered, start=1):
        ids = item.get("ids") or {}
        media_type = item.get("spine_media_type") or MEDIATYPES.get(item.get("mediatype"))
        entries.append(
            Entry(
                rank=index,
                title=str(item.get("title") or ""),
                year=item.get("release_year"),
                media_type=media_type,
                ids={
                    "tmdb": ids.get("tmdb") or item.get("id"),
                    "imdb": ids.get("imdb") or item.get("imdb_id"),
                },
                image=poster_url(item),
                note=_rank_note(item),
            ),
        )
    return entries[:limit] if limit else entries


def _network_problem(error):
    """Describe a requests failure without its message, which holds the keyed URL."""
    status_code = getattr(getattr(error, "response", None), "status_code", None)
    return f"HTTP {status_code}" if status_code is not None else f"network error ({type(error).__name__})"


def _public_list(list_ids):
    """Return ``(metadata, items)`` from the first of ``list_ids`` that has items.

    The last list is returned even when empty, so the writer's guards judge it.
    """
    problems = []
    for index, list_id in enumerate(list_ids):
        try:
            metadata = mdblist.get_list(list_id)
            items = mdblist.get_list_items(list_id)
        except services.ProviderAPIError as error:
            problems.append(f"list {list_id}: {sanitize(error, 160)}")
            continue
        except requests.RequestException as error:  # its text names the keyed URL
            problems.append(f"list {list_id}: {_network_problem(error)}")
            continue
        if items or index == len(list_ids) - 1:
            return metadata, items
        problems.append(f"list {list_id} is empty")
    raise AdapterError("MDBList public lists failed: " + "; ".join(problems))


def fetch(definition):
    """Fetch one MDBList list."""
    params = definition.params
    mode = _mode(params)
    try:
        if mode == "external":
            value = str(params["external"])
            list_id = external_list_id(value)
            metadata = find_external_list(value) if list_id is None else mdblist.get_external_list(list_id)
            list_id = list_id or str(metadata["id"])
            items = mdblist.get_external_list_items(list_id)
        elif mode == "list_id":
            metadata, items = _public_list([params["list_id"], *params.get("fallback_ids", ())])
        else:
            metadata = {}
            items = mdblist.get_official_list_items(params["official"], params["mediatype"])
    except services.ProviderAPIError as error:
        raise AdapterError(sanitize(error)) from None
    except requests.RequestException as error:
        raise AdapterError(f"MDBList request failed: {_network_problem(error)}.") from None
    metadata = metadata if isinstance(metadata, dict) else {}
    limit = int(params["limit"]) if params.get("limit") else None
    expected = metadata.get("items")
    return FetchResult(
        entries=entries_from_items(items, limit),
        expected_count=int(expected) if expected and not limit else None,
        name=metadata.get("name") or None,
    )


ADAPTER = Adapter(key="mdblist", fetch=fetch, validate=validate)
