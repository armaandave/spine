import logging
import re
import time
from datetime import UTC, datetime

import requests
from bs4 import BeautifulSoup
from django.core.cache import cache

from app import helpers
from app.models import MediaTypes, Sources
from app.providers import services

logger = logging.getLogger(__name__)

API_URL = "https://graphql.anilist.co"
FRESH_TTL = 60 * 60 * 24
STALE_TTL = 60 * 60 * 24 * 30
FAILURE_TTL = 60 * 5
REQUEST_TIMEOUT = 3
CACHE_VERSION = "v4"
PERSON_CACHE_VERSION = "v6"
PERSON_PAGE_LIMIT = 20
PERSON_LOCK_TTL = REQUEST_TIMEOUT + 2
PERSON_REFRESH_MARKER_TTL = 60
PERSON_POSTER_CACHE_VERSION = "v2"
PERSON_COMPLETION_CACHE_VERSION = "v1"
REVIEWS_CACHE_VERSION = "v1"
REVIEWS_FRESH_TTL = 60 * 60
REVIEWS_STALE_TTL = 60 * 60 * 24
REVIEWS_PAGE_SIZE = 25

PERSON_SEARCH_QUERY = """
query ($search: String!, $page: Int!, $perPage: Int!) {
  Page(page: $page, perPage: $perPage) {
    staff(search: $search, sort: [SEARCH_MATCH, FAVOURITES_DESC]) {
      id
      name {
        full
        native
      }
      image {
        large
        medium
      }
      primaryOccupations
    }
  }
}
"""

ANIME_SERIES_QUERY = """
query ($malIds: [Int]) {
  Page(page: 1, perPage: 50) {
    media(idMal_in: $malIds, type: ANIME) {
      idMal
      popularity
      title {
        english
        romaji
        native
      }
      relations {
        edges {
          relationType
          node {
            idMal
            type
          }
        }
      }
    }
  }
}
"""

ANIME_EPISODES_QUERY = """
query ($malIds: [Int], $page: Int) {
  Page(page: $page, perPage: 25) {
    pageInfo {
      hasNextPage
    }
    media(idMal_in: $malIds, type: ANIME) {
      idMal
      nextAiringEpisode {
        episode
        airingAt
      }
      airingSchedule(notYetAired: true, perPage: 50) {
        nodes {
          episode
          airingAt
        }
      }
      streamingEpisodes {
        title
        thumbnail
      }
    }
  }
}
"""

STUDIO_ANIME_QUERY = """
query (
  $studioId: Int!
  $seedMalId: Int!
  $page: Int!
  $perPage: Int!
  $sort: [MediaSort]
) {
  studio: Studio(id: $studioId) {
    id
    name
    isAnimationStudio
    media(page: $page, perPage: $perPage, sort: $sort) {
      pageInfo {
        currentPage
        hasNextPage
      }
      nodes {
        idMal
      }
    }
  }
  seed: Media(idMal: $seedMalId, type: ANIME) {
    studios {
      nodes {
        id
        name
      }
    }
  }
}
"""

ANILIST_GENRES = {
    "Action",
    "Adventure",
    "Comedy",
    "Drama",
    "Ecchi",
    "Fantasy",
    "Hentai",
    "Horror",
    "Mahou Shoujo",
    "Mecha",
    "Music",
    "Mystery",
    "Psychological",
    "Romance",
    "Sci-Fi",
    "Slice of Life",
    "Sports",
    "Supernatural",
    "Thriller",
}

ANIME_GENRE_QUERY = """
query (
  $genres: [String]
  $tags: [String]
  $page: Int!
  $perPage: Int!
  $isAdult: Boolean
) {
  Page(page: $page, perPage: $perPage) {
    pageInfo {
      total
      perPage
      currentPage
      lastPage
      hasNextPage
    }
    media(
      type: ANIME
      idMal_not: 0
      genre_in: $genres
      tag_in: $tags
      isAdult: $isAdult
      sort: [POPULARITY_DESC, SCORE_DESC]
    ) {
      idMal
      title {
        english
        romaji
        native
      }
      coverImage {
        extraLarge
        large
        medium
      }
      startDate {
        year
        month
        day
      }
      format
      episodes
      description(asHtml: false)
      genres
      averageScore
      popularity
    }
  }
}
"""

MANGA_GENRE_QUERY = """
query (
  $genres: [String]
  $tags: [String]
  $page: Int!
  $perPage: Int!
  $isAdult: Boolean
) {
  Page(page: $page, perPage: $perPage) {
    pageInfo {
      total
      perPage
      currentPage
      lastPage
      hasNextPage
    }
    media(
      type: MANGA
      idMal_not: 0
      genre_in: $genres
      tag_in: $tags
      isAdult: $isAdult
      sort: [POPULARITY_DESC, SCORE_DESC]
    ) {
      idMal
      title {
        english
        romaji
        native
      }
      coverImage {
        extraLarge
        large
        medium
      }
      startDate {
        year
        month
        day
      }
      format
      chapters
      volumes
      description(asHtml: false)
      genres
      averageScore
      popularity
    }
  }
}
"""

ANIME_QUERY = """
query ($malId: Int!) {
  Media(idMal: $malId, type: ANIME) {
    id
    idMal
    siteUrl
    title {
      english
      romaji
      native
    }
    coverImage {
      extraLarge
      large
      medium
      color
    }
    bannerImage
    averageScore
    stats {
      scoreDistribution {
        score
        amount
      }
    }
    reviews(page: 1, perPage: 1) {
      nodes {
        id
      }
    }
    relations {
      edges {
        relationType
        node {
          id
          idMal
          type
          format
          siteUrl
          title {
            english
            romaji
            native
          }
          coverImage {
            extraLarge
            large
            medium
          }
          startDate {
            year
          }
        }
      }
    }
    characters(page: 1, perPage: 25, sort: [ROLE, RELEVANCE]) {
      edges {
        role
        node {
          id
          name {
            full
            native
          }
          image {
            large
            medium
          }
        }
        voiceActors(sort: [RELEVANCE, ID]) {
          id
          languageV2
          name {
            full
            native
          }
          image {
            large
            medium
          }
        }
      }
    }
  }
}
"""

