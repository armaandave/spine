"""Live award nights from Wikipedia ceremony pages.

Nominees are matched when nominations come out and stored in
``static/nominees/<ceremony>.csv`` (``category,rank,title,year,creator,ref,note``).
Each poll makes one batched MediaWiki revisions request for the watched pages; when a
page changed, it fetches the new revisions and reads each one's winner markers
(``lists.featured.wikitext``). A winner is published only when:

- it is a pre-matched nominee;
- it is the only one marked in its category, unless the page marks a tie;
- the page has marked it, without a break, for at least ``HOLD`` (60 s), counted
  from the timestamp of the revision that first marked it.

A published winner stays until another nominee has been marked for ``HOLD``; a
category that loses its marker keeps its winner. Marked entries that aren't
nominees, and nominees without a ref, go to ``unresolved`` for review. The small
state this needs lives in the cache under ``featured:award:<key>``.

Params:

- ``pages``: up to 50 page titles, or ``{"title": …, "section": …}`` to read one
  section only (the Golden Joysticks article keeps every year on one page).
  Redirects are never followed: a redirect counts as a missing page.
- ``ceremony``: the nominee file's name without ``.csv`` (default: the list key).
- ``aliases``: ``{category: [page label, …]}`` when the page labels a category
  differently from the nominee file.
- ``infobox``: ``{infobox field: category}``; ``awardN_type``/``awardN_winner``
  pairs are read without it.
- ``edition``: the period label ("2026").
- ``wiki``: the wiki host (default ``en.wikipedia.org``).

Before the ceremony the list is the nominees ("Nominee · <Category>"); as winners
are published they rise to the top in category order ("Winner · <Category>").
"""

import csv
import hashlib
import logging
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from django.core.cache import cache
from django.utils import timezone

from lists.featured import wikitext
from lists.featured.helpers import get_json
from lists.featured.resolve import parse_ref
from lists.featured.text import normalize_title
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

logger = logging.getLogger(__name__)

DIRECTORY = Path(__file__).resolve().parent.parent / "static" / "nominees"
COLUMNS = ["category", "rank", "title", "year", "creator", "ref", "note"]
HOLD_SECONDS = 60
MAX_TITLES = 50  # MediaWiki's limit per request
MAX_REVISIONS = 10  # revisions read per changed page per poll
STATE_PREFIX = "featured:award:"
STATE_TTL = 60 * 60 * 24 * 21
STATE_VERSION = 1
PARSER_VERSION = "1"  # bump when wikitext reading changes, to re-sync every award list
DEFAULT_WIKI = "en.wikipedia.org"
PARAMS = {"pages", "ceremony", "aliases", "infobox", "edition", "wiki"}
_WIKI_HOST = re.compile(r"^[a-z][a-z-]*\.wikipedia\.org$")
NO_MATCH_NOMINEE = "nominee has no Spine match"
NO_MATCH_WINNER = "winner has no Spine match"


def now():
    """Return the current time (patched by replay tests)."""
    return timezone.now()


# Nominee files -------------------------------------------------------------------


def nominee_path(definition):
    """Return ``static/nominees/<ceremony>.csv`` for a definition."""
    name = str(definition.params.get("ceremony") or definition.key)
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        msg = "ceremony must be a kebab-case file name"
        raise ValueError(msg)
    return DIRECTORY / f"{name}.csv"


