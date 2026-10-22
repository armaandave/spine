"""MusicBrainz helpers shared by the music adapters (not an adapter: the ``_`` skips discovery).

A Spine music item is a MusicBrainz release group, so every music adapter ends up
here to turn what its source knows (an Apple Music or Spotify album URL, or a title
and an artist) into a release-group MBID:

- ``mb_get`` sends every MusicBrainz request through the provider's 1-per-second
  session with Spine's User-Agent.
- ``release_groups_for_urls`` maps streaming URLs to release groups through
  MusicBrainz URL relationships, in batches.
- ``search_release_group`` is the strict fallback: exact title (or the title with an
  edition marker such as "(Deluxe)" removed), a matching artist, no singles, the year
  within one when the source has one, and a single best candidate.

Answers are cached under ``featured:music:…`` (hits 30 days, misses one day).
"""

import hashlib
import re
import time

from django.core.cache import cache

from app.providers import musicbrainz, services
from lists.featured import helpers
from lists.featured.text import creators_match, normalize_title, parse_year
from lists.featured.types import AdapterError

MB_URL = "https://musicbrainz.org/ws/2"
CACHE_PREFIX = "featured:music:"
HIT_TTL = 60 * 60 * 24 * 30
MISS_TTL = 60 * 60 * 24
MISS = {"miss": True}
URL_BATCH = 25  # resources per /url lookup
RELEASE_BATCH = 40  # release ids per search
SEARCH_LIMIT = 25
MB_ATTEMPTS = 3
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# "(Deluxe)", "[Remastered 2011]", " - Expanded Edition", "(Video Version)"…
_MARKER_WORDS = (
    r"deluxe|edition|version|remaster(?:ed)?|expanded|anniversary|bonus|"
    r"explicit|clean|sped up|slowed|extended|reissue|cast recording"
)
_EDITION_SUFFIX = re.compile(
    rf"\s*(?:[\(\[][^\)\]]*\b(?:{_MARKER_WORDS})\b[^\)\]]*[\)\]]"
    rf"|\s[-–—]\s[^-–—()\[\]]*\b(?:{_MARKER_WORDS})\b[^()\[\]]*)\s*$",
    re.IGNORECASE,
)
_SKIPPED_PRIMARY_TYPES = {"single"}
MIN_PREFIX_LENGTH = 30  # shorter source titles are never treated as truncated


def cover_url(release_group_mbid):
    """Return the Cover Art Archive front cover, as Spine's MusicBrainz provider does."""
    return f"{musicbrainz.COVER_ART_URL}/release-group/{release_group_mbid}/front-500"


def is_mbid(value):
    """Return whether ``value`` is a lower-case MusicBrainz UUID."""
    return bool(UUID.match(str(value or "")))


def mb_get(path, params):
    """GET ``/ws/2/<path>`` as JSON at MusicBrainz's 1 request per second.

    A 503 (MusicBrainz is shedding load) is retried twice; any other failure raises
    ``AdapterError``.
    """
    for attempt in range(MB_ATTEMPTS):
        try:
            return helpers._request(  # noqa: SLF001 - the helpers' error mapping, with the MB session
                "musicbrainz",
                "GET",
                f"{MB_URL}/{path}",
                params={"fmt": "json", **params},
                headers=helpers._headers({"Accept": "application/json"}),  # noqa: SLF001
                request_session=services.musicbrainz_session,
            )
        except AdapterError as error:
            if "HTTP 503" not in str(error) or attempt == MB_ATTEMPTS - 1:
                raise
            time.sleep(1 + attempt)
    raise AssertionError("unreachable")


def credit_text(artist_credit):
    """Return "Artist & Other" from a MusicBrainz artist-credit list."""
    return "".join(
        f"{credit.get('name') or (credit.get('artist') or {}).get('name') or ''}{credit.get('joinphrase') or ''}"
        for credit in artist_credit or []
    ).strip()


def release_group_info(group, *, artist_credit=None):
    """Return the cacheable summary of a release-group object."""
    credits = artist_credit if artist_credit is not None else group.get("artist-credit")
    return {
        "id": group.get("id") or "",
        "title": group.get("title") or "",
        "year": parse_year(group.get("first-release-date")),
        "artist": credit_text(credits),
        "primary_type": group.get("primary-type") or "",
        "secondary_types": list(group.get("secondary-types") or []),
    }


def _cache_get(key):
    return cache.get(f"{CACHE_PREFIX}{key}")


def _cache_set(key, value):
    cache.set(f"{CACHE_PREFIX}{key}", value or MISS, HIT_TTL if value else MISS_TTL)


