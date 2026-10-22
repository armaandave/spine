"""Nielsen's Streaming Top 10, from the server-rendered nielsen.com/data-center/top-ten/.

Params: ``{"chart": "overall"}`` (movies and shows, US streaming minutes) or
``{"chart": "movies"}``. The page holds several tables; the streaming ones have a
"Streaming Provider(s)" column. Overall is the first with a "# of Episodes" column,
Movies the one without. Nielsen publishes about two and a half weeks behind; the
edition is the week shown above the table ("Week of Sep 28, 2026").

Titles only, matched to TMDB here (``_tmdb_match``): "East of Eden (2026)" searches
"East of Eden" within a year of 2026. On the overall chart a title with more than
one episode is a show; otherwise it tries movies, then shows.
"""

import re
from html.parser import HTMLParser

from lists.featured.adapters._tmdb_match import best_match, with_tmdb_match
from lists.featured.helpers import get_text
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

URL = "https://www.nielsen.com/data-center/top-ten/"
CHARTS = {"overall": ("movie", "tv"), "movies": ("movie",)}
PROVIDER_HEADER = "streaming provider(s)"
EPISODES_HEADER = "# of episodes"
_YEAR_SUFFIX = re.compile(r"\s*\((?P<year>(?:19|20)\d\d)\)$")
_WEEK = re.compile(r"(?P<month>[A-Z][a-z]+)\.?\s+(?P<day>\d{1,2})\s*[-–]\s*[A-Z][a-z]+\.?\s+\d{1,2},\s*(?P<year>\d{4})")


class _Tables(HTMLParser):
    """Collect every table's rows (lists of cell texts) and the text just before it."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []  # [{"before": str, "rows": [[str, …], …]}]
        self._recent = []
        self._table = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):  # noqa: ARG002
        if tag == "table" and self._table is None:
            self._table = {"before": " ".join(self._recent[-12:]), "rows": []}
        elif self._table is not None and tag == "tr":
            self._row = []
        elif self._row is not None and tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self._table["rows"].append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table, self._recent = None, []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        elif self._table is None and data.strip():
            self._recent.append(data.strip())


def streaming_tables(page):
    """Return ``{"overall": table, "movies": table}`` from the page's HTML."""
    parser = _Tables()
    parser.feed(page)
    found = {}
    for table in parser.tables:
        header = [cell.casefold() for cell in (table["rows"] or [[]])[0]]
        if PROVIDER_HEADER not in header:
            continue
        chart = "overall" if EPISODES_HEADER in header else "movies"
        found.setdefault(chart, table)
    return found


def edition_label(text):
    """Return "Week of Sep 28, 2026" from "Sep. 28 – Oct. 04, 2026"."""
    match = _WEEK.search(text or "")
    if not match:
        return ""
    return f"Week of {match['month']} {int(match['day'])}, {match['year']}"


def validate(definition):
    """Check the params without network."""
    chart = definition.params.get("chart")
    if chart not in CHARTS:
        msg = f"chart must be one of {sorted(CHARTS)}"
        raise ValueError(msg)
    if set(definition.media_types) != set(CHARTS[chart]):
        msg = f"the {chart} chart holds {CHARTS[chart]}"
        raise ValueError(msg)


def _int(value):
    digits = re.sub(r"[^\d]", "", str(value or ""))
    return int(digits) if digits else None


def _entry(definition, chart, rank, title, episodes):
    year = None
    match = _YEAR_SUFFIX.search(title)
    if match:
        year = int(match["year"])
        title = title[: match.start()].strip()
    bounds = {"min_year": year - 1, "max_year": year + 1} if year else {}
    if chart == "movies" or (episodes or 0) > 1:
        media_type = "movie" if chart == "movies" else "tv"
        entry = Entry(rank=rank, title=title, year=year, media_type=media_type)
        return with_tmdb_match(definition, entry, [title], **bounds)
    for media_type in ("movie", "tv"):  # one episode: a film, or a special
        entry = Entry(rank=rank, title=title, year=year, media_type=media_type)
        if best_match(media_type, [title], **bounds):
            return with_tmdb_match(definition, entry, [title], **bounds)
    return Entry(rank=rank, title=title, year=year)


def fetch(definition):
    """Fetch the page and parse one chart."""
    chart = definition.params["chart"]
    table = streaming_tables(get_text("nielsen", URL)).get(chart)
    if table is None or len(table["rows"]) < 2:  # noqa: PLR2004
        msg = f"Nielsen's page has no streaming {chart} table."
        raise AdapterError(msg)
    header = [cell.casefold() for cell in table["rows"][0]]
    try:
        title_at, rank_at = header.index("program name"), header.index("rank")
    except ValueError:
        msg = f"Nielsen's {chart} table has unexpected columns {header}."
        raise AdapterError(msg) from None
    episodes_at = header.index(EPISODES_HEADER) if EPISODES_HEADER in header else None
    entries = []
    for row in table["rows"][1:]:
        if len(row) <= max(title_at, rank_at) or not row[title_at]:
            continue
        episodes = _int(row[episodes_at]) if episodes_at is not None and len(row) > episodes_at else None
        entries.append(_entry(definition, chart, _int(row[rank_at]), row[title_at], episodes))
    if not entries:
        msg = f"Nielsen's {chart} table is empty."
        raise AdapterError(msg)
    return FetchResult(entries=entries, edition=edition_label(table["before"]))


ADAPTER = Adapter(key="nielsen", fetch=fetch, validate=validate)