REVIEWS_QUERY = """
query ($malId: Int!, $page: Int!) {
  Media(idMal: $malId, type: ANIME) {
    reviews(
      page: $page
      perPage: 25
      sort: [RATING_DESC, ID_DESC]
    ) {
      pageInfo {
        currentPage
        hasNextPage
      }
      nodes {
        id
        score
        summary
        body(asHtml: true)
        rating
        ratingAmount
        private
        siteUrl
        createdAt
        user {
          id
          name
          siteUrl
          avatar {
            large
            medium
          }
        }
      }
    }
  }
}
"""

MANGA_QUERY = """
query ($malId: Int!) {
  Media(idMal: $malId, type: MANGA) {
    id
    idMal
    siteUrl
    title {
      english
      romaji
      native
    }
    coverImage {
      extraLarge
      large
      medium
      color
    }
    bannerImage
    averageScore
    stats {
      scoreDistribution {
        score
        amount
      }
    }
    relations {
      edges {
        relationType
        node {
          id
          idMal
          type
          format
          siteUrl
          title {
            english
            romaji
            native
          }
          coverImage {
            extraLarge
            large
            medium
          }
          startDate {
            year
          }
        }
      }
    }
    characters(page: 1, perPage: 12, sort: [ROLE, RELEVANCE]) {
      edges {
        role
        node {
          id
          name {
            full
            native
          }
          image {
            large
            medium
          }
        }
      }
    }
    staff(page: 1, perPage: 12, sort: [RELEVANCE, ID]) {
      edges {
        role
        node {
          id
          name {
            full
            native
          }
          image {
            large
            medium
          }
        }
      }
    }
    recommendations(page: 1, perPage: 12, sort: [RATING_DESC, ID]) {
      nodes {
        mediaRecommendation {
          id
          idMal
          type
          siteUrl
          title {
            english
            romaji
            native
          }
          coverImage {
            extraLarge
            large
            medium
          }
          startDate {
            year
          }
        }
      }
    }
  }
}
"""

STAFF_QUERY = """
query ($id: Int!, $page: Int!) {
  Staff(id: $id) {
    id
    name {
      full
      native
      alternative
    }
    image {
      large
      medium
    }
    description(asHtml: true)
    primaryOccupations
    dateOfBirth {
      year
      month
      day
    }
    dateOfDeath {
      year
      month
      day
    }
    homeTown
    siteUrl
    favourites
    mangaStaffMedia: staffMedia(
      type: MANGA
      page: $page
      perPage: 25
      sort: [POPULARITY_DESC, SCORE_DESC]
    ) {
      pageInfo {
        hasNextPage
      }
      edges {
        staffRole
        node {
          id
          idMal
          siteUrl
          title {
            english
            romaji
            native
          }
          coverImage {
            extraLarge
            large
            medium
          }
          startDate {
            year
            month
            day
          }
          endDate {
            year
            month
            day
          }
          countryOfOrigin
          genres
          averageScore
          popularity
        }
      }
    }
    animeStaffMedia: staffMedia(
      type: ANIME
      page: $page
      perPage: 25
      sort: [POPULARITY_DESC, SCORE_DESC]
    ) {
      pageInfo {
        hasNextPage
      }
      edges {
        staffRole
        node {
          id
          idMal
          siteUrl
          title {
            english
            romaji
            native
          }
          coverImage {
            extraLarge
            large
            medium
          }
          startDate {
            year
            month
            day
          }
          endDate {
            year
            month
            day
          }
          countryOfOrigin
          genres
          averageScore
          popularity
          relations {
            edges {
              relationType
              node {
                idMal
                type
              }
            }
          }
        }
      }
    }
    animeCharacterMedia: characterMedia(
      page: $page
      perPage: 25
      sort: [POPULARITY_DESC, SCORE_DESC]
    ) {
      pageInfo {
        hasNextPage
      }
      edges {
        characterRole
        characters {
          id
          name {
            full
            native
          }
        }
        node {
          id
          idMal
          siteUrl
          title {
            english
            romaji
            native
          }
          coverImage {
            extraLarge
            large
            medium
          }
          startDate {
            year
            month
            day
          }
          endDate {
            year
            month
            day
          }
          countryOfOrigin
          genres
          averageScore
          popularity
          relations {
            edges {
              relationType
              node {
                idMal
                type
              }
            }
          }
        }
      }
    }
  }
}
"""

RELATION_LABELS = {
    "SOURCE": "Source",
    "ADAPTATION": "Adaptation",
    "PREQUEL": "Prequel",
    "SEQUEL": "Sequel",
    "PARENT": "Parent Story",
    "SIDE_STORY": "Side Story",
    "SPIN_OFF": "Spin-off",
    "ALTERNATIVE": "Alternative",
    "CHARACTER": "Character",
    "SUMMARY": "Summary",
    "COMPILATION": "Compilation",
    "CONTAINS": "Contains",
    "OTHER": "Other",
}


def anime(mal_id, *, raise_errors=False):
    """Return cached, normalized AniList enrichment for a MAL anime ID."""
    return _media(
        mal_id,
        media_kind=MediaTypes.ANIME.value,
        query=ANIME_QUERY,
        raise_errors=raise_errors,
    )


def manga(mal_id, *, raise_errors=False):
    """Return cached, normalized AniList enrichment for a MAL manga ID."""
    return _media(
        mal_id,
        media_kind=MediaTypes.MANGA.value,
        query=MANGA_QUERY,
        raise_errors=raise_errors,
    )


def anime_series_nodes(mal_ids, *, timeout=REQUEST_TIMEOUT):
    """Return direct AniList prequel/sequel links for MAL anime IDs."""
    ids = sorted({int(value) for value in mal_ids if str(value).isdigit()})
    results = {}
    for offset in range(0, len(ids), 50):
        response = services.api_request(
            "ANILIST",
            "POST",
            API_URL,
            params={
                "query": ANIME_SERIES_QUERY,
                "variables": {"malIds": ids[offset : offset + 50]},
            },
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=timeout,
            retry_rate_limits=False,
        )
        media = (((response.get("data") or {}).get("Page") or {}).get("media") or [])
        for node in media:
            mal_id = node.get("idMal")
            if mal_id:
                results[str(mal_id)] = {
                    "display_title": _display_title(node.get("title") or {}),
                    "popularity": node.get("popularity"),
                    "series_links": _series_links(node),
                }
    return results


