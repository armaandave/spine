import logging
import re
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from django.utils import timezone
from requests_ratelimiter import LimiterSession

from app.models import MediaTypes
from app.providers import services, tmdb
from integrations.models import LetterboxdUriCache

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)
IMDB_RE = re.compile(r"tt\d+")
TMDB_RE = re.compile(r"/movie/(\d+)|themoviedb\.org/movie/(\d+)")
USER_FILM_PATH_RE = re.compile(r"letterboxd\.com/([^/]+)/film/([^/?#]+)")


class LetterboxdResolver:
    """Resolve Letterboxd URLs to TMDB movie IDs."""

    def __init__(self, max_workers=10):
        self.max_workers = max_workers
        self.session = LimiterSession(per_second=5)
        self.session.headers.update({"User-Agent": USER_AGENT})

    def resolve_rows(self, rows):
        """Resolve each film once and return uri -> result for all of its links."""
        uris = {row.get("uri") for row in rows if row.get("uri")}
        cached = set(LetterboxdUriCache.objects.filter(uri__in=uris).values_list("uri", flat=True))
        lookups = _lookups_by_uri(rows, cached)
        resolved = self.resolve_many(sorted(set(lookups.values())), rows)
        return {uri: resolved[lookups[uri]] for uri in uris if lookups[uri] in resolved}

    def resolve_many(self, uris, rows=()):
        """Resolve unique URIs through cache, scrape, IMDb fallback, then search."""
        cached = {
            cache.uri: {
                "tmdb_id": cache.tmdb_id,
                "media_type": cache.media_type,
                "imdb_id": cache.imdb_id,
                "confidence": cache.confidence,
            }
            for cache in LetterboxdUriCache.objects.filter(uri__in=uris)
        }
        by_uri = {row.get("uri"): row for row in rows if row.get("uri")}
        missing = [uri for uri in uris if uri not in cached]
        resolved = dict(cached)

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self.resolve_one, uri, by_uri.get(uri, {})): uri
                for uri in missing
            }
            for future in as_completed(futures):
                uri = futures[future]
                try:
                    result = future.result()
                except (requests.RequestException, services.ProviderAPIError) as error:
                    logger.warning("Could not resolve Letterboxd URI %s: %s", uri, error)
                    result = None
                if result:
                    resolved[uri] = result
                    # A title search can be wrong, so a later import gets to try the film's page.
                    if result.get("confidence") == "search":
                        continue
                    LetterboxdUriCache.objects.update_or_create(
                        uri=uri,
                        defaults={
                            "tmdb_id": str(result["tmdb_id"]),
                            "media_type": MediaTypes.MOVIE.value,
                            "imdb_id": result.get("imdb_id"),
                            "resolved_at": timezone.now(),
                            "confidence": result.get("confidence", ""),
                        },
                    )
        return resolved

    def resolve_one(self, uri, row=None):
        """Resolve one URI."""
        try:
            result = self._resolve_from_page(uri)
        except requests.RequestException as error:
            # Letterboxd may be blocking us; the title search doesn't need it.
            logger.warning("Could not read Letterboxd page %s: %s", uri, error)
            result = None
        if result:
            return result
        return _search_by_name_year(row) if row else None

    def _resolve_from_page(self, uri):
        response = self.session.get(uri, allow_redirects=True, timeout=20)
        response.raise_for_status()
        canonical = _canonical_film_url(getattr(response, "url", ""))
        if canonical:
            response = self.session.get(canonical, allow_redirects=True, timeout=20)
            response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Ported from kopiro/letterboxd-sync: prefer Letterboxd's TMDB link.
        tmdb_id = _tmdb_id_from_html(soup)
        if tmdb_id:
            return {"tmdb_id": str(tmdb_id), "confidence": "scrape"}

        imdb_id = _imdb_id_from_html(soup)
        if imdb_id:
            found = tmdb.find(imdb_id, "imdb_id")
            if found.get("movie_results"):
                return {
                    "tmdb_id": str(found["movie_results"][0]["id"]),
                    "imdb_id": imdb_id,
                    "confidence": "imdb",
                }
        return None


def film_key(row):
    """Identify a film by its name and year, for rows whose links differ."""
    name = (row.get("name") or "").strip().casefold()
    if not name:
        return None
    return (name, row.get("year"))


def _lookups_by_uri(rows, cached):
    """Map each link to the one link looked up for it.

    A film can have many links, one per log or review, and they all share a lookup: one
    already cached, or else the film's own link. Two film links under the same name and
    year are different films, so those look up every link separately.
    """
    lookups = {}
    rows_by_film = defaultdict(list)
    for row in rows:
        uri = row.get("uri")
        if not uri:
            continue
        lookups[uri] = uri
        key = film_key(row)
        if key:
            rows_by_film[key].append(row)

    for film_rows in rows_by_film.values():
        film_links = {row["uri"] for row in film_rows if not row.get("entry_link")}
        if len(film_links) > 1:
            continue
        links = {row["uri"] for row in film_rows}
        chosen = min(links & cached or film_links or links)
        for link in links:
            lookups[link] = chosen
    return lookups


def _canonical_film_url(url):
    """Diary links redirect to /username/film/... pages without TMDB metadata."""
    match = USER_FILM_PATH_RE.search(url)
    if not match or match.group(1) == "film":
        return None
    return f"https://letterboxd.com/film/{match.group(2)}/"


def _tmdb_id_from_html(soup):
    link = soup.select_one('a.micro-button[data-track-action="TMDB"]')
    candidates = []
    if link:
        candidates.extend([link.get("data-tmdb-id"), link.get("href")])
    data_node = soup.select_one("[data-tmdb-id]")
    if data_node:
        candidates.append(data_node.get("data-tmdb-id"))
    for candidate in candidates:
        if not candidate:
            continue
        if str(candidate).isdigit():
            return str(candidate)
        match = TMDB_RE.search(candidate)
        if match:
            return next(group for group in match.groups() if group)
    return None


def _imdb_id_from_html(soup):
    for candidate in soup.select("[href]"):
        match = IMDB_RE.search(candidate.get("href", ""))
        if match:
            return match.group(0)
    match = IMDB_RE.search(soup.get_text(" "))
    return match.group(0) if match else None


def _search_by_name_year(row):
    name = row.get("name", "")
    year = row.get("year")
    for result in tmdb.search("movie", name, page=1).get("results", []):
        if _title_key(result.get("title", "")) != _title_key(name):
            continue
        if not year or str(result.get("release_date", ""))[:4] == str(year):
            return {"tmdb_id": str(result["media_id"]), "confidence": "search"}
    return None


def _title_key(title):
    """Compare titles without case, accents or punctuation."""
    letters = "".join(char for char in unicodedata.normalize("NFKD", title) if char.isalnum())
    return letters.casefold() or title.casefold()
