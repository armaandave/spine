"""Netflix Top 10, from the TSV files behind netflix.com/tudum/top10 (no auth).

Params:

- ``{"file": "weekly", "category": "Films (English)"}``: the latest week of one
  category from ``all-weeks-global.tsv``. Edition "Week of Oct 12, 2026".
- ``{"file": "most-popular", "categories": ["TV (English)", "TV (Non-English)"]}``:
  the all-time most popular titles (views in their first 91 days) from
  ``most-popular.tsv``; several categories are merged and re-ranked by views, and a
  show with several seasons on the list appears once, at its best season.

Categories: "Films (English)", "Films (Non-English)", "TV (English)",
"TV (Non-English)". Netflix sends titles only, so entries are matched to TMDB here
(``_tmdb_match``): TV tries the show title without ": Season 2" or ": Limited Series",
then the series name from ``season_title``; the most popular exact match in the
category's language (English or not) wins.

Every list of a file shares one download, cached for ``CACHE_TTL`` seconds (the weekly
file is about 900 KB), so a pass over the four weekly lists fetches it once.

``"min_entries"`` is the rows a complete chart has: 10 for the latest week of a weekly
category, 10 per category for most-popular. Fewer fail the run, so a file caught
mid-update never shrinks a list.
"""

import csv
import io
import re
from datetime import date, timedelta

from django.core.cache import cache

from lists.featured.adapters import _rows
from lists.featured.adapters._tmdb_match import with_tmdb_match
from lists.featured.helpers import get_text
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

BASE_URL = "https://www.netflix.com/tudum/top10/data/"
FILES = {"weekly": "all-weeks-global.tsv", "most-popular": "most-popular.tsv"}
CATEGORIES = {
    "Films (English)": "movie",
    "Films (Non-English)": "movie",
    "TV (English)": "tv",
    "TV (Non-English)": "tv",
}
COLUMNS = {
    "weekly": {"week", "category", "weekly_rank", "show_title", "season_title", "cumulative_weeks_in_top_10"},
    "most-popular": {"category", "rank", "show_title", "season_title", "views_first_91_days"},
}
NONE = {"", "N/A"}
CACHE_PREFIX = "featured:netflix:"
CACHE_TTL = 30 * 60
# ": Season 2", " - Part 3", ": Limited Series", ": Volume 1", ": Book 2", " Season 4"
_SEASON_SUFFIX = re.compile(
    r"(?:\s*[:\-–—]\s*|\s+)(?:(?:season|series|part|volume|vol\.?|chapter|book|collection)\s+[\w.]+"
    r"|limited\s+series|miniseries)\s*$",
    re.IGNORECASE,
)
_LABEL_ONLY = re.compile(
    r"^(?:(?:season|series|part|volume|vol\.?|chapter|book|collection)\s+[\w.]+|limited\s+series|miniseries"
    r"|s\d+\b.*)$",
    re.IGNORECASE,
)
_FIRST_SEASON = re.compile(r"^(?:(?:season|series|part|volume|collection|book|chapter)\s+1|limited\s+series)$", re.IGNORECASE)


def clean_title(title):
    """Return a title without a trailing season, part or limited-series label."""
    text = str(title or "").strip()
    while True:
        stripped = _SEASON_SUFFIX.sub("", text).strip()
        if stripped == text or not stripped:
            return text
        text = stripped


def season_label(show_title, season_title):
    """Return what ``season_title`` adds to the show ("Season 4", "Limited Series")."""
    season = str(season_title or "").strip()
    if season in NONE:
        return ""
    show = str(show_title or "").strip()
    if show and season.casefold().startswith(show.casefold()):
        rest = season[len(show) :].lstrip(" :-–—").strip()
        return rest or ""
    return season


def tv_titles(show_title, season_title):
    """Return the titles to try on TMDB for a TV row, best first.

    The show title without its season label, then a series name that only
    ``season_title`` carries ("DAHMER" / "DAHMER: Monster: The Jeffrey Dahmer Story"),
    then ``season_title`` itself ("Stranger Things 4"). Never the main title alone:
    "Instadocs: AI Gone Wild" is not "Instadocs".
    """
    show = clean_title(show_title)
    titles = [show]
    label = season_label(show_title, season_title)
    if label and not _LABEL_ONLY.match(label):
        titles.append(clean_title(label))
    season = str(season_title or "").strip()
    if season not in NONE:
        titles.append(clean_title(season))
    return [title for title in dict.fromkeys(titles) if title]


