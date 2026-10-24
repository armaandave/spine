"""The UK's Official Charts (Official Charts Company), from the weekly chart page.

Params: ``{"chart": "albums-chart"}``. The page lists positions, titles and artists
in capitals, with long titles cut short ("YOU SEEM PRETTY SAD FOR A GIRL SO IN"). Each
row gets a strict MusicBrainz search: exact title or title without edition markers,
a matching artist, no singles, and a single best candidate; a title of 30 or more
characters may also match a longer release-group title it is the start of.
``"min_entries"`` (default ``MIN_ROWS``) is the fewest readable rows a complete chart
has; fewer fail the run before any MusicBrainz lookup.
"""

import re
from datetime import datetime

from bs4 import BeautifulSoup

from lists.featured.adapters import _music, _rows
from lists.featured.helpers import get_text
from lists.featured.text import normalize_title
from lists.featured.types import Adapter, AdapterError, Entry, FetchResult

URL = "https://www.officialcharts.com/charts/{chart}/"
CHARTS = ("albums-chart",)
MIN_ROWS = 40  # fewer means the page layout changed
# Long-running chart albums the strict search can't settle, checked by hand on
# musicbrainz.org (2026-10-22). Keyed by the page's normalized title and artist, since
# a title alone ("GREATEST HITS") isn't unique.
VERIFIED = {
    ("greatest hits", "queen"): "69ce61c8-127f-3809-95d8-62fdf3ae1347",  # the 1981 album, not the 2008/2023 sets
    ("divide", "ed sheeran"): "e05f9927-e24b-41c5-928d-d0e008a12444",  # "÷"
    ("divide x tour collection", "ed sheeran"): "fcb3558d-23f2-47f0-b023-688ab15d7cfd",  # "+−=÷× (Tour Collection)"
    ("1962 1966", "beatles"): "5bb120e2-fe76-33cc-a4bb-8f56f2fd3d67",  # "1962–1966", the Red Album
    ("1967 1970", "beatles"): "f2e0599e-222b-37a5-8544-2f68ce65fd66",  # "1967–1970", the Blue Album
    ("my love the essential collection", "celine dion"): "8a04b686-8a05-3e7c-a1de-4173ee98fa08",
    ("diamonds and rhinestones greatest hits", "dolly parton"): "0ebf82f1-f5b0-496a-a6bc-d29f30410d68",
    ("papercuts singles collection 2000 2023", "linkin park"): "a580c858-69a5-4afd-9541-48ec1322caa4",  # "Papercuts"
    ("greatest hits", "red hot chili peppers"): "e30a440f-8505-4f15-b000-7308dd2c03f9",  # the 2003 Warner album
    ("best of", "50 cent"): "6ff60ca7-3ebb-4fd3-95c9-41d1675cba36",  # the 2017 "Best of 50 Cent", not the 2003 mixtape
}
_DATE = re.compile(r"(\d{1,2} [A-Z][a-z]+ \d{4})\s*-\s*\d{1,2} [A-Z][a-z]+ \d{4}")


def validate(definition):
    """Check the params without network."""
    if definition.params.get("chart") not in CHARTS:
        msg = f"chart must be one of {CHARTS}"
        raise ValueError(msg)
    if set(definition.media_types) != {"music"}:
        msg = "the albums chart holds music"
        raise ValueError(msg)
    _rows.validate(definition)


def _text(node):
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def parse_chart(html):
    """Return ``([(position, title, artist)], chart_start_date | None)``."""
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for item in soup.select("div.chart-item-content"):
        position = _text(item.select_one(".chart-key strong"))
        name = item.select_one("a.chart-name")
        artist = _text(item.select_one("a.chart-artist"))
        if not position.isdigit() or name is None or not artist:
            continue
        spans = [span for span in name.find_all("span") if "movement-icon" not in (span.get("class") or [])]
        title = _text(spans[-1]) if spans else _text(name)
        if title:
            rows.append((int(position), title, artist))
    match = _DATE.search(soup.get_text(" "))
    start = None
    if match:
        try:
            start = datetime.strptime(match.group(1), "%d %B %Y").date()
        except ValueError:
            start = None
    return rows, start


def _verified_key(title, artist):
    return normalize_title(title), normalize_title(artist)


def fetch(definition):
    """Fetch this week's chart and match every album."""
    html = get_text("official charts", URL.format(chart=definition.params["chart"]))
    rows, start = parse_chart(html)
    if len(rows) < MIN_ROWS:
        msg = f"The Official Charts page had {len(rows)} readable rows; its layout may have changed."
        raise AdapterError(msg)
    _rows.require(definition, len(rows), "The Official Charts page")
    entries = []
    verified = _music.release_group_details(
        mbid for _, title, artist in rows if (mbid := VERIFIED.get(_verified_key(title, artist)))
    )
    for position, title, artist in rows:
        mbid = VERIFIED.get(_verified_key(title, artist))
        if mbid:
            info = verified.get(mbid) or {"id": mbid, "title": title, "year": None}
        else:
            info = _music.search_release_group(title, artist, abbreviated=True)
        entries.append(
            Entry(
                rank=position,
                title=info["title"] if info else title,
                year=info["year"] if info else None,
                creator=artist,
                media_type="music",
                ids={"mbid_rg": info["id"]} if info else {},
                image=_music.cover_url(info["id"]) if info else "",
            ),
        )
    edition = f"Week of {start:%b} {start.day}, {start.year}" if start else ""
    return FetchResult(entries=entries, edition=edition)


ADAPTER = Adapter(key="official_charts", fetch=fetch, validate=validate)
