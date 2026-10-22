"""Awards and curated lists read from Wikipedia tables.

Each page is fetched through the MediaWiki API and its ``wikitable`` tables are read row
by row, with ``rowspan`` and ``colspan`` expanded so a year cell spanning a ceremony's
nominees applies to each of them.

A page is read as its newest revision that is at least ``STABLE_AGE`` (30 minutes) old:
one ``prop=revisions`` request finds it, then ``action=parse`` renders that revision. A
vandal edit is usually reverted within minutes, so one present at sync time can't stick
for the list's whole cadence. A page with no revision that old (it was just created)
defers the run until it has one. Live award pages (``award_page``) have their own rule.

Params:

- ``pages``: page titles, read in order. ``{year}`` is replaced by the current year;
  with ``"fallback_previous_year": True`` a missing or empty page falls back to the
  previous year's (a January chart page may not exist yet).
- ``site``: the Wikipedia language edition, default ``"en"``.
- ``columns``: header patterns (case-insensitive, matched against the whole header):
  ``title`` (required, or ``titles`` below), ``year``, ``creator``, ``rank``,
  ``category``, ``result``, ``date``.
- ``titles``: several title columns in one row, ``[{"header": "Men ver.", "label":
  "Men's"}, …]``; each yields its own entry with the label in its note.
- ``tables``: which tables to read: ``heading`` and ``caption`` patterns (searched) and
  ``index`` (a list of positions among the page's wikitables). By default every
  wikitable whose header row has the configured columns.
- ``winners``: ``"only"`` keeps winners, for tables that list nominees too (a
  ``result`` column reading "Won"/"Winner", else a bold title, ``‡``, or a winner
  background); ``"all"`` (default) keeps every row.
- ``category``: a pattern the category cell must match.
- ``latest_only``: keep only the rows of the latest year (a yearly top 10).
- ``last_year``: a year cell naming several years ("2021 / 2022" for a season that
  spans two) gives its last one instead of its first.
- ``order``: ``"newest"`` (default: newest year first) or ``"source"``.
- ``match_year``: send the year to the resolver (default true). Turn it off when the
  award year isn't the work's year (a TV series winning in its fifth season).
- ``split_creator``: a separator such as ``" by "`` when the title cell also names
  the creator ("Pluto by Naoki Urasawa").
- ``main_title``: keep only the title before ": " ("Master of the Senate: The Years of
  Lyndon Johnson"): providers often store books without the subtitle, and the strict
  title search compares the whole entry title.
- ``strip_parenthetical``: drop a trailing "(Season 2)" or "(Dark Horse Comics)"
  from titles (off by default: book titles end in parentheses too).
- ``min_entries``: fail when fewer rows parse, so a reshaped page never shrinks a list.
- ``musicbrainz_search``: music rows also get a strict MusicBrainz search by title and
  artist (albums and EPs only); its one result wins over an unconfirmed Wikidata ID.
- ``mangaupdates_search``: manga rows without Wikidata IDs try MangaUpdates, accepting
  only one series whose matched name equals the title exactly (Japanese titles too).

Rows naming the same work (a series winning three times) become one entry whose note
lists every year. Linked titles get provider IDs through Wikidata
(``_wikipedia_ids``); the rest go to the resolver's strict title search.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote

from bs4 import BeautifulSoup
from django.utils import timezone

from app.providers import quota
from lists.featured.adapters import _wikipedia_ids as wikidata
from lists.featured.helpers import get_json
from lists.featured.text import normalize_title, parse_year
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

COLUMN_NAMES = ("title", "year", "creator", "rank", "category", "result", "date")
WINNER_RESULT = re.compile(r"^\s*(won|winner)", re.IGNORECASE)
# Row or cell backgrounds Wikipedia's award tables use for winners.
WINNER_BACKGROUNDS = ("#faeb86", "#99ff99", "lemonchiffon", "lightyellow", "#eedd82", "#b0c4de")
WINNER_MARK = "‡"
# Footnote marks before or after a title; "M*A*S*H" keeps its stars.
SYMBOLS = re.compile(r"^[\s‡†*§¤#^]+|[\s‡†*§¤#^]+$")
BRACKETED_NOTE = re.compile(r"\[\s*[^\]]{0,12}\s*\]")
TRAILING_PARENTHETICAL = re.compile(r"\s*\([^()]*\)\s*$")
YEARS = re.compile(r"(?<!\d)(1[5-9]\d\d|2[01]\d\d)(?!\d)")  # as text.parse_year reads them
EMPTY_TITLES = {"", "-", "—", "–", "n/a", "tba", "tbd", "none", "not awarded", "no award"}
MAX_SPAN = 500
STABLE_AGE = timedelta(minutes=30)  # read the newest revision at least this old
SITE = re.compile(r"^[a-z]{2,3}$")
# Links into these namespaces are never the work itself (ja: 特別 is Special).
NAMESPACES = {"File", "Image", "Help", "Wikipedia", "Category", "Template", "Special", "Portal", "特別", "ファイル"}


@dataclass
class Row:
    """One work read from a table, before IDs are attached."""

    title: str
    year: int | None = None
    creator: str = ""
    rank: int | None = None
    link: str = ""  # linked article title
    label: str = ""
    date: str = ""
    notes: list = field(default_factory=list)


# Params ---------------------------------------------------------------------------


def _patterns(definition):
    params = definition.params
    columns = dict(params.get("columns") or {})
    titles = [dict(item) for item in params.get("titles") or []]
    return columns, titles


def validate(definition):
    """Check the params without network."""
    params = definition.params
    pages = params.get("pages")
    if not pages or not isinstance(pages, (list, tuple)) or not all(isinstance(p, str) and p for p in pages):
        msg = "pages must be a non-empty list of page titles"
        raise ValueError(msg)
    if not SITE.match(str(params.get("site", "en"))):
        msg = "site must be a Wikipedia language code"
        raise ValueError(msg)
    columns, titles = _patterns(definition)
    unknown = set(columns) - set(COLUMN_NAMES)
    if unknown:
        msg = f"unknown columns: {sorted(unknown)}"
        raise ValueError(msg)
    if not columns.get("title") and not titles:
        msg = "columns.title or titles is required"
        raise ValueError(msg)
    if columns.get("title") and titles:
        msg = "use columns.title or titles, not both"
        raise ValueError(msg)
    for item in titles:
        if not item.get("header"):
            msg = "every titles entry needs a header"
            raise ValueError(msg)
    if params.get("winners", "all") not in ("only", "all"):
        msg = "winners must be 'only' or 'all'"
        raise ValueError(msg)
    if params.get("order", "newest") not in ("newest", "source"):
        msg = "order must be 'newest' or 'source'"
        raise ValueError(msg)
    tables = dict(params.get("tables") or {})
    if set(tables) - {"heading", "caption", "index"}:
        msg = "tables takes heading, caption and index"
        raise ValueError(msg)
    if "index" in tables and not all(isinstance(value, int) for value in tables["index"]):
        msg = "tables.index must be a list of integers"
        raise ValueError(msg)
    for pattern in [
        *columns.values(),
        *(item["header"] for item in titles),
        tables.get("heading", ""),
        tables.get("caption", ""),
        params.get("category", ""),
    ]:
        try:
            re.compile(pattern)
        except re.error as error:
            msg = f"invalid pattern {pattern!r}: {error}"
            raise ValueError(msg) from None
    if len(definition.media_types) != 1 or definition.media_types[0] not in wikidata.PROPERTIES:
        msg = "a Wikipedia table list holds exactly one media type"
        raise ValueError(msg)
    if params.get("mangaupdates_search") and definition.media_types != ("manga",):
        msg = "mangaupdates_search is for manga lists"
        raise ValueError(msg)
    if params.get("musicbrainz_search") and definition.media_types != ("music",):
        msg = "musicbrainz_search is for music lists"
        raise ValueError(msg)


# HTML -----------------------------------------------------------------------------


def _clean_text(text):
    text = BRACKETED_NOTE.sub(" ", text)
    text = " ".join(text.split())
    text = re.sub(r"\(\s+", "(", text)
    return re.sub(r"\s+([),.:;!?])", r"\1", text).strip()


def cell_text(cell):
    """Return a cell's visible text, without footnotes."""
    if cell is None:
        return ""
    return _clean_text(cell.get_text(""))