def _digest(*parts):
    return hashlib.sha256("\x1f".join(str(part) for part in parts).encode()).hexdigest()[:32]


def _chunks(values, size):
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start : start + size]


# URL relationships --------------------------------------------------------------


def _url_releases(urls):
    """Return ``{url: {release_mbid}}`` (and release groups linked directly) for ``urls``."""
    releases, direct = {}, {}
    for chunk in _chunks(urls, URL_BATCH):
        try:
            data = mb_get("url", {"resource": chunk, "inc": "release-rels+release-group-rels"})
        except AdapterError as error:
            if "HTTP 404" in str(error):  # a lookup of one unknown URL is a 404
                continue
            raise
        found = data.get("urls") if "urls" in data else [data]
        for url in found or []:
            resource = url.get("resource") or ""
            for relation in url.get("relations") or []:
                if relation.get("target-type") == "release" and relation.get("release"):
                    releases.setdefault(resource, set()).add(relation["release"]["id"])
                elif relation.get("target-type") == "release_group" and relation.get("release_group"):
                    direct.setdefault(resource, {})[relation["release_group"]["id"]] = release_group_info(
                        relation["release_group"],
                    )
    return releases, direct


def _release_groups_of(release_ids):
    """Return ``{release_mbid: release-group info}`` through batched release searches."""
    found = {}
    for chunk in _chunks(sorted(release_ids), RELEASE_BATCH):
        query = "reid:(" + " OR ".join(chunk) + ")"
        data = mb_get("release", {"query": query, "limit": 100})
        for release in data.get("releases") or []:
            group = release.get("release-group") or {}
            if release.get("id") in release_ids and group.get("id"):
                found[release["id"]] = release_group_info(group, artist_credit=release.get("artist-credit"))
    for release_id in sorted(set(release_ids) - set(found)):  # not in the search index yet
        data = mb_get(f"release/{release_id}", {"inc": "release-groups+artist-credits"})
        group = data.get("release-group") or {}
        if group.get("id"):
            found[release_id] = release_group_info(group, artist_credit=data.get("artist-credit"))
    return found


def release_groups_for_urls(urls):
    """Return ``{url: release-group info}`` for the URLs MusicBrainz links to one release group.

    A URL linked to releases of more than one release group is left out.
    """
    urls = list(dict.fromkeys(urls))
    result, pending = {}, []
    for url in urls:
        cached = _cache_get(f"url:{_digest(url)}")
        if cached is None:
            pending.append(url)
        elif cached != MISS:
            result[url] = cached
    if not pending:
        return result
    releases, direct = _url_releases(pending)
    groups = _release_groups_of({release for ids in releases.values() for release in ids})
    for url in pending:
        candidates = dict(direct.get(url, {}))
        for release_id in releases.get(url, ()):
            info = groups.get(release_id)
            if info:
                candidates.setdefault(info["id"], info)
        info = next(iter(candidates.values())) if len(candidates) == 1 else None
        _cache_set(f"url:{_digest(url)}", info)
        if info:
            result[url] = info
    return result


# Strict search ------------------------------------------------------------------


def strip_edition(title):
    """Return the title without trailing edition markers: "Take Care (Deluxe Version)" -> "Take Care"."""
    text = str(title or "").strip()
    while True:
        stripped = _EDITION_SUFFIX.sub("", text).strip()
        if not stripped or stripped == text:
            return text
        text = stripped


def _without_article(name):
    return re.sub(r"^the\s+", "", name)


def artist_matches(expected, actual):
    """Return whether the credited artist matches, ignoring a leading "The"."""
    if not expected or not actual:
        return False
    if creators_match(expected, actual):
        return True
    return creators_match(_without_article(normalize_title(expected)), _without_article(normalize_title(actual)))


def _without_artist_prefix(title, artist):
    """Return "Greatest Hits" for "Westlife - Greatest Hits" by Westlife, else the title."""
    for separator in (" - ", " – ", " — ", ": "):
        head, found, tail = str(title).partition(separator)
        if found and tail and normalize_title(head) == normalize_title(artist):
            return tail
    return title


