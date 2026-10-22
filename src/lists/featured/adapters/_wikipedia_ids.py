"""Provider IDs for linked Wikipedia articles, through Wikidata (private to wikipedia_table).

1. ``wikidata_items(site, titles)``: one batched MediaWiki ``pageprops`` request per 50
   titles gives each linked article's Wikidata item. A link that redirects to a section
   of another article (``tofragment``) names that article's item, not the work, so it
   gets none.
2. ``provider_ids(media_type, qids)``: one SPARQL request per 50 items reads only the
   properties this media type needs and turns them into ``Entry.ids``:

   | media type | property | ids key |
   |---|---|---|
   | tv / movie | P4983 / P4947, else IMDb (P345) | ``tmdb`` / ``imdb`` |
   | game | P5794 with the numeric qualifier P9043, else Steam (P1733) | ``igdb`` / ``steam`` |
   | anime / manga | P4086 / P4087 (manga also P11149, MangaUpdates) | ``mal`` / ``mangaupdates`` |
   | comic | P5905 (volumes only, ``4050-<id>``) | ``comicvine`` |
   | music | P436 | ``mbid_rg`` |
   | book | P2969 (Goodreads edition) through Hardcover's ``book_mappings``; else P648 | ``hardcover`` / ``olid`` |

   Hardcover maps Goodreads *book* (edition) IDs, which Wikidata keeps in P2969; the
   Goodreads *work* ID (P8383) never matches there. An Open Library work becomes its
   cover edition through one batched ``search.json`` request.

A TV season's item (``Breaking Bad season 5``) without IDs of its own takes its series'
(P179): Spine stores whole TV series. Anime seasons are separate MAL entries, so they
never do.

A property with several different values and no preferred one is ambiguous (an anime
item listing every season, say) and is left out, so the resolver falls back to a strict
title search instead of guessing. Every failure raises ``AdapterError``: a list without
its IDs would resolve worse, so the run stops and the list stays as it was.
"""

import re
import unicodedata

from django.conf import settings

from lists.featured.helpers import get_json, post_json
from lists.featured.types import AdapterError

BATCH = 50
SPARQL_URL = "https://query.wikidata.org/sparql"
HARDCOVER_URL = "https://api.hardcover.app/v1/graphql"
HARDCOVER_GOODREADS_PLATFORM = 1
OPENLIBRARY_SEARCH_URL = "https://openlibrary.org/search.json"
OPENLIBRARY_COVER = "https://covers.openlibrary.org/b/id/{}-L.jpg"
MANGAUPDATES_SEARCH_URL = "https://api.mangaupdates.com/v1/series/search"
MANGAUPDATES_EXCLUDED_GENRES = ["Adult", "Hentai", "Doujinshi"]
COMICVINE_VOLUME_PREFIX = "4050-"
IGDB_NUMERIC_QUALIFIER = "P9043"

PROPERTIES = {
    "tv": ("P4983", "P345", "P179"),
    "movie": ("P4947", "P345"),
    "game": ("P5794", "P1733"),
    "anime": ("P4086",),
    "manga": ("P4087", "P11149"),
    "comic": ("P5905",),
    "music": ("P436",),
    "book": ("P2969", "P648"),
}
# Properties where every value is kept (each is tried), instead of one unambiguous value.
MULTI_VALUED = {"P2969", "P648"}
PART_OF_SERIES = "P179"
ENTITY_PREFIX = "http://www.wikidata.org/entity/"

_HARDCOVER_QUERY = """
query GoodreadsMappings($ids: [String!]) {
  book_mappings(where: {platform_id: {_eq: %d}, external_id: {_in: $ids}}) {
    external_id
    book { id title cached_image(path: "url") }
  }
}
""" % HARDCOVER_GOODREADS_PLATFORM  # noqa: UP031


def _batches(values):
    values = list(values)
    for start in range(0, len(values), BATCH):
        yield values[start : start + BATCH]


def api_url(site):
    """Return the MediaWiki API URL of a Wikipedia language edition."""
    return f"https://{site}.wikipedia.org/w/api.php"


def wikidata_items(site, titles):
    """Return ``{article title as linked: "Q…"}`` for the titles that have an item."""
    found = {}
    for batch in _batches(dict.fromkeys(titles)):
        data = get_json(
            "wikipedia",
            api_url(site),
            params={
                "action": "query",
                "prop": "pageprops",
                "ppprop": "wikibase_item",
                "redirects": "1",
                "format": "json",
                "formatversion": "2",
                "titles": "|".join(batch),
            },
        )
        query = data.get("query")
        if not isinstance(query, dict):
            msg = "Wikipedia returned no page properties."
            raise AdapterError(msg)
        renamed = {row["from"]: row["to"] for row in query.get("normalized") or []}
        redirects = {}
        for row in query.get("redirects") or []:
            redirects[row["from"]] = None if row.get("tofragment") else row["to"]
        items = {
            page["title"]: (page.get("pageprops") or {}).get("wikibase_item")
            for page in query.get("pages") or []
            if not page.get("missing")
        }
        for title in batch:
            current = renamed.get(title, title)
            seen = set()
            while current in redirects and current not in seen:
                seen.add(current)
                current = redirects[current]
            qid = items.get(current) if current else None
            if qid:
                found[title] = qid
    return found