def _prepare(soup):
    """Drop footnotes, hidden sort keys and edit links; turn line breaks into spaces."""
    for node in soup.select("sup, style, .mw-editsection, .sortkey, .reference"):
        node.decompose()
    for node in soup.select("[style]"):
        if node.name and "display:none" in node.get("style", "").replace(" ", "").lower():
            node.decompose()
    for node in soup.find_all("br"):
        node.replace_with(" ")


def _span(cell, name):
    match = re.match(r"\s*(\d+)", str(cell.get(name) or "1"))
    return max(1, min(int(match.group(1)), MAX_SPAN)) if match else 1


def expand(table):
    """Return ``[(tr, [cell, …])]`` with row and column spans filled in."""
    carry = {}  # column -> [cell, rows left]
    grid = []
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue
        queue = list(tr.find_all(["td", "th"], recursive=False))
        row, column = [], 0
        while queue or any(position >= column for position in carry):
            if column in carry:
                cell, left = carry[column]
                row.append(cell)
                if left <= 1:
                    del carry[column]
                else:
                    carry[column][1] = left - 1
                column += 1
                continue
            if not queue:
                row.append(None)
                column += 1
                continue
            cell = queue.pop(0)
            rows = _span(cell, "rowspan")
            for _ in range(_span(cell, "colspan")):
                row.append(cell)
                if rows > 1:
                    carry[column] = [cell, rows - 1]
                column += 1
        grid.append((tr, row))
    return grid