def _int(value):
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def as_utf8(text):
    """Undo requests' Latin-1 decoding: Netflix serves UTF-8 without a charset."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def parse_tsv(text, file):
    """Return the TSV rows as dicts, checking the columns this adapter reads."""
    text = as_utf8(text)
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    missing = COLUMNS[file] - set(reader.fieldnames or ())
    if missing:
        msg = f"Netflix {FILES[file]} is missing columns {sorted(missing)}."
        raise AdapterError(msg)
    return list(reader)


def latest_weeks(rows):
    """Keep each category's latest week only: all the weekly lists read."""
    latest = {}
    for row in rows:
        category = row.get("category")
        latest[category] = max(latest.get(category, ""), row.get("week") or "")
    return [row for row in rows if row.get("week") == latest.get(row.get("category"))]


def file_rows(file):
    """Return one file's rows, downloading it at most once per ``CACHE_TTL``.

    The weekly file is trimmed to each category's latest week before caching.
    """
    cache_key = f"{CACHE_PREFIX}{file}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    rows = parse_tsv(get_text("netflix", BASE_URL + FILES[file]), file)
    if file == "weekly":
        rows = latest_weeks(rows)
    if rows:
        cache.set(cache_key, rows, CACHE_TTL)
    return rows


def edition_label(week):
    """Return "Week of Oct 12, 2026" for a week ending ``week`` (a Sunday, ISO)."""
    try:
        start = date.fromisoformat(week) - timedelta(days=6)
    except (TypeError, ValueError):
        return ""
    return f"Week of {start:%b} {start.day}, {start.year}"


def _categories(params):
    if params.get("file") == "weekly":
        return [params.get("category")]
    return list(params.get("categories") or ())


def validate(definition):
    """Check the params without network."""
    params = definition.params
    file = params.get("file")
    if file not in FILES:
        msg = f"file must be one of {sorted(FILES)}"
        raise ValueError(msg)
    if file == "weekly" and "categories" in params:
        msg = "weekly lists take one category"
        raise ValueError(msg)
    categories = _categories(params)
    if not categories or any(category not in CATEGORIES for category in categories):
        msg = f"categories must be among {sorted(CATEGORIES)}"
        raise ValueError(msg)
    media_types = {CATEGORIES[category] for category in categories}
    if not media_types <= set(definition.media_types):
        msg = f"the categories hold {sorted(media_types)}, so media_types must include them"
        raise ValueError(msg)
    _rows.validate(definition)


def _entry(definition, rank, row, max_year):
    category = row["category"]
    media_type = CATEGORIES[category]
    show, season = row.get("show_title") or "", row.get("season_title") or ""
    entry = Entry(
        rank=rank,
        title=clean_title(show),
        media_type=media_type,
        note=season_label(show, season) if media_type == "tv" else "",
    )
    titles = tv_titles(show, season) if media_type == "tv" else [show, clean_title(show)]
    english = "(English)" in category  # Netflix splits by original language
    recent_since = max_year - 2 if max_year and _FIRST_SEASON.match(entry.note) else None
    return with_tmdb_match(
        definition,
        entry,
        titles,
        max_year=max_year,
        english=english,
        recent_since=recent_since,
    )


def _weekly(definition, rows):
    category = definition.params["category"]
    rows = [row for row in rows if row.get("category") == category]
    if not rows:
        msg = f"Netflix has no weekly rows for {category!r}."
        raise AdapterError(msg)
    week = max(row["week"] for row in rows)
    rows = sorted((row for row in rows if row["week"] == week), key=lambda row: _int(row["weekly_rank"]) or 99)
    _rows.require(definition, len(rows), f"Netflix's {category} week of {week}")
    year = int(week[:4]) if week[:4].isdigit() else None
    entries = [
        _entry(definition, _int(row["weekly_rank"]), row, year + 1 if year else None)
        for row in rows
    ]
    return FetchResult(entries=entries, edition=edition_label(week))


def _most_popular(definition, rows):
    categories = _categories(definition.params)
    rows = [row for row in rows if row.get("category") in categories]
    if not rows:
        msg = f"Netflix has no most-popular rows for {categories}."
        raise AdapterError(msg)
    for category in categories:
        _rows.require(definition, sum(row["category"] == category for row in rows), f"Netflix's most-popular {category}")
    rows.sort(key=lambda row: -(_int(row["views_first_91_days"]) or 0))
    entries, seen = [], set()
    for row in rows:
        entry = _entry(definition, len(entries) + 1, row, None)
        if entry.key in seen:  # another season of a show already listed
            continue
        seen.add(entry.key)
        entries.append(entry)
    return FetchResult(entries=entries)


def fetch(definition):
    """Read the (shared) TSV and build one list."""
    file = definition.params["file"]
    rows = file_rows(file)
    return _weekly(definition, rows) if file == "weekly" else _most_popular(definition, rows)


ADAPTER = Adapter(key="netflix", fetch=fetch, validate=validate)
