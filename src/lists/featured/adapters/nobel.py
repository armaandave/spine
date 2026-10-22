"""Nobel Prize laureates from the official API, as a people list.

Params: ``{"category": "lit", "person_source": "openlibrary"}``.

``api.nobelprize.org/2.1/nobelPrizes`` gives the prizes (years, laureates, shares);
``/laureates`` gives each laureate's Wikidata id (and ``sameAs`` links). One SPARQL
query then maps the laureates to Open Library authors (``P648``) and a Commons
portrait (``P18``). A laureate without an Open Library id falls back to the
resolver's strict name search in ``person_source``. Newest first; a prize split
between laureates reads "<year> · shared".
"""

import re
from urllib.parse import quote

from lists.featured.adapters import wikidata
from lists.featured.helpers import get_json
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

API = "https://api.nobelprize.org/2.1"
CATEGORIES = ("che", "eco", "lit", "pea", "phy", "med")
LIMIT = 500
TIMEOUT = 30
PORTRAIT_WIDTH = 400
OPENLIBRARY_AUTHOR = re.compile(r"^OL\d+A$")


def validate(definition):
    """Check the params without network."""
    if definition.params.get("category") not in CATEGORIES:
        msg = f"category must be one of {CATEGORIES}"
        raise ValueError(msg)
    if definition.list_type != "people":
        msg = "Nobel lists are people lists"
        raise ValueError(msg)


def _get(path, category):
    data = get_json(
        "nobelprize",
        f"{API}/{path}",
        params={"nobelPrizeCategory": category, "limit": LIMIT},
        timeout=TIMEOUT,
    )
    if not isinstance(data, dict):
        msg = "The Nobel Prize API returned an unexpected response."
        raise AdapterError(msg)
    return data


def _english(value):
    return (value or {}).get("en", "") if isinstance(value, dict) else ""


def laureate_qids(laureates):
    """Return ``{laureate id: QID}`` from ``wikidata.id``, else a ``sameAs`` link."""
    found = {}
    for laureate in laureates:
        qid = (laureate.get("wikidata") or {}).get("id") or ""
        if not wikidata.QID.match(qid):
            qid = next(
                (
                    wikidata.qid_of(link)
                    for link in laureate.get("sameAs") or []
                    if "wikidata.org" in str(link) and wikidata.qid_of(link)
                ),
                "",
            )
        if qid:
            found[str(laureate.get("id"))] = qid
    return found


def portrait_url(file_url):
    """Return a Commons thumbnail URL for a ``P18`` value."""
    name = str(file_url or "").rsplit("/", 1)[-1]
    if not name:
        return ""
    name = name.replace("%20", "_").replace(" ", "_")
    return (
        f"https://commons.wikimedia.org/wiki/Special:FilePath/{quote(name, safe='%_.-()')}"
        f"?width={PORTRAIT_WIDTH}"
    )


def people_ids(qids):
    """Return ``{QID: {"openlibrary": OL…A, "image": url}}`` from one SPARQL query."""
    if not qids:
        return {}
    query = f"""SELECT ?p (SAMPLE(?ol) AS ?ol_) (SAMPLE(?image) AS ?image_) WHERE {{
  VALUES ?p {{ {" ".join(f"wd:{qid}" for qid in sorted(set(qids)))} }}
  OPTIONAL {{ ?p wdt:P648 ?ol . FILTER(STRENDS(?ol, "A")) }}
  OPTIONAL {{ ?p wdt:P18 ?image }}
}} GROUP BY ?p"""
    found = {}
    for row in wikidata.sparql(query):
        qid = wikidata.qid_of(row.get("p"))
        if qid:
            found[qid] = {"openlibrary": row.get("ol_", ""), "image": portrait_url(row.get("image_"))}
    return found


def fetch(definition):
    """Fetch every prize in the category and its laureates."""
    category = definition.params["category"]
    prizes = _get("nobelPrizes", category).get("nobelPrizes") or []
    if not prizes:
        msg = "The Nobel Prize API returned no prizes."
        raise AdapterError(msg)
    qids = laureate_qids(_get("laureates", category).get("laureates") or [])
    people = people_ids(qids.values())
    rows = []
    for prize in prizes:
        year = int(prize.get("awardYear") or 0)
        laureates = prize.get("laureates") or []
        shared = len(laureates) > 1
        for laureate in laureates:
            laureate_id = str(laureate.get("id") or "")
            name = _english(laureate.get("knownName")) or _english(laureate.get("fullName")) or _english(
                laureate.get("orgName"),
            )
            qid = qids.get(laureate_id, "")
            person = people.get(qid, {})
            olid = person.get("openlibrary", "")
            ids = {"wikidata": qid}
            if OPENLIBRARY_AUTHOR.match(olid):
                ids["openlibrary_person"] = olid
            rows.append(
                (
                    -year,
                    int(laureate.get("sortOrder") or 1),
                    Entry(
                        rank=None,
                        title=name,
                        year=year or None,
                        ids=ids,
                        image=person.get("image", ""),
                        note=f"{year} · shared" if shared else str(year),
                    ),
                ),
            )
    rows.sort(key=lambda row: (row[0], row[1]))
    entries = [entry for _, _, entry in rows]
    latest = max((entry.year for entry in entries if entry.year), default=None)
    return FetchResult(entries=entries, edition=str(latest) if latest else "")


ADAPTER = Adapter(key="nobel", fetch=fetch, validate=validate)