def _header_index(grid):
    for position, (_, row) in enumerate(grid):
        cells = [cell for cell in row if cell is not None]
        if len({id(cell) for cell in cells}) >= 2 and all(cell.name == "th" for cell in cells):  # noqa: PLR2004
            return position
    return None


def _find_columns(header, columns, titles):
    """Return ``{name: index}`` (and ``titles`` as ``[(index, label)]``), or None."""
    texts = [cell_text(cell) for cell in header]
    found = {}
    for name, pattern in columns.items():
        for index, text in enumerate(texts):
            if re.fullmatch(pattern, text, re.IGNORECASE):
                found[name] = index
                break
        else:
            return None
    title_columns = []
    for item in titles:
        for index, text in enumerate(texts):
            if re.fullmatch(item["header"], text, re.IGNORECASE):
                title_columns.append((index, item.get("label", "")))
                break
        else:
            return None
    if not title_columns:
        title_columns = [(found["title"], "")]
    return found, title_columns


def _background(tag):
    style = (tag.get("style") or "").replace(" ", "").lower()
    match = re.search(r"background(?:-color)?:([^;]+)", style)
    return match.group(1) if match else ""


def is_winner(tr, cell, result_text=None):
    """Return whether a row marks its title as the winner."""
    if result_text is not None:
        return bool(WINNER_RESULT.match(result_text))
    if WINNER_MARK in cell.get_text(""):
        return True
    bold = cell.find("b")
    if bold is not None and bold.get_text(strip=True):
        return True
    return any(
        _background(tag) and any(color in _background(tag) for color in WINNER_BACKGROUNDS)
        for tag in (tr, cell)
    )


def _word_prefix(short, long):
    return long == short or long.startswith(f"{short} ")


def article_link(cell, title):
    """Return the article the title itself links to, or "".

    Only a link whose text is the title (or a word prefix of it, or the reverse) counts:
    the first link in "The Practice (Season 2)" may be the season's episode list, and in
    "Her by Tomoko Yamashita" it is the author. Red links, links to a section of another
    article and namespaces are skipped.
    """
    wanted = normalize_title(title)
    for anchor in cell.find_all("a", href=True):
        href = anchor["href"]
        if not href.startswith("/wiki/") or "#" in href or "new" in (anchor.get("class") or []):
            continue  # a red link, or a section of another article (an album in a discography)
        target = unquote(href[len("/wiki/") :]).replace("_", " ")
        if not target or target.split(":", 1)[0] in NAMESPACES:
            continue
        text = normalize_title(anchor.get_text(""))
        if text and wanted and (_word_prefix(text, wanted) or _word_prefix(wanted, text)):
            return target
    return ""


