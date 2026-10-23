"""Award winners and collections from Wikidata, one SPARQL query per list.

Params (``mode`` picks the query):

- ``{"mode": "award", "awards": ["Q102427"]}``: every win of one of these awards,
  newest first, each with the note ``"<year>"`` or ``"<year> · tie"``; a work that
  won in two years is two entries. Winners
  come from the work (``P166`` with ``P585`` for the year and ``P805`` for the
  ceremony). With ``"author_statements": True`` (book lists), a work with no
  statement of its own can also come from its author's statement's ``P1686`` ("for
  work"); film people's statements are too often a Silver Lion or a foreign-language
  BAFTA recorded as the main prize to trust. Humans are excluded and the work must be an
  instance of one of the media type's classes, checked with a second small, cached
  query (``classes`` overrides them; ``exclude_classes`` drops, say, short films).
- ``{"mode": "criterion"}``: the Criterion Collection by spine number (``P12279``),
  ranked by spine number.
- ``{"mode": "registry", "part_of": "Q823422"}``: works that are part of a
  collection (the National Film Registry), newest induction (``P580``) first, with
  "Inducted <year>" as the note.

Wikidata lags: ``static/supplements/wikidata.csv`` adds verified winners it lacks and
fixes years it has wrong. Columns ``list,year,qid,title,creator,published,ref,why``: a
row with a ``qid`` sets that work's award years for the list (its ids still come
from Wikidata); a row without one adds an entry from ``title``/``creator``/
``published`` (or its ``ref``); a row whose ``ref`` is ``-`` drops its ``qid`` from
the list (an Honor book recorded as the Medal, a duplicate item).

Ids per media type: TMDB (``P4947`` movies, ``P4983`` TV) and IMDb (``P345``); IGDB's
numeric id (the ``P9043`` qualifier on ``P5794``); MusicBrainz release groups
(``P436``); MyAnimeList (``P4086``/``P4087``). Books carry Open Library *work* ids
(``P648``), so the adapter turns them into a Hardcover book (Hardcover's own Open
Library mappings, title-checked) or else an Open Library edition (the work's
editions endpoint). A book with no Open Library id is looked up by title *and*
author on Open Library's search first, since generic titles ("Selected Poems")
never surface in a title-only search. Goodreads work ids (``P8383``) are not used: Hardcover maps
Goodreads *book* ids, a different number space, so they match the wrong books.
"""

import csv
import hashlib
import json
import logging
import re
from collections import defaultdict
from dataclasses import replace
from functools import cache as memoize
from pathlib import Path

from django.conf import settings
from django.core.cache import cache

from lists.featured.helpers import get_json, post_json
from lists.featured.resolve import parse_ref
from lists.featured.text import creators_match, normalize_title, title_forms
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

logger = logging.getLogger(__name__)

SPARQL_URL = "https://query.wikidata.org/sparql"
HARDCOVER_URL = "https://api.hardcover.app/v1/graphql"
OPENLIBRARY_URL = "https://openlibrary.org"
SUPPLEMENT = Path(__file__).resolve().parent.parent / "static" / "supplements" / "wikidata.csv"
SUPPLEMENT_COLUMNS = ["list", "year", "qid", "title", "creator", "published", "ref", "why"]

# Bump when the query or the parsing changes, so every Wikidata list re-syncs.
QUERY_VERSION = "4"
SPARQL_TTL = 60 * 60 * 12
LOOKUP_HIT_TTL = 60 * 60 * 24 * 30
LOOKUP_MISS_TTL = 60 * 60 * 24
SPARQL_TIMEOUT = 90
HARDCOVER_BATCH = 200
SEARCH_YEAR_TOLERANCE = 2
OPENLIBRARY_PLATFORM = 3  # Hardcover's platform id for Open Library mappings
CACHE_PREFIX = "featured:wikidata:"

QID = re.compile(r"^Q[1-9]\d*$")
MODES = ("award", "criterion", "registry")
TIE = "tie"
EXCLUDE = "-"
DISAMBIGUATOR = re.compile(r"\s*\((?:book|novel|poetry collection|memoir|nonfiction book|\d{4} book)\)$", re.IGNORECASE)