def _title_kind(source_title, group_title, artist, *, abbreviated):
    """Return how the source title names the release group, best first, or None.

    0: exactly; 1: without edition markers or a leading "<artist> - "; 2: with the
    artist's name added before or after it ("At San Quentin" by Johnny Cash for
    "Johnny Cash at San Quentin", "THE ESSENTIAL" by MICHAEL JACKSON for "The
    Essential Michael Jackson"); with ``abbreviated`` (chart pages), 3: the start of a
    longer title cut short at 30 or more characters.
    """
    wanted = normalize_title(group_title)
    source = normalize_title(source_title)
    if not wanted or not source:
        return None
    if source == wanted:
        return 0
    plain = _without_artist_prefix(strip_edition(source_title), artist)
    if normalize_title(plain) == wanted or normalize_title(strip_edition(plain)) == wanted:
        return 1
    base = normalize_title(plain)
    artist_name = normalize_title(artist)
    names = {artist_name, _without_article(artist_name), f"the {_without_article(artist_name)}"}
    if artist_name and base and any(wanted in {f"{name} {base}", f"{base} {name}"} for name in names):
        return 2
    if abbreviated and len(source_title) >= MIN_PREFIX_LENGTH and wanted.startswith(f"{source} "):
        return 3
    return None


def _type_rank(info):
    primary = (info.get("primary_type") or "").casefold()
    primary_rank = {"album": 0, "ep": 1}.get(primary, 2)
    return primary_rank, 1 if info.get("secondary_types") else 0


def pick_release_group(groups, title, artist, year=None, *, abbreviated=False, include_singles=False):
    """Return the single best strict match among MusicBrainz release-group search results."""
    scored = []
    for group in groups:
        info = release_group_info(group)
        if not include_singles and (info["primary_type"] or "").casefold() in _SKIPPED_PRIMARY_TYPES:
            continue
        kind = _title_kind(title, info["title"], artist, abbreviated=abbreviated)
        if kind is None or not artist_matches(artist, info["artist"]):
            continue
        if year:
            if info["year"] is None or abs(info["year"] - year) > 1:
                continue
            distance = abs(info["year"] - year)
        else:
            distance = 0
        scored.append(((kind, *_type_rank(info), distance), info))
    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0])
    best = [info for score, info in scored if score == scored[0][0]]
    unique = {info["id"]: info for info in best}
    return best[0] if len(unique) == 1 else None


def _terms(value):
    """Return plain search words: no Lucene syntax, so no escaping and no phrase quirks.

    Dots and apostrophes join their letters ("R.E.M." -> "rem", "I've" -> "ive").
    """
    text = re.sub(r"[.'’`]", "", str(value or ""))
    return " ".join(re.sub(r"[^\w]+", " ", text).casefold().split())


def _queries(title, artist):
    """Return the searches to try: the whole title, then its main title alone.

    "Papercuts - Singles Collection 2000-2023" isn't found by all its words because
    MusicBrainz keeps "2000–2023" as one token; "papercuts" finds it.
    """
    plain = _without_artist_prefix(strip_edition(title), artist)
    main = re.split(r"\s[-–—]\s|:|\(", plain, maxsplit=1)[0]
    artist_terms = _terms(artist)
    return [
        f"releasegroup:({words}) AND artist:({artist_terms})"
        for words in dict.fromkeys((_terms(plain) or _terms(title), _terms(main)))
        if words
    ]


def search_release_group(title, artist, year=None, *, abbreviated=False, include_singles=False):
    """Return release-group info for an album by title and artist, or None (cached).

    The searches are loose (any of the words); ``pick_release_group`` is the strict part.
    """
    if not _terms(title) or not _terms(artist):
        return None
    cache_key = "search:" + _digest(
        normalize_title(title), normalize_title(artist), year or "", abbreviated, include_singles,
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        return None if cached == MISS else cached
    info = None
    for query in _queries(title, artist):
        data = mb_get("release-group", {"query": query, "limit": SEARCH_LIMIT})
        info = pick_release_group(
            data.get("release-groups") or [],
            title,
            artist,
            year,
            abbreviated=abbreviated,
            include_singles=include_singles,
        )
        if info:
            break
    _cache_set(cache_key, info)
    return info


def release_group_details(mbids):
    """Return ``{mbid: release-group info}`` through batched searches (cached)."""
    result, pending = {}, []
    for mbid in dict.fromkeys(mbids):
        cached = _cache_get(f"rg:{mbid}")
        if cached is None:
            pending.append(mbid)
        elif cached != MISS:
            result[mbid] = cached
    for chunk in _chunks(pending, RELEASE_BATCH):
        query = "rgid:(" + " OR ".join(chunk) + ")"
        data = mb_get("release-group", {"query": query, "limit": 100})
        found = {group["id"]: release_group_info(group) for group in data.get("release-groups") or [] if group.get("id")}
        for mbid in chunk:
            _cache_set(f"rg:{mbid}", found.get(mbid))
            if mbid in found:
                result[mbid] = found[mbid]
    return result