def clean_title(text, split_creator="", *, strip_parenthetical=False):
    """Return ``(title, creator)`` from a title cell's text."""
    creator = ""
    if split_creator and split_creator in text:
        text, creator = text.split(split_creator, 1)
    text = SYMBOLS.sub("", text)
    text = " ".join(text.split())
    while strip_parenthetical and TRAILING_PARENTHETICAL.search(text):  # "Succession (Season 2)"
        stripped = TRAILING_PARENTHETICAL.sub("", text).strip()
        if not stripped:
            break
        text = stripped
    text = text.strip(" \"'“”")
    return text, " ".join(SYMBOLS.sub("", creator).split())


def _table_matches(table, index, selector):
    if "index" in selector and index not in selector["index"]:
        return False
    if selector.get("caption"):
        caption = table.find("caption")
        if caption is None or not re.search(selector["caption"], cell_text(caption), re.IGNORECASE):
            return False
    if selector.get("heading"):
        heading = table.find_previous(["h2", "h3", "h4"])
        if heading is None or not re.search(selector["heading"], cell_text(heading), re.IGNORECASE):
            return False
    return True


def cell_year(text, *, last=False):
    """Return the first year in a cell's text, or its last when ``last``."""
    if not last:
        return parse_year(text)
    years = YEARS.findall(text or "")
    return int(years[-1]) if years else None


def _context_year(table):
    caption = table.find("caption")
    heading = table.find_previous(["h2", "h3", "h4"])
    for node in (caption, heading):
        year = parse_year(cell_text(node)) if node is not None else None
        if year:
            return year
    return None


def parse_rows(html, params):  # noqa: C901, PLR0912
    """Return every ``Row`` the configured tables hold, in source order."""
    soup = BeautifulSoup(html, "html.parser")
    _prepare(soup)
    columns = dict(params.get("columns") or {})
    titles = [dict(item) for item in params.get("titles") or []]
    selector = dict(params.get("tables") or {})
    category = re.compile(params["category"], re.IGNORECASE) if params.get("category") else None
    winners_only = params.get("winners", "all") == "only"
    split_creator = params.get("split_creator", "")
    strip_parenthetical = bool(params.get("strip_parenthetical"))
    main_title = bool(params.get("main_title"))
    last_year = bool(params.get("last_year"))
    rows, matched_tables = [], 0
    for index, table in enumerate(soup.select("table.wikitable")):
        if not _table_matches(table, index, selector):
            continue
        grid = expand(table)
        header_at = _header_index(grid)
        if header_at is None:
            continue
        found = _find_columns(grid[header_at][1], columns, titles)
        if found is None:
            continue
        matched_tables += 1
        found, title_columns = found
        group_year = _context_year(table) if "year" not in found else None
        for tr, row in grid[header_at + 1 :]:
            distinct = {id(cell) for cell in row if cell is not None}
            if len(distinct) == 1 and len(row) > 1:  # a group row: "2026", "Best Dramatic Show"
                group_year = cell_year(cell_text(row[0]), last=last_year) or group_year
                continue

            def cell_at(name, row=row):
                position = found.get(name)
                return row[position] if position is not None and position < len(row) else None

            year = cell_year(cell_text(cell_at("year")), last=last_year) if "year" in found else group_year
            if category and not category.search(cell_text(cell_at("category"))):
                continue
            result_cell = cell_at("result")
            for position, label in title_columns:
                cell = row[position] if position < len(row) else None
                if cell is None or cell is cell_at("year"):
                    continue
                if winners_only and not is_winner(
                    tr,
                    cell,
                    cell_text(result_cell) if "result" in found else None,
                ):
                    continue
                title, creator = clean_title(
                    cell_text(cell),
                    split_creator,
                    strip_parenthetical=strip_parenthetical,
                )
                if title.casefold() in EMPTY_TITLES:
                    continue
                if main_title and ": " in title:
                    title = title.split(": ", 1)[0].strip()
                rank_match = re.search(r"\d+", cell_text(cell_at("rank")))
                rows.append(
                    Row(
                        title=title,
                        year=year,
                        creator=creator or SYMBOLS.sub("", cell_text(cell_at("creator"))).strip(),
                        rank=int(rank_match.group()) if rank_match and "rank" in found else None,
                        link=article_link(cell, title),
                        label=label,
                        date=cell_text(cell_at("date")),
                    ),
                )
    return rows, matched_tables