def _sparql(qids, properties):
    values = " ".join(f"wd:{qid}" for qid in qids)
    pairs = " ".join(f'("{pid}" p:{pid} ps:{pid})' for pid in properties)
    query = (
        "SELECT ?item ?pid ?value ?rank ?number WHERE { "
        f"VALUES ?item {{ {values} }} VALUES (?pid ?p ?ps) {{ {pairs} }} "
        "?item ?p ?statement . ?statement ?ps ?value ; wikibase:rank ?rank . "
        "FILTER(?rank != wikibase:DeprecatedRank) "
        f"OPTIONAL {{ ?statement pq:{IGDB_NUMERIC_QUALIFIER} ?number }} }}"
    )
    data = get_json(
        "wikidata",
        SPARQL_URL,
        params={"query": query, "format": "json"},
        headers={"Accept": "application/sparql-results+json"},
    )
    try:
        bindings = data["results"]["bindings"]
    except (KeyError, TypeError):
        msg = "Wikidata returned an unreadable query result."
        raise AdapterError(msg) from None
    claims = {}
    for row in bindings:
        qid = row["item"]["value"].rsplit("/", 1)[-1]
        value = row["value"]["value"].removeprefix(ENTITY_PREFIX)
        if row.get("number"):
            value = row["number"]["value"]
        elif row["pid"]["value"] == "P5794":
            continue  # an IGDB slug without the numeric ID the resolver needs
        preferred = row.get("rank", {}).get("value", "").endswith("PreferredRank")
        claims.setdefault(qid, {}).setdefault(row["pid"]["value"], []).append((value, preferred))
    return claims


def _single(values):
    """Return the one value of a claim: the preferred one, or the only distinct one."""
    preferred = list(dict.fromkeys(value for value, is_preferred in values if is_preferred))
    if len(preferred) == 1:
        return preferred[0]
    distinct = list(dict.fromkeys(value for value, _ in values))
    return distinct[0] if len(distinct) == 1 else None


def _all(values):
    preferred = [value for value, is_preferred in values if is_preferred]
    rest = [value for value, is_preferred in values if not is_preferred]
    return list(dict.fromkeys(preferred + rest))


def _mangaupdates_id(value):
    """Turn a MangaUpdates website ID (base 36) into the API's numeric series ID."""
    try:
        return str(int(str(value).strip(), 36))
    except ValueError:
        return None


def _simple_ids(media_type, claims):
    single = {pid: _single(values) for pid, values in claims.items() if pid not in MULTI_VALUED}
    if media_type in ("tv", "movie"):
        value = single.get("P4983" if media_type == "tv" else "P4947")
        if value and value.isdigit():
            return {"tmdb": value}
        imdb = single.get("P345") or ""
        return {"imdb": imdb} if re.fullmatch(r"tt\d+", imdb) else {}
    if media_type == "game":
        value = single.get("P5794")
        if value and value.isdigit():
            return {"igdb": value}
        steam = single.get("P1733")
        return {"steam": steam} if steam and steam.isdigit() else {}
    if media_type == "anime":
        value = single.get("P4086")
        return {"mal": value} if value and value.isdigit() else {}
    if media_type == "manga":
        ids = {}
        if single.get("P4087", "").isdigit():
            ids["mal"] = single["P4087"]
        mangaupdates = _mangaupdates_id(single["P11149"]) if single.get("P11149") else None
        if mangaupdates:
            ids["mangaupdates"] = mangaupdates
        return ids
    if media_type == "comic":
        value = single.get("P5905") or ""
        volume = value.removeprefix(COMICVINE_VOLUME_PREFIX)
        return {"comicvine": volume} if value.startswith(COMICVINE_VOLUME_PREFIX) and volume.isdigit() else {}
    if media_type == "music":
        value = single.get("P436")
        return {"mbid_rg": value} if value else {}
    return {}


def hardcover_books(goodreads_ids):
    """Return ``{goodreads book id: {"id", "title", "image"}}`` from Hardcover's mappings."""
    if not getattr(settings, "HARDCOVER_API", ""):
        return {}
    found = {}
    for batch in _batches(dict.fromkeys(goodreads_ids)):
        data = post_json(
            "hardcover",
            HARDCOVER_URL,
            json={"query": _HARDCOVER_QUERY, "variables": {"ids": batch}},
            headers={"Authorization": settings.HARDCOVER_API},
        )
        if data.get("errors") or not isinstance(data.get("data"), dict):
            msg = "Hardcover refused the Goodreads mapping query."
            raise AdapterError(msg)
        for row in data["data"].get("book_mappings") or []:
            book = row.get("book") or {}
            if book.get("id"):
                found[str(row["external_id"])] = {
                    "id": str(book["id"]),
                    "title": book.get("title") or "",
                    "image": book.get("cached_image") or "",
                }
    return found