CLASSES = {
    "movie": ("Q11424",),  # film
    "tv": ("Q5398426",),  # television series
    # literary work, written work, book, edition; Wikidata types some poetry
    # collections only as "creative work" or "work"
    "book": ("Q7725634", "Q47461344", "Q571", "Q3331189", "Q17537576", "Q386724"),
    "game": ("Q7889",),  # video game
    "music": ("Q482994",),  # album
    "anime": ("Q1107", "Q581714", "Q20650540"),  # anime, animated series, anime film
    "manga": ("Q8274", "Q21198342"),  # manga, manga series
}
CREATORS = {
    "movie": "P57",  # director
    "tv": "P170",  # creator
    "book": "P50",  # author
    "game": "P178",  # developer
    "music": "P175",  # performer
    "anime": "P272",  # production company
    "manga": "P50",
}
# column -> (media types that use it, triple pattern)
ID_COLUMNS = {
    "tmdb_movie": (("movie",), "?w wdt:P4947 ?tmdb_movie ."),
    "tmdb_tv": (("tv",), "?w wdt:P4983 ?tmdb_tv ."),
    "imdb": (("movie", "tv"), "?w wdt:P345 ?imdb ."),
    "igdb": (("game",), "?w p:P5794 ?igdb_statement . ?igdb_statement pq:P9043 ?igdb ."),
    "olw": (("book",), "?w wdt:P648 ?olw ."),
    "mbid_rg": (("music",), "?w wdt:P436 ?mbid_rg ."),
    "mal_anime": (("anime",), "?w wdt:P4086 ?mal_anime ."),
    "mal_manga": (("manga",), "?w wdt:P4087 ?mal_manga ."),
}


# Validation ---------------------------------------------------------------------


def _qids(values, name):
    values = list(values or ())
    bad = [value for value in values if not QID.match(str(value))]
    if bad:
        msg = f"{name} must be Wikidata item ids (Q…), not {bad}"
        raise ValueError(msg)
    return values


def validate(definition):
    """Check the params and this list's supplement rows without network."""
    params = definition.params
    mode = params.get("mode")
    if mode not in MODES:
        msg = f"mode must be one of {MODES}"
        raise ValueError(msg)
    unsupported = set(definition.media_types) - set(CLASSES)
    if unsupported:
        msg = f"Wikidata lists can't hold {sorted(unsupported)}"
        raise ValueError(msg)
    if mode == "award" and not _qids(params.get("awards"), "awards"):
        msg = "award lists need at least one award in awards"
        raise ValueError(msg)
    if mode in ("criterion", "registry") and set(definition.media_types) != {"movie"}:
        msg = f"{mode} lists hold movies"
        raise ValueError(msg)
    if mode == "registry":
        _qids([params.get("part_of")], "part_of")
    _qids(params.get("classes"), "classes")
    _qids(params.get("exclude_classes"), "exclude_classes")
    if mode == "award" and definition.is_ranked:
        msg = "award lists are unranked (newest first)"
        raise ValueError(msg)
    supplement_rows(definition.key)


# Supplement ---------------------------------------------------------------------


def _int(value, column, line):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        msg = f"supplements/wikidata.csv:{line}: {column} must be a whole number"
        raise ValueError(msg) from None


@memoize
def _supplement(path=None):
    path = Path(path or SUPPLEMENT)
    if not path.exists():
        return ()
    rows = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != SUPPLEMENT_COLUMNS:
            msg = f"supplements/wikidata.csv: header must be {','.join(SUPPLEMENT_COLUMNS)}"
            raise ValueError(msg)
        for line, raw in enumerate(reader, start=2):
            row = {name: (raw[name] or "").strip() for name in SUPPLEMENT_COLUMNS}
            row["line"] = line
            row["year"] = _int(row["year"], "year", line)
            row["published"] = _int(row["published"], "published", line)
            where = f"supplements/wikidata.csv:{line}"
            if not row["list"] or row["year"] is None:
                msg = f"{where}: list and year are required"
                raise ValueError(msg)
            if row["qid"] and not QID.match(row["qid"]):
                msg = f"{where}: qid must look like Q123"
                raise ValueError(msg)
            if not row["qid"] and not row["title"]:
                msg = f"{where}: a row without a qid needs a title"
                raise ValueError(msg)
            if row["ref"] == EXCLUDE and not row["qid"]:
                msg = f"{where}: an exclusion (ref -) needs a qid"
                raise ValueError(msg)
            if row["ref"] and row["ref"] != EXCLUDE:
                parse_ref(row["ref"])
            rows.append(row)
    return tuple(rows)