# Fetch ----------------------------------------------------------------------------


def stable_revision(site, title, now=None):
    """Return the id of the newest revision of ``title`` at least ``STABLE_AGE`` old.

    Redirects are followed. None when the page doesn't exist; ``quota.Deferred`` when
    every revision is newer (the page was just created), so the run is retried later.
    """
    now = now or timezone.now()
    data = get_json(
        "wikipedia",
        wikidata.api_url(site),
        params={
            "action": "query",
            "prop": "revisions",
            "titles": title,
            "redirects": "1",
            "rvprop": "ids|timestamp",
            "rvlimit": "1",
            "rvdir": "older",
            "rvstart": (now - STABLE_AGE).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "format": "json",
            "formatversion": "2",
        },
    )
    query = data.get("query") if isinstance(data, dict) else None
    pages = query.get("pages") if isinstance(query, dict) else None
    if not isinstance(pages, list) or not pages or not isinstance(pages[0], dict):
        error = (data.get("error") or {}) if isinstance(data, dict) else {}
        code = error.get("code") if isinstance(error, dict) else None
        msg = f"Wikipedia could not list the revisions of {title!r}: {code or 'no pages'}."
        raise AdapterError(msg)
    page = pages[0]
    if page.get("missing"):
        return None
    if page.get("invalid"):
        msg = f"Wikipedia rejected the page title {title!r}."
        raise AdapterError(msg)
    revisions = page.get("revisions") or []
    if not revisions or not revisions[0].get("revid"):
        minutes = int(STABLE_AGE.total_seconds() // 60)
        raise quota.Deferred("wikipedia", now + STABLE_AGE, f"page {title!r} is under {minutes} minutes old")
    return revisions[0]["revid"]


def fetch_page(site, title, now=None):
    """Return ``(html, revid)`` for a page's stable revision, or ``(None, None)`` when it doesn't exist."""
    revid = stable_revision(site, title, now)
    if revid is None:
        return None, None
    data = get_json(
        "wikipedia",
        wikidata.api_url(site),
        params={
            "action": "parse",
            "oldid": str(revid),
            "prop": "text|revid",
            "disableeditsection": "1",
            "disablelimitreport": "1",
            "format": "json",
            "formatversion": "2",
        },
    )
    error = (data.get("error") or {}) if isinstance(data, dict) else {"code": "unreadable"}
    parsed = (data.get("parse") or {}) if isinstance(data, dict) else {}
    if error or not isinstance(parsed.get("text"), str):
        msg = f"Wikipedia could not parse {title!r}: {error.get('code') or 'no text'}."
        raise AdapterError(msg)
    return parsed["text"], parsed.get("revid")


def _merge(rows, params):
    """Order rows and fold repeats of one work into a single row."""
    if params.get("latest_only"):
        latest = max((row.year for row in rows if row.year), default=None)
        rows = [row for row in rows if row.year == latest]
    if params.get("order", "newest") == "newest":
        rows = sorted(reversed(rows), key=lambda row: -(row.year or 0))
    merged, by_title = [], {}
    for row in rows:
        note = " · ".join(part for part in (row.date or (str(row.year) if row.year else ""), row.label) if part)
        # The same work: the same title, and no two different links (Wikipedia often
        # links only a title's first appearance; two seasons share a title, not a link).
        title_key = (normalize_title(row.title), normalize_title(row.creator))
        existing = next(
            (
                candidate
                for candidate in by_title.get(title_key, [])
                if not (candidate.link and row.link and candidate.link != row.link)
            ),
            None,
        )
        if existing is not None:
            existing.link = existing.link or row.link
            if note and note not in existing.notes:
                existing.notes.append(note)
            continue
        row.notes = [note] if note else []
        by_title.setdefault(title_key, []).append(row)
        merged.append(row)
    return merged


def _names_article(row):
    """Return whether the row's title is its linked article's title (disambiguation aside)."""
    article = TRAILING_PARENTHETICAL.sub("", row.link).strip()
    title = TRAILING_PARENTHETICAL.sub("", row.title).strip()
    return normalize_title(article) == normalize_title(title)


def _entries(definition, rows):
    media_type = definition.media_types[0]
    site = definition.params.get("site", "en")
    links = [row.link for row in rows if row.link]
    qids = wikidata.wikidata_items(site, links) if links else {}
    ids_by_qid = wikidata.provider_ids(media_type, set(qids.values())) if qids else {}
    match_year = definition.params.get("match_year", True)
    by_title = {}
    if definition.params.get("mangaupdates_search"):
        missing = [row.title for row in rows if qids.get(row.link, "") not in ids_by_qid]
        by_title = wikidata.mangaupdates_by_title(missing) if missing else {}
    albums = {}
    if definition.params.get("musicbrainz_search"):
        albums = wikidata.musicbrainz_albums((row.title, row.creator) for row in rows)
    entries = []
    for row in rows:
        qid = qids.get(row.link, "")
        ids, image = ids_by_qid.get(qid) or by_title.get(row.title) or ({}, "")
        if media_type == "anime" and ids and not _names_article(row):
            # Spine stores anime per season, and a series article's MAL ID is its first
            # season's: "Attack on Titan: The Final Season" linking "Attack on Titan".
            ids, image = {}, ""
        found = albums.get((row.title, row.creator)) or []
        # Wikidata's P436 sometimes holds a release, not its release group: an ID the
        # strict search doesn't confirm gives way to the one album it does find.
        if len(found) == 1 and ids.get("mbid_rg") not in found:
            ids, image = {"mbid_rg": found[0]}, ""
        entries.append(
            Entry(
                rank=row.rank if definition.is_ranked else None,
                title=row.title,
                year=row.year if match_year else None,
                creator=row.creator,
                media_type=media_type,
                ids={**ids, "wikidata": qid},
                image=image,
                note=", ".join(row.notes),
            ),
        )
    return entries


def _page_titles(params, year):
    return [title.replace("{year}", str(year)) for title in params["pages"]]


def fetch(definition):
    """Fetch every configured page and return its rows as entries."""
    params = definition.params
    site = params.get("site", "en")
    year = datetime.now(tz=UTC).year
    attempts = [year, year - 1] if params.get("fallback_previous_year") else [year]
    rows, edition_year = [], None
    for attempt in attempts:
        rows, tables = [], 0
        for title in _page_titles(params, attempt):
            html, _ = fetch_page(site, title)
            if html is None:
                continue
            page_rows, page_tables = parse_rows(html, params)
            rows += page_rows
            tables += page_tables
        if rows:
            edition_year = attempt
            break
    if not rows:
        msg = f"No rows matched in {', '.join(_page_titles(params, attempts[-1]))}; the page layout may have changed."
        raise AdapterError(msg)
    minimum = params.get("min_entries", 1)
    if len(rows) < minimum:
        msg = f"Only {len(rows)} rows parsed (expected at least {minimum}); the page layout may have changed."
        raise AdapterError(msg)
    rows = _merge(rows, params)
    latest = max((row.year for row in rows if row.year), default=None)
    if "{year}" in "".join(params["pages"]):
        edition = str(edition_year)
    elif params.get("latest_only") and latest:
        edition = str(latest)
    else:
        edition = f"Through {latest}" if latest else ""
    return FetchResult(entries=_entries(definition, rows), edition=edition)


ADAPTER = Adapter(key="wikipedia_table", fetch=fetch, validate=validate)