def anime_episodes(mal_ids, *, timeout=None, retry_rate_limits=True):
    """Return what AniList says about MAL anime IDs' episodes, by MAL ID as text.

    One request per 25 titles, never one per episode. Each value is {"next": {"episode",
    "airing_at"} or None, "schedule": {episode: airing Unix seconds}, "episodes":
    {episode: {"title", "image"}}}. A title AniList doesn't have is left out.
    """
    ids = sorted({int(value) for value in mal_ids if str(value).isdigit()})
    results = {}
    for offset in range(0, len(ids), 25):
        chunk = ids[offset : offset + 25]
        page = 1
        while True:
            response = services.api_request(
                "ANILIST",
                "POST",
                API_URL,
                params={
                    "query": ANIME_EPISODES_QUERY,
                    "variables": {"malIds": chunk, "page": page},
                },
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=timeout or REQUEST_TIMEOUT,
                retry_rate_limits=retry_rate_limits,
            )
            connection = (response.get("data") or {}).get("Page") or {}
            for media in connection.get("media") or []:
                if media.get("idMal"):
                    results[str(media["idMal"])] = _anime_episodes(media)
            # A full page reports a next one even with nothing left to ask for.
            answered = all(str(mal_id) in results for mal_id in chunk)
            if answered or not (connection.get("pageInfo") or {}).get("hasNextPage"):
                break
            page += 1
    return results


def _anime_episodes(media):
    next_airing = media.get("nextAiringEpisode") or {}
    schedule = {
        node["episode"]: node["airingAt"]
        for node in (media.get("airingSchedule") or {}).get("nodes") or []
        if node.get("episode") and node.get("airingAt")
    }
    episodes = {}
    for streaming in media.get("streamingEpisodes") or []:
        number, title = parse_episode_title(streaming.get("title"))
        # Order isn't guaranteed, and a title can come once per streaming site.
        if number is not None and number not in episodes:
            episodes[number] = {"title": title, "image": streaming.get("thumbnail") or None}
    return {
        "next": (
            {"episode": next_airing["episode"], "airing_at": next_airing.get("airingAt")}
            if next_airing.get("episode")
            else None
        ),
        "schedule": schedule,
        "episodes": episodes,
    }


def parse_episode_title(value):
    """Return (episode number, title) from AniList's "Episode 5 - Title", or (None, "")."""
    match = re.match(r"^\s*(?:episode|ep\.?)\s*(\d+)\b[\s\-–—:.]*(.*)$", str(value or ""), re.IGNORECASE)
    if match is None:
        return None, ""
    return int(match.group(1)), match.group(2).strip()


def studio_anime_ids(
    studio_id,
    *,
    studio_name,
    seed_anime_id,
    page,
    page_size,
    sort,
    direction,
    timeout=REQUEST_TIMEOUT,
):
    """Return MAL anime IDs only after validating an AniList studio mapping."""
    studio_id = int(studio_id)
    sort_key = {
        "popularity": "POPULARITY",
        "release_date": "START_DATE",
        "average_rating": "SCORE",
        "title": "TITLE_ROMAJI",
    }[sort]
    if direction == "desc":
        sort_key = f"{sort_key}_DESC"
    response = services.api_request(
        "ANILIST",
        "POST",
        API_URL,
        params={
            "query": STUDIO_ANIME_QUERY,
            "variables": {
                "studioId": studio_id,
                "seedMalId": int(seed_anime_id),
                "page": int(page),
                "perPage": int(page_size),
                "sort": [sort_key],
            },
        },
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=timeout,
        retry_rate_limits=False,
    )
    data = response.get("data") or {}
    studio = data.get("studio")
    seed = data.get("seed")
    expected_name = _normalized_studio_name(studio_name)
    if (
        not isinstance(studio, dict)
        or studio.get("id") != studio_id
        or studio.get("isAnimationStudio") is not True
        or _normalized_studio_name(studio.get("name")) != expected_name
        or not expected_name
    ):
        raise ValueError("AniList studio did not match the cached MAL identity")

    seed_studios = ((seed or {}).get("studios") or {}).get("nodes") or []
    if not any(
        node.get("id") == studio_id
        and _normalized_studio_name(node.get("name")) == expected_name
        for node in seed_studios
        if isinstance(node, dict)
    ):
        raise ValueError("AniList studio did not match the cached MAL seed anime")

    connection = studio.get("media") or {}
    page_info = connection.get("pageInfo") or {}
    anime_ids = list(
        dict.fromkeys(
            str(node["idMal"])
            for node in connection.get("nodes") or []
            if isinstance(node, dict) and node.get("idMal")
        ),
    )
    return {
        "anime_ids": anime_ids,
        "page": int(page_info.get("currentPage") or page),
        "has_next_page": bool(page_info.get("hasNextPage")),
    }


def anime_genre_page(
    genre,
    *,
    page,
    page_size,
    include_adult,
    timeout=REQUEST_TIMEOUT,
):
    """Return one AniList anime genre page with MAL-addressable IDs."""
    return _media_genre_page(
        ANIME_GENRE_QUERY,
        "anime",
        genre,
        page=page,
        page_size=page_size,
        include_adult=include_adult,
        timeout=timeout,
    )


def manga_genre_page(
    genre,
    *,
    page,
    page_size,
    include_adult,
    timeout=REQUEST_TIMEOUT,
):
    """Return one AniList manga genre page with MAL-addressable IDs."""
    return _media_genre_page(
        MANGA_GENRE_QUERY,
        "manga",
        genre,
        page=page,
        page_size=page_size,
        include_adult=include_adult,
        timeout=timeout,
    )


def _media_genre_page(
    query,
    media_type,
    genre,
    *,
    page,
    page_size,
    include_adult,
    timeout,
):
    genre = str(genre)
    taxonomy = "genres" if genre in ANILIST_GENRES else "tags"
    response = services.api_request(
        "ANILIST",
        "POST",
        API_URL,
        params={
            "query": query,
            "variables": {
                taxonomy: [genre],
                "page": int(page),
                "perPage": int(page_size),
                "isAdult": None if include_adult else False,
            },
        },
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=timeout,
        retry_rate_limits=False,
    )
    connection = ((response.get("data") or {}).get("Page") or {})
    page_info = connection.get("pageInfo")
    media = connection.get("media")
    if not isinstance(page_info, dict) or not isinstance(media, list):
        raise ValueError(
            f"AniList returned malformed {media_type} discovery data",
        )
    return {
        "page_info": page_info,
        "media": [
            {
                **node,
                "release_date": _fuzzy_date(node.get("startDate")),
            }
            for node in media
            if isinstance(node, dict) and node.get("idMal")
        ],
    }