def openlibrary_editions(work_ids):
    """Return ``{"OL…W": {"olid": "OL…M", "image": url}}`` for each work's cover edition."""
    found = {}
    for batch in _batches(dict.fromkeys(work_ids)):
        query = " OR ".join(f"/works/{work}" for work in batch)
        data = get_json(
            "openlibrary",
            OPENLIBRARY_SEARCH_URL,
            params={
                "q": f"key:({query})",
                "fields": "key,cover_edition_key,cover_i,editions,editions.key,editions.cover_i",
                "limit": str(len(batch)),
            },
        )
        for doc in data.get("docs") or []:
            work = str(doc.get("key") or "").rsplit("/", 1)[-1]
            edition = doc.get("cover_edition_key") or ""
            cover = doc.get("cover_i")
            if not edition:
                editions = (doc.get("editions") or {}).get("docs") or []
                if editions:
                    edition = str(editions[0].get("key") or "").rsplit("/", 1)[-1]
                    cover = editions[0].get("cover_i") or cover
            if work and edition.upper().endswith("M"):
                found[work] = {"olid": edition.upper(), "image": OPENLIBRARY_COVER.format(cover) if cover else ""}
    return found


def _book_ids(claims_by_qid):
    goodreads = {qid: _all(claims.get("P2969", [])) for qid, claims in claims_by_qid.items()}
    mapped = hardcover_books(value for values in goodreads.values() for value in values)
    result = {}
    for qid, values in goodreads.items():
        books = {mapped[value]["id"]: mapped[value] for value in values if value in mapped}
        if len(books) == 1:
            book = next(iter(books.values()))
            result[qid] = ({"hardcover": book["id"]}, book["image"])
    olids = {
        qid: [value.upper() for value in _all(claims.get("P648", []))]
        for qid, claims in claims_by_qid.items()
        if qid not in result
    }
    works = sorted({value for values in olids.values() for value in values if value.endswith("W")})
    editions = openlibrary_editions(works) if works else {}
    for qid, values in olids.items():
        for value in values:
            if value.endswith("M"):
                result[qid] = ({"olid": value}, "")
                break
            if value in editions:
                result[qid] = ({"olid": editions[value]["olid"]}, editions[value]["image"])
                break
    return result


def provider_ids(media_type, qids):
    """Return ``{qid: (ids, image)}`` for the items that carry a usable ID."""
    properties = PROPERTIES.get(media_type)
    if not properties:
        return {}
    claims = {}
    for batch in _batches(dict.fromkeys(qids)):
        claims.update(_sparql(batch, properties))
    if media_type == "book":
        return _book_ids(claims)
    result = {}
    for qid, item_claims in claims.items():
        ids = _simple_ids(media_type, item_claims)
        if ids:
            result[qid] = (ids, "")
    if media_type == "tv":
        parents = {
            qid: parent
            for qid, item_claims in claims.items()
            if qid not in result and (parent := _single(item_claims.get(PART_OF_SERIES, [])))
        }
        if parents:
            series = provider_ids(media_type, set(parents.values()) - set(parents))
            for qid, parent in parents.items():
                if parent in series:
                    result[qid] = series[parent]
    return result


def _exact(value):
    """Return a title for exact comparison: NFKC, case- and space-insensitive, nothing more."""
    return "".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def mangaupdates_by_title(titles):
    """Return ``{title: (ids, image)}`` for titles that name exactly one MangaUpdates series.

    MangaUpdates matches its associated names (Japanese, romanized and English) and
    reports which one matched as ``hit_title``. A title counts only when exactly one
    result's ``hit_title`` equals it exactly, so a Japanese top 10 can match without any
    fuzzy guessing; an ambiguous or missing title is left to the resolver.
    """
    found = {}
    for title in dict.fromkeys(titles):
        data = post_json(
            "mangaupdates",
            MANGAUPDATES_SEARCH_URL,
            json={
                "search": title,
                "stype": "title",
                "perpage": 25,
                "exclude_genre": MANGAUPDATES_EXCLUDED_GENRES,
            },
        )
        if not isinstance(data.get("results"), list):
            msg = "MangaUpdates returned an unreadable search result."
            raise AdapterError(msg)
        wanted = _exact(title)
        hits = {
            str(row["record"]["series_id"]): row["record"]
            for row in data["results"]
            if row.get("record", {}).get("series_id") and _exact(row.get("hit_title")) == wanted
        }
        if len(hits) == 1:
            series_id, record = next(iter(hits.items()))
            image = ((record.get("image") or {}).get("url") or {}).get("original") or ""
            found[title] = ({"mangaupdates": series_id}, image)
    return found