def _int(value, column, line):
    value = (value or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        msg = f"line {line}: {column} must be a whole number"
        raise ValueError(msg) from None


def read_nominees(path, media_types):
    """Parse a nominee file, raising ``ValueError`` on any bad row."""
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != COLUMNS:
            msg = f"{Path(path).name}: header must be {','.join(COLUMNS)}"
            raise ValueError(msg)
        nominees = []
        for line, row in enumerate(reader, start=2):
            category, title = (row["category"] or "").strip(), (row["title"] or "").strip()
            if not category or not title:
                msg = f"line {line}: category and title are required"
                raise ValueError(msg)
            ref = (row["ref"] or "").strip()
            if ref:
                _, media_type, _ = parse_ref(ref)
                if media_type not in media_types:
                    msg = f"line {line}: {media_type} is not one of this list's media types"
                    raise ValueError(msg)
            nominees.append(
                wikitext.Nominee(
                    category=category,
                    rank=_int(row["rank"], "rank", line),
                    title=title,
                    year=_int(row["year"], "year", line),
                    creator=(row["creator"] or "").strip(),
                    ref=ref,
                    note=(row["note"] or "").strip(),
                ),
            )
    return nominees


def categories(nominees):
    """Return the categories in file order."""
    return list(dict.fromkeys(nominee.category for nominee in nominees))


def ordered(nominees):
    """Return nominees by category (file order), then rank (file order for ties)."""
    order = {category: index for index, category in enumerate(categories(nominees))}
    return sorted(
        nominees,
        key=lambda nominee: (order[nominee.category], nominee.rank is None, nominee.rank or 0),
    )


# Params --------------------------------------------------------------------------


def watched_pages(definition):
    """Return ``[(title, section or None)]``."""
    pages = definition.params.get("pages")
    if isinstance(pages, str) or not isinstance(pages, (list, tuple)) or not pages:
        msg = "pages must be a non-empty list of page titles"
        raise ValueError(msg)
    found = []
    for page in pages:
        if isinstance(page, Mapping):
            title, section = page.get("title"), page.get("section")
        else:
            title, section = page, None
        if not isinstance(title, str) or not title.strip():
            msg = "every page needs a title"
            raise ValueError(msg)
        if section is not None and (not isinstance(section, str) or not section.strip()):
            msg = f"{title}: section must be a heading's text"
            raise ValueError(msg)
        found.append((title.strip(), section))
    if len(found) > MAX_TITLES:
        msg = f"at most {MAX_TITLES} pages"
        raise ValueError(msg)
    return found


def labels_for(definition, nominees):
    """Return ``{normalized page label: category}``."""
    labels = {normalize_title(category): category for category in categories(nominees)}
    for category, aliases in (definition.params.get("aliases") or {}).items():
        for alias in [aliases] if isinstance(aliases, str) else aliases:
            labels[normalize_title(alias)] = category
    return labels


def _wiki(definition):
    host = definition.params.get("wiki", DEFAULT_WIKI)
    if not isinstance(host, str) or not _WIKI_HOST.match(host):
        msg = "wiki must be a host like en.wikipedia.org"
        raise ValueError(msg)
    return host


def validate(definition):
    """Check params and the nominee file; no network."""
    unknown = set(definition.params) - PARAMS
    if unknown:
        msg = f"unknown params: {sorted(unknown)}"
        raise ValueError(msg)
    watched_pages(definition)
    _wiki(definition)
    path = nominee_path(definition)
    if not path.exists():
        msg = f"static/nominees/{path.name} does not exist"
        raise ValueError(msg)
    nominees = read_nominees(path, set(definition.media_types))
    if not nominees and definition.featured:
        msg = f"static/nominees/{path.name} has no nominees; keep featured=False until it does"
        raise ValueError(msg)
    known = set(categories(nominees))
    aliases = definition.params.get("aliases") or {}
    infobox = definition.params.get("infobox") or {}
    if not isinstance(aliases, Mapping) or not isinstance(infobox, Mapping):
        msg = "aliases and infobox must be mappings"
        raise ValueError(msg)
    for category, values in aliases.items():
        if isinstance(values, str) or not all(isinstance(value, str) for value in values):
            msg = f"aliases for {category!r} must be a list of labels"
            raise ValueError(msg)
    for category in [*aliases, *infobox.values()]:
        if nominees and category not in known:
            msg = f"{category!r} is not a category in static/nominees/{path.name}"
            raise ValueError(msg)


def version(definition):
    """Return a hash of the nominee file (and the reader version)."""
    try:
        data = nominee_path(definition).read_bytes()
    except (OSError, ValueError):
        return "missing"
    return hashlib.sha256(PARSER_VERSION.encode() + b"\n" + data).hexdigest()


# MediaWiki -----------------------------------------------------------------------


def _query(host, params):
    data = get_json(
        "wikipedia",
        f"https://{host}/w/api.php",
        params={"action": "query", "format": "json", "formatversion": "2", **params},
    )
    if not isinstance(data, dict):
        msg = "Wikipedia returned an unreadable response."
        raise AdapterError(msg)
    if "error" in data:
        code = (data["error"] or {}).get("code", "unknown") if isinstance(data["error"], dict) else "unknown"
        msg = f"Wikipedia API error: {code}"
        raise AdapterError(msg)
    return data.get("query") or {}


def latest_revisions(host, titles):
    """Return ``{title: {"pageid", "revid", "ts"} | None}`` in one request per 50 titles.

    Missing pages and redirects are None: a redirect usually points at a general
    article (``The Game Awards 2026`` -> ``The Game Awards``), never the ceremony.
    """
    found = {}
    for start in range(0, len(titles), MAX_TITLES):
        chunk = titles[start : start + MAX_TITLES]
        query = _query(host, {"prop": "revisions|info", "rvprop": "ids|timestamp", "titles": "|".join(chunk)})
        normalized = {item.get("from"): item.get("to") for item in query.get("normalized") or []}
        pages = {page.get("title"): page for page in query.get("pages") or []}
        for title in chunk:
            page = pages.get(normalized.get(title, title))
            revisions = (page or {}).get("revisions") or []
            if not page or page.get("missing") or page.get("invalid") or page.get("redirect") or not revisions:
                found[title] = None
                continue
            found[title] = {"pageid": page["pageid"], "revid": revisions[0]["revid"], "ts": revisions[0]["timestamp"]}
    return found


def new_revisions(host, pageid, after=None):
    """Return ``(revisions, continuous)``: up to ``MAX_REVISIONS`` revisions newer than
    ``after`` (``{"revid", "ts"}``), oldest first, as ``(revid, ts, content)``.

    ``continuous`` is False when revisions between ``after`` and the oldest returned
    one were skipped, so no marker can be assumed to have held across the gap.
    """
    params = {
        "prop": "revisions",
        "pageids": str(pageid),
        "rvprop": "ids|timestamp|content",
        "rvslots": "main",
        "rvlimit": str(MAX_REVISIONS),
    }
    if after:
        params["rvend"] = after["ts"]
    pages = _query(host, params).get("pages") or []
    rows, parents = [], {}
    for revision in pages[0].get("revisions") or [] if pages else []:
        if after and revision["revid"] <= after["revid"]:
            continue
        parents[revision["revid"]] = revision.get("parentid")
        slot = (revision.get("slots") or {}).get("main") or {}
        if slot.get("texthidden") or "content" not in slot:
            continue  # suppressed revisions are almost always vandalism
        rows.append((revision["revid"], revision["timestamp"], slot["content"]))
    rows.sort()
    oldest = min(parents, default=None)
    continuous = bool(after) and oldest is not None and parents[oldest] == after["revid"]
    return rows, continuous


# State ---------------------------------------------------------------------------


def state_key(definition):
    """Return the cache key holding a definition's live state."""
    return f"{STATE_PREFIX}{definition.key}"


def _load_state(definition, nominee_hash):
    state = cache.get(state_key(definition))
    if not isinstance(state, dict) or state.get("v") != STATE_VERSION or state.get("nominees") != nominee_hash:
        return {"v": STATE_VERSION, "nominees": nominee_hash, "pages": {}, "categories": {}}
    return state


def _outcome_data(outcome):
    return [outcome.state, list(outcome.winners), outcome.tie, list(outcome.reasons)]


def _outcome(data):
    state, winners, tie, reasons = data
    return wikitext.Outcome(state, tuple(winners), tuple(reasons), tie)


def _when(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso(moment):
    """Return a UTC timestamp in MediaWiki's format, so stored times sort as text."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _advance(state, category_names, timestamp, *, fresh):
    """Fold the pages' current outcomes into each category's candidate at ``timestamp``."""
    for category in category_names:
        outcome = wikitext.merge(
            _outcome(page["outcomes"][category])
            for page in state["pages"].values()
            if category in page.get("outcomes", {})
        )
        entry = state["categories"].setdefault(
            category,
            {"state": wikitext.MISSING, "winners": [], "since": timestamp, "published": [], "published_at": None},
        )
        if fresh or [entry["state"], entry["winners"]] != [outcome.state, list(outcome.winners)]:
            entry.update(state=outcome.state, winners=list(outcome.winners), since=timestamp)
        entry["reasons"] = list(outcome.reasons)


def _publish(definition, state, current):
    for category, entry in state["categories"].items():
        if entry["state"] != wikitext.WINNERS or entry["winners"] == entry["published"]:
            continue
        held = (current - _when(entry["since"])).total_seconds()
        if held >= HOLD_SECONDS:
            entry["published"] = list(entry["winners"])
            entry["published_at"] = _iso(current)
            logger.info(
                "Featured list %s: %s winner %s published (marked since %s, held %ss)",
                definition.key,
                category,
                ", ".join(entry["published"]),
                entry["since"],
                int(held),
            )


def poll(definition, nominees):
    """Read the watched pages, advance the state and publish held winners.

    Returns the state (also saved in the cache).
    """
    host = _wiki(definition)
    pages = watched_pages(definition)
    labels = labels_for(definition, nominees)
    infobox = dict(definition.params.get("infobox") or {})
    names = categories(nominees)
    state = _load_state(definition, version(definition))
    current = now()
    latest = latest_revisions(host, [title for title, _ in pages])
    events = []
    for title, section in pages:
        info, known = latest.get(title), state["pages"].get(title)
        if info is None:
            if known is None or known.get("revid") is not None:
                missing = {category: _outcome_data(wikitext.Outcome(wikitext.MISSING)) for category in names}
                events.append((_iso(current), 0, title, None, missing, False))
            continue
        if known is not None and known.get("revid") == info["revid"]:
            continue
        # A lower latest revid means the newest revisions were deleted: read afresh.
        after = known if known and known.get("revid") and info["revid"] > known["revid"] else None
        revisions, continuous = new_revisions(host, info["pageid"], after)
        for index, (revid, timestamp, content) in enumerate(revisions):
            try:
                reading = wikitext.read_page(content, labels, section=section, infobox=infobox)
            except Exception:  # never let one odd revision stop the night
                logger.exception("Featured list %s: could not read %s revision %s", definition.key, title, revid)
                reading = wikitext.Reading()
            outcomes = {
                category: _outcome_data(outcome)
                for category, outcome in wikitext.evaluate(reading, nominees).items()
            }
            events.append((timestamp, revid, title, revid, outcomes, index == 0 and not continuous))
    for timestamp, _, title, revid, outcomes, fresh in sorted(events, key=lambda event: (event[0], event[1])):
        state["pages"][title] = {"revid": revid, "ts": timestamp, "outcomes": outcomes}
        _advance(state, names, timestamp, fresh=fresh)
    _publish(definition, state, current)
    cache.set(state_key(definition), state, STATE_TTL)
    return state


# Entries -------------------------------------------------------------------------


def _entry(nominee, note, review=None):
    if review or not nominee.ref:
        return Entry(
            rank=None,
            title=nominee.title,
            year=nominee.year,
            creator=nominee.creator,
            ids={"review": review or NO_MATCH_NOMINEE},
            note=note,
        )
    return Entry(
        rank=None,
        title=nominee.title,
        year=nominee.year,
        creator=nominee.creator,
        media_type=parse_ref(nominee.ref)[1],
        ids={"ref": nominee.ref},
        note=note,
    )


def _note(label, nominee, rows_with_key):
    note = f"{label} · {nominee.category}"
    if nominee.note and rows_with_key == 1:
        note += f" · {nominee.note}"
    return note


def build_entries(nominees, state):
    """Return the list: published winners in category order, then the other nominees,
    then one review row per problem the pages show now.
    """
    rows = ordered(nominees)
    counts = {}
    for nominee in rows:
        counts[(nominee.category, nominee.key)] = counts.get((nominee.category, nominee.key), 0) + 1
    published = {
        category: set(entry.get("published") or []) for category, entry in state.get("categories", {}).items()
    }
    entries, emitted = [], set()
    for category in categories(rows):
        for nominee in rows:
            if nominee.category != category or nominee.key not in published.get(category, ()):
                continue
            if nominee.key in emitted:
                continue
            emitted.add(nominee.key)
            review = None if nominee.ref else NO_MATCH_WINNER
            entries.append(_entry(nominee, _note("Winner", nominee, counts[(category, nominee.key)]), review))
    for nominee in rows:
        if nominee.key in emitted:
            continue
        emitted.add(nominee.key)
        entries.append(_entry(nominee, _note("Nominee", nominee, counts[(nominee.category, nominee.key)])))
    for category in categories(rows):
        entry = state.get("categories", {}).get(category) or {}
        if entry.get("state") != wikitext.REVIEW:
            continue
        for reason in entry.get("reasons") or ["needs review"]:
            entries.append(
                Entry(rank=None, title=f"{category}: {reason}"[:200], ids={"review": reason}, note=f"Review · {category}"),
            )
    return entries


def announced(nominees, state):
    """Return ``(categories with a published winner, all categories)``."""
    names = categories(nominees)
    done = sum(1 for category in names if (state.get("categories", {}).get(category) or {}).get("published"))
    return done, len(names)


def fetch(definition):
    """Poll the ceremony pages and return the list."""
    try:
        nominees = read_nominees(nominee_path(definition), set(definition.media_types))
    except (OSError, ValueError) as error:
        raise AdapterError(str(error)) from None
    edition = str(definition.params.get("edition") or "")
    if not nominees:
        return FetchResult(entries=[], edition=edition)
    state = poll(definition, nominees)
    done, total = announced(nominees, state)
    return FetchResult(
        entries=build_entries(nominees, state),
        edition=edition,
        live_label=f"{done} of {total} announced",
    )


ADAPTER = Adapter(key="award_page", fetch=fetch, validate=validate, version=version)