def supplement_rows(list_key):
    """Return the supplement rows for one list, exclusions included."""
    return [row for row in _supplement() if row["list"] == list_key]


def _split(supplement):
    """Return ``(rows that add or date works, excluded QIDs)``."""
    excluded = {row["qid"] for row in supplement if row["ref"] == EXCLUDE}
    return [row for row in supplement if row["ref"] != EXCLUDE], excluded


def version(definition):
    """Return a hash of the query version and this list's supplement rows."""
    rows = [
        {key: value for key, value in row.items() if key != "line"}
        for row in supplement_rows(definition.key)
    ]
    payload = json.dumps([QUERY_VERSION, rows], sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


# SPARQL -------------------------------------------------------------------------


def _values(qids):
    return " ".join(f"wd:{qid}" for qid in qids)


def _classes(definition):
    params = definition.params
    return params.get("classes") or [qid for media in definition.media_types for qid in CLASSES[media]]


def _class_filter(definition):  # noqa: ARG001
    """Return the pattern every work must pass inside the query: not a human.

    The class check happens afterwards (``kept_types``): a transitive ``P279*`` path
    inside the main query makes Blazegraph walk down from "film" or "written work"
    and time out.
    """
    return "FILTER NOT EXISTS { ?w wdt:P31 wd:Q5 }"


def kept_types(types, classes):
    """Return the subset of ``types`` that are (subclasses of) one of ``classes``.

    One small query walks up from the given types; answers are cached for 30 days.
    """
    types = sorted({value for value in types if QID.match(value)})
    classes = sorted(set(classes))
    scope = hashlib.sha256(" ".join(classes).encode()).hexdigest()[:16]
    found, unknown = set(), []
    for value in types:
        cached = cache.get(f"{CACHE_PREFIX}type:{scope}:{value}")
        if cached is None:
            unknown.append(value)
        elif cached:
            found.add(value)
    if unknown:
        query = f"""SELECT DISTINCT ?type WHERE {{
  VALUES ?type {{ {_values(unknown)} }}
  VALUES ?class {{ {_values(classes)} }}
  ?type wdt:P279* ?class .
}}"""
        matched = {qid_of(row.get("type")) for row in sparql(query)}
        for value in unknown:
            cache.set(f"{CACHE_PREFIX}type:{scope}:{value}", value in matched, LOOKUP_HIT_TTL)
        found |= matched & set(unknown)
    return found


def filter_by_class(definition, works, keep=()):
    """Drop works that aren't of the list's classes (or are of an excluded class).

    ``keep`` (supplement QIDs) always stays.
    """
    types = {value for work in works for value in work["types"]}
    allowed = kept_types(types, _classes(definition))
    excluded_classes = definition.params.get("exclude_classes")
    excluded = kept_types(types, excluded_classes) if excluded_classes else set()
    kept = []
    for work in works:
        if work["qid"] in keep or (set(work["types"]) & allowed and not set(work["types"]) & excluded):
            kept.append(work)
        else:
            logger.info("Wikidata %s: %s (%s) is not of this list's classes", definition.key, work["qid"], work["title"])
    return kept


def _core(definition, extra_qids):
    """Return the inner pattern binding ``?w`` and ``?win`` ("side|year|ceremony|number")."""
    params = definition.params
    mode = params["mode"]
    classes = _class_filter(definition)
    if mode == "criterion":
        blocks = [
            f"""?w p:P12279 ?st . ?st ps:P12279 ?number .
      {classes}
      BIND(CONCAT("c||", "", "|", STR(?number)) AS ?win)""",
        ]
    elif mode == "registry":
        blocks = [
            f"""?w p:P361 ?st . ?st ps:P361 wd:{params["part_of"]} .
      {classes}
      OPTIONAL {{ ?st pq:P580 ?date }}
      BIND(CONCAT("r|", COALESCE(STR(YEAR(?date)), ""), "||") AS ?win)""",
        ]
    else:
        awards = _values(params["awards"])
        dated = """OPTIONAL { ?st pq:P585 ?statementDate }
      OPTIONAL { ?st pq:P805 ?ceremony . OPTIONAL { ?ceremony wdt:P585 ?ceremonyDate } }
      BIND(COALESCE(?statementDate, ?ceremonyDate) AS ?date)"""
        win = 'COALESCE(STR(YEAR(?date)), ""), "|", COALESCE(STRAFTER(STR(?ceremony), "/entity/"), ""), "|"'
        blocks = [
            f"""VALUES ?award {{ {awards} }}
      ?w p:P166 ?st . ?st ps:P166 ?award .
      {classes}
      {dated}
      BIND(CONCAT("w|", {win}) AS ?win)""",
        ]
        if params.get("author_statements"):
            blocks.append(
                f"""VALUES ?award {{ {awards} }}
      ?author p:P166 ?st . ?st ps:P166 ?award ; pq:P1686 ?w .
      ?author wdt:P31 wd:Q5 .
      FILTER NOT EXISTS {{ ?w wdt:P31 wd:Q5 }}
      {dated}
      BIND(CONCAT("a|", {win}) AS ?win)""",
            )
    if extra_qids:
        blocks.append(f'VALUES ?w {{ {_values(extra_qids)} }} BIND("s|||" AS ?win)')
    return "\n    } UNION {\n      ".join(blocks)


def build_query(definition, extra_qids=()):
    """Return the one SPARQL query for a definition."""
    media_types = definition.media_types
    columns = [name for name, (types, _) in ID_COLUMNS.items() if set(types) & set(media_types)]
    creator_props = sorted({CREATORS[media] for media in media_types})
    select_ids = " ".join(f"(SAMPLE(?{name}) AS ?{name}_)" for name in columns)
    optional_ids = "\n  ".join(f"OPTIONAL {{ {ID_COLUMNS[name][1]} }}" for name in columns)
    creators = " ".join(f"wdt:{prop}" for prop in creator_props)
    return f"""SELECT ?w (SAMPLE(?label) AS ?title) (GROUP_CONCAT(DISTINCT ?win; separator=";") AS ?wins)
  (MIN(?published) AS ?year) (GROUP_CONCAT(DISTINCT ?creatorName; separator="; ") AS ?creators)
  (GROUP_CONCAT(DISTINCT ?type; separator=";") AS ?types) {select_ids}
WHERE {{
  {{ SELECT DISTINCT ?w ?win WHERE {{ {{
      {_core(definition, extra_qids)}
  }} }} }}
  OPTIONAL {{ ?w rdfs:label ?enLabel FILTER(LANG(?enLabel) = "en") }}
  OPTIONAL {{ ?w rdfs:label ?mulLabel FILTER(LANG(?mulLabel) = "mul") }}
  BIND(COALESCE(?enLabel, ?mulLabel) AS ?label)
  OPTIONAL {{ ?w wdt:P31 ?type }}
  OPTIONAL {{ ?w wdt:P577 ?date . BIND(YEAR(?date) AS ?published) }}
  OPTIONAL {{ VALUES ?creatorProperty {{ {creators} }} ?w ?creatorProperty ?creator .
             ?creator rdfs:label ?creatorName FILTER(LANG(?creatorName) = "en") }}
  {optional_ids}
}}
GROUP BY ?w"""


def sparql(query, provider="wikidata"):
    """Run a SPARQL query (cached for 12 hours) and return its result bindings."""
    cache_key = f"{CACHE_PREFIX}sparql:{hashlib.sha256(query.encode()).hexdigest()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
    data = post_json(
        provider,
        SPARQL_URL,
        data={"query": query},
        headers={"Accept": "application/sparql-results+json"},
        timeout=SPARQL_TIMEOUT,
    )
    try:
        bindings = data["results"]["bindings"]
    except (KeyError, TypeError):
        msg = "Wikidata returned an unexpected SPARQL response."
        raise AdapterError(msg) from None
    rows = [
        {name: str(value.get("value", "")) for name, value in binding.items()}
        for binding in bindings
    ]
    cache.set(cache_key, rows, SPARQL_TTL)
    return rows


def qid_of(uri):
    """Return ``Q123`` from ``http://www.wikidata.org/entity/Q123``."""
    value = str(uri or "").rsplit("/", 1)[-1]
    return value if QID.match(value) else ""


# Rows -> works ------------------------------------------------------------------


def _wins(raw):
    """Parse ``?wins`` into ``[(side, year, ceremony, number)]``."""
    found = []
    for part in str(raw or "").split(";"):
        if not part:
            continue
        side, year, ceremony, number = ([*part.split("|"), "", "", ""])[:4]
        found.append((side, int(year) if year.isdigit() else None, ceremony, number))
    return found


def _creators(raw):
    return ", ".join(sorted({name.strip() for name in str(raw or "").split(";") if name.strip()}))


def _year(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _work(row):
    return {
        "qid": qid_of(row.get("w")),
        "title": row.get("title") or "",
        "year": _year(row.get("year")),
        "creator": _creators(row.get("creators")),
        "wins": _wins(row.get("wins")),
        "types": [qid_of(value) for value in str(row.get("types") or "").split(";") if qid_of(value)],
        "ids": {name[:-1]: value for name, value in row.items() if name.endswith("_") and value},
    }


def _media_type(definition, ids):
    media_types = definition.media_types
    if len(media_types) == 1:
        return media_types[0]
    if "tmdb_movie" in ids and "movie" in media_types:
        return "movie"
    if "tmdb_tv" in ids and "tv" in media_types:
        return "tv"
    return None


def _entry_ids(media_type, work):
    ids = work["ids"]
    found = {"wikidata": work["qid"]}
    if media_type == "movie" and ids.get("tmdb_movie"):
        found["tmdb"] = ids["tmdb_movie"]
    if media_type == "tv" and ids.get("tmdb_tv"):
        found["tmdb"] = ids["tmdb_tv"]
    if media_type in ("movie", "tv") and str(ids.get("imdb", "")).startswith("tt"):
        found["imdb"] = ids["imdb"]
    if media_type == "game" and str(ids.get("igdb", "")).isdigit():
        found["igdb"] = ids["igdb"]
    if media_type == "music" and ids.get("mbid_rg"):
        found["mbid_rg"] = ids["mbid_rg"]
    if media_type in ("anime", "manga") and ids.get(f"mal_{media_type}"):
        found["mal"] = ids[f"mal_{media_type}"]
    return found


def _award_years(work, override_years):
    """Return ``[(year, ceremony)]``: work-side wins, else author-side, else the supplement's."""
    if override_years:
        return [(year, "") for year in sorted(override_years)]
    wins = [win for win in work["wins"] if win[0] == "w" and win[1]]
    if not wins:
        wins = [win for win in work["wins"] if win[0] == "a" and win[1]]
    years = {}
    for _, year, ceremony, _ in wins:
        years.setdefault(year, ceremony)
    return sorted(years.items())


def tied_years(candidates):
    """Return the years with more than one winner of the same ceremony.

    Two winners in one year are a tie unless each names a different ceremony (the
    Academy held two ceremonies in 1930).
    """
    by_year = defaultdict(list)
    for years in candidates:
        for year, ceremony in years:
            by_year[year].append(ceremony)
    return {
        year
        for year, ceremonies in by_year.items()
        if len(ceremonies) > 1 and not (all(ceremonies) and len(set(ceremonies)) == len(ceremonies))
    }


def _note(year, ties):
    return f"{year} · {TIE}" if year in ties else str(year)


def _with_supplement_details(work, supplement):
    """Fill a work's missing year and add the supplement's spelling of its creator.

    Wikidata items added by the supplement often lack an author, or spell one
    differently from catalogs ("Leonard Levy" vs "Leonard W. Levy"); the strict
    title search accepts any one matching name.
    """
    rows = [row for row in supplement if row["qid"] and row["qid"] == work["qid"]]
    if not rows:
        return work
    names = [name for name in (work["creator"], *(row["creator"] for row in rows)) if name]
    creator = ", ".join(dict.fromkeys(names))
    year = work["year"] or next((row["published"] for row in rows if row["published"]), None)
    return {**work, "creator": creator, "year": year}


def _award_entries(definition, works, supplement):
    by_qid = defaultdict(set)
    for row in supplement:
        if row["qid"]:
            by_qid[row["qid"]].add(row["year"])
    candidates = []
    for work in works:
        work = _with_supplement_details(work, supplement)  # noqa: PLW2901
        media_type = _media_type(definition, work["ids"])
        years = _award_years(work, by_qid.get(work["qid"]))
        if not years:
            logger.info("Wikidata %s: %s has no award year; left out", definition.key, work["qid"])
            continue
        candidates.append((work, media_type, years))
    for row in supplement:
        if row["qid"]:
            continue
        ids = {"ref": row["ref"]} if row["ref"] else {}
        work = {"qid": "", "title": row["title"], "year": row["published"], "creator": row["creator"], "ids": ids}
        media_type = parse_ref(row["ref"])[1] if row["ref"] else _media_type(definition, {})
        candidates.append((work, media_type, [(row["year"], "")]))
    ties = tied_years(years for _, _, years in candidates)
    entries = []
    for work, media_type, years in candidates:
        ids = dict(work["ids"]) if not work["qid"] else _entry_ids(media_type, work)
        entries.extend(  # one entry per win: a work that won twice appears twice
            (
                year,
                Entry(
                    rank=None,
                    title=work["title"] or work["qid"],
                    year=work["year"],
                    creator=work["creator"],
                    media_type=media_type,
                    ids=ids,
                    note=_note(year, ties),
                ),
                work,
            )
            for year, _ in years
        )
    entries.sort(key=lambda item: (-item[0], normalize_title(item[1].title)))
    return [(entry, work) for _, entry, work in entries]


def _criterion_entries(definition, works):
    entries = []
    for work in works:
        numbers = sorted(int(number) for side, _, _, number in work["wins"] if side == "c" and number.isdigit())
        if not numbers:
            continue
        media_type = _media_type(definition, work["ids"])
        entries.append(
            (
                Entry(
                    rank=numbers[0],
                    title=work["title"] or work["qid"],
                    year=work["year"],
                    creator=work["creator"],
                    media_type=media_type,
                    ids=_entry_ids(media_type, work),
                ),
                work,
            ),
        )
    entries.sort(key=lambda item: item[0].rank)
    return entries


def _registry_entries(definition, works, supplement):
    by_qid = defaultdict(set)
    for row in supplement:
        if row["qid"]:
            by_qid[row["qid"]].add(row["year"])
    entries = []
    for work in works:
        years = by_qid.get(work["qid"]) or {year for side, year, _, _ in work["wins"] if side == "r" and year}
        if not years:
            logger.info("Wikidata %s: %s has no induction year; left out", definition.key, work["qid"])
            continue
        media_type = _media_type(definition, work["ids"])
        year = min(years)
        entries.append(
            (
                year,
                Entry(
                    rank=None,
                    title=work["title"] or work["qid"],
                    year=work["year"],
                    creator=work["creator"],
                    media_type=media_type,
                    ids=_entry_ids(media_type, work),
                    note=f"Inducted {year}",
                ),
                work,
            ),
        )
    for row in supplement:
        if not row["qid"]:
            entries.append(
                (
                    row["year"],
                    Entry(
                        rank=None,
                        title=row["title"],
                        year=row["published"],
                        creator=row["creator"],
                        media_type="movie",
                        ids={"ref": row["ref"]} if row["ref"] else {},
                        note=f"Inducted {row['year']}",
                    ),
                    None,
                ),
            )
    entries.sort(key=lambda item: (-item[0], normalize_title(item[1].title)))
    return [(entry, work) for _, entry, work in entries]


# Books: Open Library works -> Hardcover books or Open Library editions ---------------


def _titles_agree(expected, actual):
    expected_forms, actual_forms = title_forms(expected), title_forms(actual)
    if expected_forms & actual_forms:
        return True
    wanted, found = normalize_title(expected), normalize_title(actual)
    return bool(wanted and found) and (wanted in found or found in wanted)


def _cached(kind, key, compute):
    cache_key = f"{CACHE_PREFIX}{kind}:{key}"
    value = cache.get(cache_key)
    if value is not None:
        return value or None
    value = compute()
    cache.set(cache_key, value or {}, LOOKUP_HIT_TTL if value else LOOKUP_MISS_TTL)
    return value


def hardcover_books(external_ids):
    """Return ``{external_id: {id, title, image}}`` from Hardcover's Open Library mappings.

    ``external_ids`` are ``/works/OL…W`` or ``/books/OL…M`` paths. Empty without a key.
    """
    if not getattr(settings, "HARDCOVER_API", "") or not external_ids:
        return {}
    query = """query Mapped($ids: [String!]) {
      book_mappings(where: {platform_id: {_eq: %d}, external_id: {_in: $ids}}) {
        external_id
        book { id title cached_image(path: "url") users_count }
      }
    }""" % OPENLIBRARY_PLATFORM  # noqa: UP031
    found = {}
    ids = sorted(set(external_ids))
    for start in range(0, len(ids), HARDCOVER_BATCH):
        data = post_json(
            "hardcover",
            HARDCOVER_URL,
            json={"query": query, "variables": {"ids": ids[start : start + HARDCOVER_BATCH]}},
            headers={"Authorization": settings.HARDCOVER_API},
        )
        if not isinstance(data, dict) or not isinstance(data.get("data") or {}, dict):
            msg = "Hardcover sent an unreadable answer to the Open Library mapping query."
            raise AdapterError(msg)
        if data.get("errors"):
            msg = "Hardcover rejected the Open Library mapping query."
            raise AdapterError(msg)
        for mapping in (data.get("data") or {}).get("book_mappings") or []:
            if not isinstance(mapping, dict) or not mapping.get("external_id"):
                continue
            book = mapping.get("book") or {}
            if not book.get("id"):
                continue
            current = found.get(mapping["external_id"])
            if current is None or (book.get("users_count") or 0) > current["users_count"]:
                found[mapping["external_id"]] = {
                    "id": str(book["id"]),
                    "title": book.get("title") or "",
                    "image": book.get("cached_image") or "",
                    "users_count": book.get("users_count") or 0,
                }
    return found


def _edition_score(edition):
    languages = {language.get("key") for language in edition.get("languages") or [] if isinstance(language, dict)}
    return (
        "/languages/eng" in languages,
        bool(edition.get("covers") and edition["covers"][0] and edition["covers"][0] > 0),
        bool(edition.get("isbn_13") or edition.get("isbn_10")),
    )


def _fetch_editions(work_id, hops=3):
    """Return the editions of an Open Library work, following merged-work redirects."""
    for _ in range(hops):
        try:
            data = get_json("openlibrary", f"{OPENLIBRARY_URL}/works/{work_id}/editions.json", params={"limit": 50})
            return (data.get("entries") or []) if isinstance(data, dict) else []
        except AdapterError:
            work = get_json("openlibrary", f"{OPENLIBRARY_URL}/works/{work_id}.json")
            location = str(work.get("location") or "")
            if (work.get("type") or {}).get("key") != "/type/redirect" or not location.startswith("/works/"):
                raise
            work_id = location.rsplit("/", 1)[-1]
    return []


def work_editions(work_id):
    """Return ``{"best": {olid, image, title}, "keys": [edition olids]}`` for a work."""

    def compute():
        editions = _fetch_editions(work_id)
        ranked = sorted(
            (edition for edition in editions if str(edition.get("key", "")).startswith("/books/")),
            key=_edition_score,
            reverse=True,
        )
        if not ranked:
            return {}
        best = ranked[0]
        covers = [cover for cover in best.get("covers") or [] if cover and cover > 0]
        return {
            "best": {
                "olid": best["key"].rsplit("/", 1)[-1],
                "title": best.get("title") or "",
                "image": f"https://covers.openlibrary.org/b/id/{covers[0]}-L.jpg" if covers else "",
            },
            "keys": [edition["key"].rsplit("/", 1)[-1] for edition in ranked],
        }

    return _cached("olwork", work_id, compute)


def work_by_author(entry):
    """Return the Open Library work for a book that has no id, searching by title and author.

    The catalog search by title alone rarely reaches "Selected Poems" by a given
    poet. Accepted only on an exact (main) title, a matching author name and a
    first publication within two years; among duplicate works the one with the
    most editions wins. Cached like the other lookups.
    """
    title = _main_title(entry).title
    authors = [name.strip() for name in entry.creator.split(",") if name.strip()]
    if not authors:
        return None

    def compute():
        data = get_json(
            "openlibrary",
            f"{OPENLIBRARY_URL}/search.json",
            params={
                "title": title,
                "author": authors[0],
                "fields": "key,title,author_name,first_publish_year,edition_count",
                "limit": 20,
            },
        )
        matches = [
            doc
            for doc in ((data.get("docs") or []) if isinstance(data, dict) else [])
            if str(doc.get("key", "")).startswith("/works/")
            and doc.get("author_name")
            and title_forms(doc.get("title")) & title_forms(title)
            and creators_match(entry.creator, doc["author_name"])
            and not (
                entry.year
                and doc.get("first_publish_year")
                and abs(int(doc["first_publish_year"]) - entry.year) > SEARCH_YEAR_TOLERANCE
            )
        ]
        if not matches:
            return {}
        best = max(matches, key=lambda doc: doc.get("edition_count") or 0)
        return {"work": best["key"].rsplit("/", 1)[-1]}

    key = hashlib.sha256(f"{normalize_title(title)}|{entry.creator}|{entry.year}".encode()).hexdigest()[:24]
    found = _cached("olsearch", key, compute)
    return (found or {}).get("work")


def _main_title(entry):
    """Search a book by its main title: "Wilmington's Lie: The Murderous Coup…" -> "Wilmington's Lie".

    Wikidata's disambiguators go too: "Blood in the Water (book)" -> "Blood in the Water".

    The strict title search accepts a result whose main title equals the entry's
    title, but not the reverse, and catalogs often drop long subtitles. The match
    stays exact on the main title, the author and the year.
    """
    title = DISAMBIGUATOR.sub("", entry.title)
    head = title.split(":", 1)[0].strip()
    if len(normalize_title(head)) < 3:  # noqa: PLR2004
        head = title
    return entry if head == entry.title else replace(entry, title=head)


def _book_ids(pairs):
    """Give book entries a Hardcover id, else an Open Library edition, from their work ids."""
    works = {}
    for index, (entry, work) in enumerate(pairs):
        olid = str(((work or {}).get("ids") or {}).get("olw", "")).upper()
        if entry.media_type == "book" and "ref" not in entry.ids and re.match(r"^OL\d+[WM]$", olid):
            works[index] = olid
    found = {}  # index -> (ids, image)
    mapped = hardcover_books([f"/works/{olid}" for olid in works.values() if olid.endswith("W")])
    for index, olid in works.items():
        book = mapped.get(f"/works/{olid}")
        if book and _titles_agree(pairs[index][0].title, book["title"]):
            found[index] = ({"hardcover": book["id"]}, book["image"])
        elif olid.endswith("M"):
            found[index] = ({"olid": olid}, "")

    for index, (entry, work) in enumerate(pairs):
        if index in works or entry.media_type != "book" or "ref" in entry.ids or not entry.creator:
            continue
        try:
            work_id = work_by_author(entry)
        except AdapterError as error:
            logger.warning("Open Library search failed for %s: %s", entry.key, error)
            continue
        if work_id:
            works[index] = work_id

    editions = {}
    for index, olid in works.items():
        if index in found:
            continue
        try:
            editions[index] = work_editions(olid)
        except AdapterError as error:
            logger.warning("Open Library editions failed for %s: %s", olid, error)
    edition_keys = [f"/books/{key}" for value in editions.values() if value for key in value["keys"]]
    mapped = hardcover_books(edition_keys)
    for index, value in editions.items():
        if not value:
            continue
        title = pairs[index][0].title
        books = [mapped[f"/books/{key}"] for key in value["keys"] if f"/books/{key}" in mapped]
        books = [book for book in books if _titles_agree(title, book["title"])]
        if books:
            book = max(books, key=lambda item: item["users_count"])
            found[index] = ({"hardcover": book["id"]}, book["image"])
        else:
            found[index] = ({"olid": value["best"]["olid"]}, value["best"]["image"])

    entries = []
    for index, (entry, _) in enumerate(pairs):
        if index not in found:
            entries.append(_main_title(entry) if "ref" not in entry.ids else entry)
            continue
        ids, image = found[index]
        entries.append(
            Entry(
                rank=entry.rank,
                title=entry.title,
                year=entry.year,
                creator=entry.creator,
                media_type=entry.media_type,
                ids={**ids, **entry.ids},
                image=image or entry.image,
                note=entry.note,
            ),
        )
    return entries


# Fetch --------------------------------------------------------------------------


def fetch(definition):
    """Run the list's query, merge the supplement, and build entries."""
    supplement, excluded = _split(supplement_rows(definition.key))
    extra = sorted({row["qid"] for row in supplement if row["qid"]})
    rows = sparql(build_query(definition, extra))
    works = [work for work in (_work(row) for row in rows) if work["qid"] and work["qid"] not in excluded]
    works = filter_by_class(definition, works, keep=set(extra))
    mode = definition.params["mode"]
    if mode == "criterion":
        pairs = _criterion_entries(definition, works)
    elif mode == "registry":
        pairs = _registry_entries(definition, works, supplement)
    else:
        pairs = _award_entries(definition, works, supplement)
    if not pairs:
        msg = "Wikidata returned no entries for this list."
        raise AdapterError(msg)
    entries = _book_ids(pairs) if "book" in definition.media_types else [entry for entry, _ in pairs]
    latest = max((year for entry in entries if (year := _latest_year(entry.note))), default=None)
    return FetchResult(entries=entries, edition=str(latest) if latest else "")


def _latest_year(note):
    match = re.search(r"(\d{4})", note or "")
    return int(match.group(1)) if match else None


ADAPTER = Adapter(key="wikidata", fetch=fetch, validate=validate, version=version)
