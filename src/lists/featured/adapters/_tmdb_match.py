"""TMDB title matching for film and TV sources that send titles only.

Netflix and the Golden Globes name a title (and sometimes a year) but no id. The
engine's strict title search gives up when a title is ambiguous; these sources know
more about what they list (a chart is current, an award winner aired before the
ceremony), so their adapters pick a TMDB id here first:

- the normalized title must equal TMDB's normalized title or original title
  exactly, trying each candidate title in order (``titles``);
- the release year must fall inside ``min_year``..``max_year`` when given;
- ``english=True`` keeps only titles originally in English, ``False`` only the rest
  (Netflix splits its charts that way);
- ``recent_since`` prefers titles from that year on, when any qualify (a show's
  first season on a chart is usually new, not the 1974 series of the same name);
- among what is left, the highest ``prefer`` value wins (``popularity`` for charts,
  ``vote_count`` for awards).

When nothing qualifies the entry is returned unchanged and the engine's resolver
tries its strict search, then overrides. An override on the entry's title key
(``t:<normalized title>|<year>``) always wins: the adapter then leaves the entry
alone so the resolver applies it. Use the title key to fix a wrong pick too.
"""

import dataclasses
import hashlib
import logging

from django.core.cache import cache

from app.providers import tmdb
from lists.featured import overrides
from lists.featured.helpers import get_json
from lists.featured.text import normalize_title, parse_year
from lists.featured.types import AdapterError

logger = logging.getLogger(__name__)

CACHE_TTL = 60 * 60 * 24


def search_results(media_type, title):
    """Return TMDB's first page of raw results for ``title`` (cached a day); empty on errors."""
    digest = hashlib.sha256(f"{media_type}|{title}".encode()).hexdigest()[:32]
    cache_key = f"featured:tmdb-search:{digest}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        data = get_json(
            "tmdb",
            f"{tmdb.base_url}/search/{media_type}",
            params={**tmdb.base_params, "query": title, "include_adult": "false"},
        )
    except AdapterError as error:
        logger.warning("Featured TMDB search failed for %r: %s", title, error)
        return []
    results = (data.get("results") or []) if isinstance(data, dict) else []
    cache.set(cache_key, results, CACHE_TTL)
    return results


def _titles(result):
    return {
        normalize_title(result.get(field))
        for field in ("title", "name", "original_title", "original_name")
        if result.get(field)
    }


def best_match(  # noqa: PLR0913
    media_type,
    titles,
    *,
    min_year=None,
    max_year=None,
    english=None,
    recent_since=None,
    prefer="popularity",
):
    """Return the raw TMDB search result that best matches one of ``titles``, or None."""
    tried = set()
    for title in titles:
        wanted = normalize_title(title)
        if not wanted or wanted in tried:
            continue
        tried.add(wanted)
        candidates = []
        for result in search_results(media_type, title):
            if wanted not in _titles(result):
                continue
            if english is not None and (result.get("original_language") == "en") != english:
                continue
            year = parse_year(result.get("release_date") or result.get("first_air_date"))
            if (min_year or max_year) and year is None:
                continue
            if min_year and year < min_year:
                continue
            if max_year and year > max_year:
                continue
            candidates.append((year, result))
        if recent_since:
            recent = [(year, result) for year, result in candidates if year and year >= recent_since]
            candidates = recent or candidates
        candidates = [result for _, result in candidates]
        if candidates:
            return max(candidates, key=lambda result: result.get(prefer) or 0)
    return None


def with_tmdb_match(definition, entry, titles, **bounds):
    """Return ``entry`` with a TMDB id and poster when one matches (see module docs)."""
    if entry.key in overrides.for_definition(definition) or not entry.media_type:
        return entry
    result = best_match(entry.media_type, titles, **bounds)
    if result is None:
        return entry
    poster = result.get("poster_path")
    return dataclasses.replace(
        entry,
        ids={**entry.ids, "tmdb": str(result["id"])},
        image=tmdb.get_image_url(poster) if poster else entry.image,
    )
