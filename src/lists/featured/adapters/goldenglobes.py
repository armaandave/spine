"""Golden Globe winners, from goldenglobes.com's WordPress REST API (no auth).

Params: ``{"category": 5}``, an ``award-categories`` term id (Best Motion Picture -
Drama is 5; the list is ``/wp-json/wp/v2/award-categories``). The definition's single
media type picks the record type: ``movie`` reads ``/film``, ``tv`` reads ``/tv-show``.

Each nomination has ``acf.year`` (the ceremony), ``acf.is_winner`` and
``acf.film_tv_show``, the id of its film or show record. Winners come newest first,
unranked, with the ceremony year as the note; a show that won several times appears
once, noted "2024, 2022, 2020". Titles are matched to TMDB here (``_tmdb_match``):
films by title within a year of their release (or of the year before the ceremony),
shows by title among those that premiered before their first win; the most-voted
exact match wins.
"""

import html
import math
import re

from lists.featured.adapters._tmdb_match import with_tmdb_match
from lists.featured.helpers import get_json
from lists.featured.text import parse_year
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

API = "https://www.goldenglobes.com/wp-json/wp/v2"
RECORDS = {"movie": "film", "tv": "tv-show"}
PER_PAGE = 100
MAX_PAGES = 20
_ARTICLE = re.compile(r"^(?P<title>.+),\s+(?P<article>The|A|An)$", re.IGNORECASE)
# "(1976)", "(1981-1989)", "(UK)", "(TV Show)", "– TV Show"
_SUFFIX = re.compile(
    r"\s*(?:\((?:(?P<year>(?:19|20)\d\d)(?:\s*[-–]\s*(?:19|20)?\d\d)?|UK|US|TV Show|TV Series)\)"
    r"|[-–—]\s*TV (?:Show|Series))$",
    re.IGNORECASE,
)


def display_title(value):
    """Return ``(title, year)`` from a record title.

    Unescaped; a trailing "(1976)" or "(1981-1989)" split off as the year; "(UK)",
    "(TV Show)" and "– TV Show" dropped; "Star Is Born, A" turned into "A Star Is Born".
    """
    title = html.unescape(str(value or "")).strip()
    year = None
    while match := _SUFFIX.search(title):
        year = year or (int(match["year"]) if match["year"] else None)
        title = title[: match.start()].strip()
    match = _ARTICLE.match(title)
    return (f"{match['article']} {match['title']}" if match else title), year


def validate(definition):
    """Check the params without network."""
    category = definition.params.get("category")
    if not isinstance(category, int) or category <= 0:
        msg = "category must be a positive award-categories id"
        raise ValueError(msg)
    if len(definition.media_types) != 1 or definition.media_types[0] not in RECORDS:
        msg = "Golden Globes lists hold either movies or TV"
        raise ValueError(msg)
    if definition.is_ranked:
        msg = "Golden Globes winner lists are unranked"
        raise ValueError(msg)


def _get(path, params):
    return get_json("goldenglobes", f"{API}/{path}", params=params)


def winners(category):
    """Return ``[(ceremony year, record id), …]`` for every winning nomination."""
    term = _get(f"award-categories/{category}", {"_fields": "id,name,count"})
    count = int((term or {}).get("count") or 0)
    if not count:
        msg = f"Golden Globes category {category} has no nominations."
        raise AdapterError(msg)
    found = []
    for page in range(1, min(math.ceil(count / PER_PAGE), MAX_PAGES) + 1):
        rows = _get(
            "nomination",
            {"award-categories": category, "per_page": PER_PAGE, "page": page, "_fields": "id,acf"},
        )
        for row in rows or []:
            acf = row.get("acf") or {}
            if acf.get("is_winner") and acf.get("film_tv_show") and acf.get("year"):
                found.append((int(acf["year"]), int(acf["film_tv_show"])))
    return found


def records(record_type, ids):
    """Return ``{id: record}`` for film or tv-show records, 100 per request."""
    found = {}
    ids = sorted(set(ids))
    for start in range(0, len(ids), PER_PAGE):
        chunk = ids[start : start + PER_PAGE]
        rows = _get(
            record_type,
            {"include": ",".join(map(str, chunk)), "per_page": PER_PAGE, "_fields": "id,title,acf"},
        )
        for row in rows or []:
            found[int(row["id"])] = row
    return found


def _entry(definition, media_type, record, years):
    title, title_year = display_title((record.get("title") or {}).get("rendered"))
    first_win = min(years)
    note = ", ".join(str(year) for year in sorted(years, reverse=True))
    if media_type == "movie":
        released = parse_year(str((record.get("acf") or {}).get("release_date") or ""))
        year = released or title_year or first_win - 1
        entry = Entry(rank=None, title=title, year=year, media_type=media_type, note=note)
        return with_tmdb_match(
            definition,
            entry,
            [title],
            min_year=year - 1,
            max_year=min(year + 1, first_win),
            prefer="vote_count",
        )
    entry = Entry(rank=None, title=title, media_type=media_type, note=note)
    bounds = {"min_year": title_year - 1, "max_year": min(title_year + 1, first_win)} if title_year else {}
    return with_tmdb_match(definition, entry, [title], **{"max_year": first_win, **bounds}, prefer="vote_count")


def fetch(definition):
    """Fetch every winner of one category."""
    media_type = definition.media_types[0]
    won = winners(definition.params["category"])
    if not won:
        msg = f"Golden Globes category {definition.params['category']} has no winners."
        raise AdapterError(msg)
    by_record = {}
    for year, record_id in won:
        by_record.setdefault(record_id, set()).add(year)
    found = records(RECORDS[media_type], by_record)
    missing = sorted(set(by_record) - set(found))
    if missing:  # the site files a few films as tv-show records ("Babe") and vice versa
        other = next(record_type for kind, record_type in RECORDS.items() if kind != media_type)
        found.update(records(other, missing))
        missing = sorted(set(by_record) - set(found))
    if missing:
        msg = f"Golden Globes {RECORDS[media_type]} records missing: {missing[:10]}"
        raise AdapterError(msg)
    ordered = sorted(by_record.items(), key=lambda item: (-max(item[1]), item[0]))
    entries = [_entry(definition, media_type, found[record_id], years) for record_id, years in ordered]
    return FetchResult(entries=entries, edition=str(max(year for year, _ in won)))


ADAPTER = Adapter(key="goldenglobes", fetch=fetch, validate=validate)