def _normalized_studio_name(value):
    return re.sub(
        r"[^a-z0-9]+",
        "",
        str(value or "").casefold(),
    )


def anime_reviews(mal_id, page):
    """Return one cached page of public AniList reviews for a MAL anime."""
    page = int(page)
    fresh_key = (
        f"anilist:{REVIEWS_CACHE_VERSION}:anime:{mal_id}:reviews:{page}:fresh"
    )
    stale_key = (
        f"anilist:{REVIEWS_CACHE_VERSION}:anime:{mal_id}:reviews:{page}:stale"
    )
    failure_key = (
        f"anilist:{REVIEWS_CACHE_VERSION}:anime:{mal_id}:reviews:{page}:failure"
    )
    if data := cache.get(fresh_key):
        return data

    stale = cache.get(stale_key)
    if cache.get(failure_key):
        if stale:
            return stale
        raise services.ProviderAPIError(
            "anilist",
            RuntimeError("Cached AniList provider failure"),
        )

    try:
        response = services.api_request(
            "ANILIST",
            "POST",
            API_URL,
            params={
                "query": REVIEWS_QUERY,
                "variables": {"malId": int(mal_id), "page": page},
            },
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        media = (response.get("data") or {}).get("Media")
        connection = (media or {}).get("reviews")
        if not isinstance(connection, dict):
            raise ValueError("AniList returned no matching anime reviews")
        data = _normalize_reviews(connection, requested_page=page)
        cache.set(fresh_key, data, REVIEWS_FRESH_TTL)
        cache.set(stale_key, data, REVIEWS_STALE_TTL)
        cache.delete(failure_key)
        return data
    except (
        requests.RequestException,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        logger.warning(
            "AniList reviews unavailable for MAL anime %s page %s: %s",
            mal_id,
            page,
            error,
        )
        cache.set(failure_key, True, FAILURE_TTL)
        if stale:
            return stale
        raise services.ProviderAPIError("anilist", error) from error


def person_page(
    person_id,
    *,
    page=1,
    enrich_credit_images=True,
    request_session=None,
):
    """Return a cumulative, lazily paged AniList staff profile."""
    page = int(page)
    if not 1 <= page <= PERSON_PAGE_LIMIT:
        raise ValueError(f"AniList person page must be 1-{PERSON_PAGE_LIMIT}")

    pages = []
    for page_number in range(1, page + 1):
        payload = _person_staff_page(
            person_id,
            page_number,
            request_session=request_session,
        )
        pages.append(payload)
        if not payload["has_next_page"]:
            break

    data = _normalize_staff(
        pages[0]["staff"],
        [
            edge
            for payload in pages
            for edge in payload["manga_edges"]
        ],
        [
            edge
            for payload in pages
            for edge in payload["anime_staff_edges"]
        ],
        [
            edge
            for payload in pages
            for edge in payload["anime_voice_edges"]
        ],
    )
    current_page = len(pages)
    provider_has_more = pages[-1]["has_next_page"]
    data["credits_page"] = current_page
    data["credits_next_page"] = (
        current_page + 1
        if current_page < PERSON_PAGE_LIMIT and provider_has_more
        else None
    )
    data["credits_complete"] = not provider_has_more
    data["credits_truncated"] = (
        current_page == PERSON_PAGE_LIMIT and provider_has_more
    )
    if enrich_credit_images:
        _apply_mal_anime_credit_images(person_id, pages[0]["staff"], data)
    return data


def search_people(query, *, limit=10, timeout=None):
    """Search AniList staff and return normalized person references."""
    response = services.api_request(
        "ANILIST",
        "POST",
        API_URL,
        params={
            "query": PERSON_SEARCH_QUERY,
            "variables": {
                "search": query,
                "page": 1,
                "perPage": limit,
            },
        },
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=timeout or REQUEST_TIMEOUT,
        retry_rate_limits=False,
        request_session=services.person_search_session,
    )
    results = []
    staff = ((response.get("data") or {}).get("Page") or {}).get("staff") or []
    for person in staff:
        name = person.get("name") or {}
        display_name = name.get("full") or name.get("native")
        if person.get("id") is None or not display_name:
            continue
        image = person.get("image") or {}
        occupations = person.get("primaryOccupations") or []
        results.append({
            "source": "anilist",
            "person_id": str(person["id"]),
            "name": display_name,
            "profile_url": image.get("large") or image.get("medium") or None,
            "known_for_department": occupations[0] if occupations else None,
        })
    return results[:limit]


def refresh_person_page(person_id, page):
    """Refresh one cached AniList staff page outside the request path."""
    return _refresh_person_staff_page(person_id, int(page))


def refresh_person_credit_images(
    person_id,
    name,
    alternative_names=None,
    birth_date=None,
):
    """Cache canonical MAL posters for an AniList person's anime credits."""
    try:
        from app.providers import mal  # noqa: PLC0415

        mal_person = mal.person_page_by_name(
            name,
            alternative_names,
            birth_date,
            timeout=1,
            request_session=requests,
        )
    except (
        requests.RequestException,
        services.ProviderAPIError,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        logger.warning("MAL poster enrichment unavailable for %s: %s", name, error)
        cache.set(_person_poster_key(person_id), {}, FAILURE_TTL)
        return {}
    mal_images = {
        (credit.get("media_type"), str(credit.get("media_id") or "")): credit.get("image")
        for credit in (mal_person or {}).get("credits") or []
        if credit.get("image")
    }
    cache.set(_person_poster_key(person_id), mal_images, STALE_TTL)
    return mal_images


def person_completion_credits(person_id, person):
    """Return one cached, fully enumerated MAL match for AniList staff."""
    cached = cache.get(_person_completion_key(person_id))
    if cached is not None:
        return cached.get("credits") if cached["complete"] else None
    if cache.get(_person_completion_failure_key(person_id)):
        return None

    try:
        from app.providers import mal  # noqa: PLC0415

        mal_person = mal.person_page_by_name(
            person.get("name"),
            person.get("alternative_names"),
            person.get("birth_date"),
            timeout=REQUEST_TIMEOUT,
            request_session=services.person_search_session,
            strict=True,
        )
    except (
        requests.RequestException,
        services.ProviderAPIError,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        logger.warning(
            "MAL completion match unavailable for AniList person %s: %s",
            person_id,
            error,
        )
        cache.set(_person_completion_failure_key(person_id), True, FAILURE_TTL)
        return None

    payload = _cache_person_completion(person_id, mal_person)
    return payload.get("credits") if payload["complete"] else None


def _person_staff_page(person_id, page, *, request_session=None):
    fresh_key, stale_key, failure_key, _ = _person_page_keys(person_id, page)
    if data := cache.get(fresh_key):
        return data

    stale = cache.get(stale_key)
    if stale is not None:
        if not cache.get(failure_key):
            _schedule_person_page_refresh(person_id, page)
        return stale
    if cache.get(failure_key):
        raise services.ProviderAPIError(
            "anilist",
            RuntimeError("Cached AniList person failure"),
        )
    return _refresh_person_staff_page(
        person_id,
        page,
        request_session=request_session,
    )


def _refresh_person_staff_page(person_id, page, *, request_session=None):
    fresh_key, stale_key, failure_key, lock_key = _person_page_keys(person_id, page)
    stale = cache.get(stale_key)
    if not cache.add(lock_key, 1, timeout=PERSON_LOCK_TTL):
        deadline = time.monotonic() + PERSON_LOCK_TTL
        while time.monotonic() < deadline:
            if data := cache.get(fresh_key):
                return data
            if cache.get(failure_key):
                break
            time.sleep(0.05)
        if stale is not None:
            return stale
        raise services.ProviderAPIError(
            "anilist",
            RuntimeError("AniList person refresh is already in progress"),
        )

    try:
        response = services.api_request(
            "ANILIST",
            "POST",
            API_URL,
            params={
                "query": STAFF_QUERY,
                "variables": {"id": int(person_id), "page": page},
            },
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=REQUEST_TIMEOUT,
            retry_rate_limits=False,
            # Person pages must fail inside the native client's request budget.
            # A provider 429 is cached below instead of occupying a web worker.
            request_session=request_session or requests,
        )
        current = (response.get("data") or {}).get("Staff")
        if not isinstance(current, dict):
            services.raise_not_found_error("anilist", person_id, "person")
        data = _staff_page_payload(current)
        cache.set(fresh_key, data, FRESH_TTL)
        cache.set(stale_key, data, STALE_TTL)
        cache.delete(failure_key)
        return data
    except (
        requests.RequestException,
        services.ProviderAPIError,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        cache.set(failure_key, True, FAILURE_TTL)
        if stale is not None:
            return stale
        if isinstance(error, services.ProviderAPIError):
            raise
        raise services.ProviderAPIError("anilist", error) from error
    finally:
        cache.delete(lock_key)


def _staff_page_payload(staff):
    manga_connection = staff.get("mangaStaffMedia") or staff.get("staffMedia") or {}
    anime_staff_connection = staff.get("animeStaffMedia") or {}
    anime_voice_connection = staff.get("animeCharacterMedia") or {}
    profile = {
        key: value
        for key, value in staff.items()
        if key
        not in {
            "staffMedia",
            "mangaStaffMedia",
            "animeStaffMedia",
            "animeCharacterMedia",
        }
    }
    connections = (
        manga_connection,
        anime_staff_connection,
        anime_voice_connection,
    )
    return {
        "staff": profile,
        "manga_edges": manga_connection.get("edges") or [],
        "anime_staff_edges": anime_staff_connection.get("edges") or [],
        "anime_voice_edges": anime_voice_connection.get("edges") or [],
        "has_next_page": any(
            (connection.get("pageInfo") or {}).get("hasNextPage") is True
            for connection in connections
        ),
    }


def _person_page_keys(person_id, page):
    prefix = f"anilist:{PERSON_CACHE_VERSION}:person:{person_id}:page:{page}"
    return (
        f"{prefix}:fresh",
        f"{prefix}:stale",
        f"{prefix}:failure",
        f"{prefix}:lock",
    )


def _person_poster_key(person_id):
    return (
        f"anilist:{PERSON_POSTER_CACHE_VERSION}:person:{person_id}:mal-credit-images"
    )


def _person_completion_key(person_id):
    return (
        f"anilist:{PERSON_COMPLETION_CACHE_VERSION}:person:{person_id}:"
        "completion-credits"
    )


def _person_completion_failure_key(person_id):
    return f"{_person_completion_key(person_id)}:failure"


def _cache_person_completion(person_id, mal_person):
    payload = {
        "complete": mal_person is not None,
        "credits": list((mal_person or {}).get("credits") or []),
    }
    cache.set(
        _person_completion_key(person_id),
        payload,
        STALE_TTL if payload["complete"] else FRESH_TTL,
    )
    cache.delete(_person_completion_failure_key(person_id))
    return payload


def _schedule_person_page_refresh(person_id, page):
    marker = (
        f"anilist:{PERSON_CACHE_VERSION}:person:{person_id}:page:{page}:refresh"
    )
    if not cache.add(marker, 1, timeout=PERSON_REFRESH_MARKER_TTL):
        return
    try:
        from app.tasks import refresh_anilist_person_page  # noqa: PLC0415

        refresh_anilist_person_page.delay(str(person_id), int(page), marker)
    except Exception:
        cache.delete(marker)
        logger.exception(
            "Could not enqueue AniList person refresh id=%s page=%s",
            person_id,
            page,
        )


def _apply_mal_anime_credit_images(person_id, staff, person):
    from app.providers import mal  # noqa: PLC0415

    anime_credits = [
        credit
        for credit in person.get("credits") or []
        if credit.get("media_type") == MediaTypes.ANIME.value
    ]
    if not anime_credits:
        return
    poster_key = _person_poster_key(person_id)
    images = cache.get(poster_key)
    if images is None:
        name = staff.get("name") or {}
        images = refresh_person_credit_images(
            person_id,
            name.get("full") or name.get("native") or "",
            _alternative_staff_names(name),
            _fuzzy_date(staff.get("dateOfBirth")),
        )
    completion = cache.get(_person_completion_key(person_id))
    if completion and completion["complete"]:
        person["_completion_credits"] = completion["credits"]
    if not anime_credits:
        return
    cached_images = mal.cached_anime_posters(
        credit.get("media_id") for credit in anime_credits
    )
    for credit in anime_credits:
        media_id = str(credit.get("media_id") or "")
        image = images.get(
            (MediaTypes.ANIME.value, media_id),
        ) or cached_images.get(media_id)
        if image:
            credit["image"] = image


def _media(mal_id, *, media_kind, query, raise_errors):
    fresh_key = f"anilist:{CACHE_VERSION}:{media_kind}:{mal_id}:fresh"
    stale_key = f"anilist:{CACHE_VERSION}:{media_kind}:{mal_id}:stale"
    failure_key = f"anilist:{CACHE_VERSION}:{media_kind}:{mal_id}:failure"
    if data := cache.get(fresh_key):
        return data

    stale = cache.get(stale_key)
    if cache.get(failure_key):
        if stale:
            return stale
        if raise_errors:
            raise RuntimeError("Cached AniList provider failure")
        return {}

    try:
        response = services.api_request(
            "ANILIST",
            "POST",
            API_URL,
            params={"query": query, "variables": {"malId": int(mal_id)}},
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        media = (response.get("data") or {}).get("Media")
        if not isinstance(media, dict):
            raise ValueError(f"AniList returned no matching {media_kind}")
        data = _normalize_media(media, media_kind=media_kind)
        cache.set(fresh_key, data, FRESH_TTL)
        cache.set(stale_key, data, STALE_TTL)
        cache.delete(failure_key)
        return data
    except (
        requests.RequestException,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        logger.warning(
            "AniList enrichment unavailable for MAL %s %s: %s",
            media_kind,
            mal_id,
            error,
        )
        cache.set(failure_key, True, FAILURE_TTL)
        if stale:
            return stale
        if raise_errors:
            raise
        return {}


def _normalize_media(media, *, media_kind=MediaTypes.ANIME.value):
    title = media.get("title") or {}
    cover = media.get("coverImage") or {}
    site_url = media.get("siteUrl") or (
        f"https://anilist.co/{media_kind}/{media['id']}"
    )
    cover_url = cover.get("extraLarge") or cover.get("large") or cover.get("medium")
    score_distribution = _normalize_score_distribution(
        (media.get("stats") or {}).get("scoreDistribution") or [],
    )
    rating_count = sum(bucket["count"] for bucket in score_distribution)
    average_score = _bounded_int(media.get("averageScore"), minimum=0, maximum=100)
    has_reviews = bool(((media.get("reviews") or {}).get("nodes") or []))

    posters = []
    if cover_url:
        posters.append(
            {
                "url": cover_url,
                "thumbnail_url": cover.get("medium") or cover_url,
                "provider_name": "AniList",
                "provider_url": site_url,
            },
        )

    backdrops = []
    if media.get("bannerImage"):
        backdrops.append(
            {
                "url": media["bannerImage"],
                "thumbnail_url": media["bannerImage"],
                "provider_name": "AniList",
                "provider_url": site_url,
            },
        )

    return {
        "anilist_id": str(media["id"]),
        "source_url": site_url,
        "titles": {
            "english": title.get("english"),
            "romaji": title.get("romaji"),
            "native": title.get("native"),
        },
        "display_title": _display_title(title),
        "posters": posters,
        "backdrops": backdrops,
        "backdrop": media.get("bannerImage"),
        "cover_color": cover.get("color"),
        "rating": {
            "value": average_score,
            "vote_count": rating_count,
            "url": site_url,
        },
        "rating_summary": {
            "average_score": average_score,
            "rating_count": rating_count,
            "score_distribution": score_distribution,
            "has_reviews": has_reviews,
            "url": site_url,
        },
        "relations": _relations(media),
        "characters": _characters(media),
        "cast": _cast(media) if media_kind == MediaTypes.ANIME.value else [],
        "creators": _staff(media) if media_kind == MediaTypes.MANGA.value else [],
        "recommendations": (
            _recommendations(media)
            if media_kind == MediaTypes.MANGA.value
            else []
        ),
    }


def _normalize_score_distribution(buckets):
    counts = {}
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        score = _bounded_int(bucket.get("score"), minimum=1, maximum=100)
        count = _bounded_int(bucket.get("amount"), minimum=0)
        if score is not None and count is not None:
            counts[score] = counts.get(score, 0) + count
    return [
        {"score": score, "count": count}
        for score, count in sorted(counts.items())
    ]


def _normalize_reviews(connection, *, requested_page):
    reviews = []
    seen = set()
    for review in connection.get("nodes") or []:
        normalized = _normalize_review(review)
        if normalized and normalized["id"] not in seen:
            seen.add(normalized["id"])
            reviews.append(normalized)

    page_info = connection.get("pageInfo") or {}
    current_page = _bounded_int(page_info.get("currentPage"), minimum=1)
    current_page = current_page or requested_page
    return {
        "current_page": current_page,
        "next_page": current_page + 1
        if page_info.get("hasNextPage") is True
        else None,
        "results": reviews[:REVIEWS_PAGE_SIZE],
    }


def _normalize_review(review):
    if not isinstance(review, dict) or review.get("private"):
        return None
    user = review.get("user") or {}
    if not isinstance(user, dict):
        return None
    review_id = _bounded_int(review.get("id"), minimum=1)
    user_id = _bounded_int(user.get("id"), minimum=1)
    name = str(user.get("name") or "").strip()
    body = helpers.plain_text(review.get("body"))
    if review_id is None or user_id is None or not name or not body:
        return None

    avatar = user.get("avatar") or {}
    if not isinstance(avatar, dict):
        avatar = {}
    return {
        "id": str(review_id),
        "user": {
            "id": str(user_id),
            "name": name,
            "avatar_url": avatar.get("large") or avatar.get("medium"),
            "profile_url": user.get("siteUrl"),
        },
        "score": _bounded_int(review.get("score"), minimum=0, maximum=100),
        "summary": helpers.plain_text(review.get("summary")),
        "body": body,
        "community_rating": _integer(review.get("rating")),
        "community_rating_count": _bounded_int(
            review.get("ratingAmount"),
            minimum=0,
        ),
        "url": review.get("siteUrl"),
        "created_at": _iso_timestamp(review.get("createdAt")),
    }


def _integer(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bounded_int(value, *, minimum=None, maximum=None):
    value = _integer(value)
    if value is None or (minimum is not None and value < minimum):
        return None
    if maximum is not None and value > maximum:
        return None
    return value


def _iso_timestamp(value):
    timestamp = _bounded_int(value, minimum=0)
    if timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(timestamp, tz=UTC).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _display_title(title):
    return title.get("english") or title.get("romaji") or title.get("native")


def _relations(media):
    relations = []
    for edge in ((media.get("relations") or {}).get("edges") or []):
        if not isinstance(edge, dict):
            continue
        node = edge.get("node") or {}
        mal_id = node.get("idMal")
        node_type = node.get("type")
        if not mal_id or node_type not in {"ANIME", "MANGA"}:
            continue
        title = node.get("title") or {}
        cover = node.get("coverImage") or {}
        media_type = (
            MediaTypes.ANIME.value
            if node_type == "ANIME"
            else MediaTypes.MANGA.value
        )
        relations.append(
            {
                "media_id": str(mal_id),
                "source": Sources.MAL.value,
                "media_type": media_type,
                "title": title.get("romaji") or title.get("native") or title.get("english") or "",
                "display_title": _display_title(title),
                "image": cover.get("extraLarge") or cover.get("large") or cover.get("medium"),
                "release_date": str((node.get("startDate") or {}).get("year") or "") or None,
                "relation": relation_label(edge.get("relationType")),
                "source_url": node.get("siteUrl"),
            },
        )
    return relations


def _series_links(media):
    links = []
    for edge in ((media.get("relations") or {}).get("edges") or []):
        node = (edge or {}).get("node") or {}
        relation = str((edge or {}).get("relationType") or "").upper()
        mal_id = node.get("idMal")
        if relation in {"PREQUEL", "SEQUEL"} and node.get("type") == "ANIME" and mal_id:
            links.append({"media_id": str(mal_id), "relation": relation.title()})
    return links


def relation_label(value):
    normalized = str(value or "OTHER").upper()
    return RELATION_LABELS.get(normalized, normalized.replace("_", " ").title())


def _characters(media):
    characters = []
    for edge in ((media.get("characters") or {}).get("edges") or [])[:25]:
        node = edge.get("node") or {}
        name = (node.get("name") or {}).get("full") or (node.get("name") or {}).get("native")
        if not name:
            continue
        image = node.get("image") or {}
        characters.append(
            {
                "person_id": f"character:{node.get('id')}",
                "name": name,
                "role": str(edge.get("role") or "").replace("_", " ").title() or None,
                "image": image.get("large") or image.get("medium"),
            },
        )
    return characters


def _staff(media):
    creators = []
    for edge in ((media.get("staff") or {}).get("edges") or [])[:12]:
        node = edge.get("node") or {}
        name = (node.get("name") or {}).get("full") or (
            node.get("name") or {}
        ).get("native")
        if not name:
            continue
        image = node.get("image") or {}
        person_id = node.get("id")
        creators.append({
            "person_id": str(person_id or ""),
            **({"person_source": "anilist"} if person_id else {}),
            "name": name,
            "role": str(edge.get("role") or "").strip() or None,
            "image": image.get("large") or image.get("medium"),
        })
    return creators


def _normalize_staff(staff, manga_edges, anime_staff_edges, anime_voice_edges):
    name = staff.get("name") or {}
    image = staff.get("image") or {}
    credits = []
    positions = {}
    for edge in manga_edges:
        _merge_staff_credit(
            credits,
            positions,
            edge,
            media_type=MediaTypes.MANGA.value,
            role=str((edge or {}).get("staffRole") or "").strip() or "Author",
        )
    for edge in anime_staff_edges:
        _merge_staff_credit(
            credits,
            positions,
            edge,
            media_type=MediaTypes.ANIME.value,
            role=str((edge or {}).get("staffRole") or "").strip() or "Staff",
        )
    for edge in anime_voice_edges:
        _merge_staff_credit(
            credits,
            positions,
            edge,
            media_type=MediaTypes.ANIME.value,
            role="Voice Actor",
            character_role=(edge or {}).get("characterRole"),
        )

    occupations = [
        str(value).strip()
        for value in staff.get("primaryOccupations") or []
        if str(value).strip()
    ]
    return {
        "source": "anilist",
        "person_id": str(staff.get("id") or ""),
        "name": name.get("full") or name.get("native") or "",
        "alternative_names": _alternative_staff_names(name),
        "image": image.get("large") or image.get("medium"),
        "biography": _staff_biography(staff.get("description")),
        "known_for_department": (
            occupations[0]
            if occupations
            else _known_for_department(credits)
        ),
        "birth_date": _fuzzy_date(staff.get("dateOfBirth")),
        "death_date": _fuzzy_date(staff.get("dateOfDeath")),
        "place_of_birth": staff.get("homeTown"),
        "popularity": staff.get("favourites"),
        "credits": credits,
    }


def _merge_staff_credit(
    credits,
    positions,
    edge,
    *,
    media_type,
    role,
    character_role=None,
):
    node = (edge or {}).get("node") or {}
    mal_id = node.get("idMal")
    if not mal_id:
        return
    key = (media_type, str(mal_id))
    if key in positions:
        current = credits[positions[key]]
        if role not in current["credit_roles"]:
            current["roles"].append(role)
            current["credit_roles"].append(role)
        if _character_role_order(character_role) < _character_role_order(
            current.get("character_role"),
        ):
            current["character_role"] = character_role
        current_links = current.setdefault("series_links", [])
        for link in _series_links(node):
            if link not in current_links:
                current_links.append(link)
        return

    title = node.get("title") or {}
    cover = node.get("coverImage") or {}
    score = node.get("averageScore")
    display_title = (
        title.get("english")
        or title.get("romaji")
        or title.get("native")
        or ""
    )
    positions[key] = len(credits)
    credits.append({
        "media_type": media_type,
        "source": Sources.MAL.value,
        "media_id": str(mal_id),
        "title": display_title,
        "display_title": display_title,
        "image": cover.get("extraLarge") or cover.get("large") or cover.get("medium"),
        "release_date": _fuzzy_date(node.get("startDate")),
        "year": str((node.get("startDate") or {}).get("year") or "") or None,
        "genres": node.get("genres") or [],
        "languages": [_language_name(node.get("countryOfOrigin"))]
        if node.get("countryOfOrigin")
        else [],
        "roles": [role],
        "credit_roles": [role],
        "character_role": character_role,
        "vote_average": score / 10 if isinstance(score, (int, float)) else None,
        "vote_count": node.get("popularity"),
        "series_links": _series_links(node),
        "url": f"https://myanimelist.net/{media_type}/{mal_id}",
    })


def _staff_biography(value):
    if not value:
        return None
    html = re.sub(
        r"""<span class=['"]markdown_spoiler['"]>.*?</span>\s*</span>""",
        "",
        str(value),
        flags=re.DOTALL | re.IGNORECASE,
    )
    soup = BeautifulSoup(html, "html.parser")
    for spoiler in soup.select(".markdown_spoiler"):
        parent = spoiler.parent if spoiler.parent and spoiler.parent.name == "p" else None
        spoiler.decompose()
        if parent and len(parent.get_text(" ", strip=True)) <= 80:
            parent.decompose()

    for paragraph in soup.find_all("p"):
        text = paragraph.get_text(" ", strip=True)
        if len(text) <= 80 and re.search(r"\b(?:roles|credits):?$", text, re.IGNORECASE):
            paragraph.decompose()
            continue
        links = paragraph.find_all("a")
        if not links:
            continue
        residual = paragraph.get_text(" ", strip=True)
        for link in links:
            residual = residual.replace(link.get_text(" ", strip=True), "")
        if not re.sub(r"[\s|·,/&-]+", "", residual):
            paragraph.decompose()

    blocks = soup.find_all(["p", "li"])
    if not blocks:
        return _clean_staff_text(helpers.plain_text(value))
    paragraphs = []
    for block in blocks:
        if block.find_parent(["p", "li"]):
            continue
        text = _clean_staff_text(block.get_text(" ", strip=True))
        if text and text not in paragraphs:
            paragraphs.append(text)
    return "\n\n".join(paragraphs) or None


def _clean_staff_text(value):
    if not value:
        return None
    text = re.sub(r"~!.*?!~", "", value, flags=re.DOTALL)
    text = re.sub(r"(\*\*|__|~~|~!|!~)", "", text)
    return " ".join(text.split()) or None


def _character_role_order(value):
    return {
        "MAIN": 0,
        "SUPPORTING": 1,
        "BACKGROUND": 2,
    }.get(str(value or "").upper(), 3)


def _alternative_staff_names(name):
    values = [name.get("native"), *(name.get("alternative") or [])]
    primary = str(name.get("full") or "").strip().casefold()
    seen = {primary} if primary else set()
    results = []
    for value in values:
        clean = str(value or "").strip()
        if clean and clean.casefold() not in seen:
            seen.add(clean.casefold())
            results.append(clean)
    return results


def _known_for_department(credits):
    roles = [
        role
        for credit in credits
        for role in credit.get("credit_roles") or []
    ]
    if "Voice Actor" in roles:
        return "Voice Actor"
    return roles[0] if roles else None


def _fuzzy_date(value):
    value = value or {}
    year = value.get("year")
    if not year:
        return None
    month = value.get("month")
    day = value.get("day")
    if month and day:
        return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    if month:
        return f"{int(year):04d}-{int(month):02d}"
    return str(year)


def _language_name(country_code):
    return {
        "CN": "Chinese",
        "JP": "Japanese",
        "KR": "Korean",
        "TW": "Chinese",
    }.get(str(country_code or "").upper(), str(country_code or "").upper())


def _recommendations(media):
    recommendations = []
    nodes = ((media.get("recommendations") or {}).get("nodes") or [])
    for recommendation in nodes[:12]:
        node = (recommendation or {}).get("mediaRecommendation") or {}
        mal_id = node.get("idMal")
        node_type = node.get("type")
        if not mal_id or node_type not in {"ANIME", "MANGA"}:
            continue
        title = node.get("title") or {}
        cover = node.get("coverImage") or {}
        recommendations.append(
            {
                "media_id": str(mal_id),
                "source": Sources.MAL.value,
                "media_type": (
                    MediaTypes.ANIME.value
                    if node_type == "ANIME"
                    else MediaTypes.MANGA.value
                ),
                "title": (
                    title.get("romaji")
                    or title.get("native")
                    or title.get("english")
                    or ""
                ),
                "display_title": _display_title(title),
                "image": (
                    cover.get("extraLarge")
                    or cover.get("large")
                    or cover.get("medium")
                ),
                "release_date": (
                    str((node.get("startDate") or {}).get("year") or "")
                    or None
                ),
                "source_url": node.get("siteUrl"),
            },
        )
    return recommendations


def _cast(media):
    cast = []
    positions = {}
    for edge in ((media.get("characters") or {}).get("edges") or [])[:25]:
        character = edge.get("node") or {}
        character_name = (character.get("name") or {}).get("full") or (
            character.get("name") or {}
        ).get("native")
        voice_actor = _preferred_voice_actor(edge.get("voiceActors") or [])
        if not character_name or not voice_actor:
            continue
        name = (voice_actor.get("name") or {}).get("full") or (
            voice_actor.get("name") or {}
        ).get("native")
        if not name:
            continue
        image = voice_actor.get("image") or {}
        person_id = voice_actor.get("id")
        if not person_id:
            continue
        key = str(person_id)
        if key in positions:
            current = cast[positions[key]]
            characters = current["character"].split(" · ")
            if character_name not in characters:
                current["character"] = " · ".join([*characters, character_name])
            continue
        positions[key] = len(cast)
        cast.append({
            "person_id": key,
            "person_source": "anilist",
            "name": name,
            "character": character_name,
            "image": image.get("large") or image.get("medium"),
        })
    return cast


def _preferred_voice_actor(voice_actors):
    if not voice_actors:
        return None
    return next(
        (
            actor
            for actor in voice_actors
            if str(actor.get("languageV2") or "").lower() == "japanese"
        ),
        voice_actors[0],
    )
