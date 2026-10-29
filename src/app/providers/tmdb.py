import logging
from datetime import timedelta

import requests
from django.conf import settings
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import get_language_info

from app import helpers
from app.models import MediaTypes, Sources
from app.providers import services
from app.providers.search_rank import rank_results

logger = logging.getLogger(__name__)
base_url = "https://api.themoviedb.org/3"
# v5: shows fetched with their seasons carry credits too (cast for ItemCredit).
DETAIL_CACHE_VERSION = "v5"
MOVIE_DETAIL_CACHE_VERSION = "v5"
COLLECTION_CACHE_VERSION = "v1"
base_params = {
    "api_key": settings.TMDB_API,
    "language": settings.TMDB_LANG,
}


def handle_error(error):
    """Handle TMDB API errors."""
    error_resp = error.response
    status_code = error_resp.status_code

    try:
        error_json = error_resp.json()
    except requests.exceptions.JSONDecodeError as json_error:
        logger.exception("Failed to decode JSON response")
        raise services.ProviderAPIError(Sources.TMDB.value, error) from json_error

    # Handle authentication errors
    if status_code == requests.codes.unauthorized:
        details = error_json.get("status_message")
        if details:
            # Remove trailing period if present
            details = details.rstrip(".")
            raise services.ProviderAPIError(Sources.TMDB.value, error, details)

    raise services.ProviderAPIError(
        Sources.TMDB.value,
        error,
    )


def get_external_links(external_ids, tmdb_id=None):
    """Build external links dictionary from TMDB external_ids response."""
    links = {}

    if external_ids.get("imdb_id"):
        links["IMDb"] = f"https://www.imdb.com/title/{external_ids['imdb_id']}/"

    if external_ids.get("tvdb_id"):
        links["TVDB"] = (
            f"https://www.thetvdb.com/dereferrer/series/{external_ids['tvdb_id']}"
        )

    if external_ids.get("wikidata_id"):
        links["Wikidata"] = (
            f"https://www.wikidata.org/wiki/{external_ids['wikidata_id']}"
        )

    if tmdb_id:
        links["Letterboxd"] = f"https://www.letterboxd.com/tmdb/{tmdb_id}"

    return links


def search(media_type, query, page, *, preserve_ranking_fields=False, timeout=None):
    """Search for media on TMDB."""
    rank_suffix = "_rank" if preserve_ranking_fields else ""
    cache_key = f"search_{Sources.TMDB.value}_{media_type}_{query}_{page}{rank_suffix}"
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/search/{media_type}"

        params = {
            **base_params,
            "query": query,
            "page": page,
        }

        if settings.TMDB_NSFW:
            params["include_adult"] = "true"

        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
                timeout=timeout,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        results = [
            {
                "media_id": media["id"],
                "source": Sources.TMDB.value,
                "media_type": media_type,
                "title": get_title(media),
                "image": get_image_url(media["poster_path"]),
                "popularity": media.get("popularity"),
                "vote_count": media.get("vote_count"),
                "release_date": media.get("release_date") or media.get("first_air_date"),
            }
            for media in response["results"]
        ]
        results = rank_results(
            query,
            results,
            media_type,
            preserve_ranking_fields=preserve_ranking_fields,
        )

        total_results = response["total_results"]
        per_page = 20  # TMDB always returns 20 results per page
        data = helpers.format_search_response(
            page,
            per_page,
            total_results,
            results,
        )

        cache.set(cache_key, data)

    return data


def search_people(query, *, limit=10, timeout=None):
    """Search TMDB people and return normalized person references."""
    params = {
        **base_params,
        "query": query,
        "page": 1,
    }
    if settings.TMDB_NSFW:
        params["include_adult"] = "true"
    try:
        response = services.api_request(
            Sources.TMDB.value,
            "GET",
            f"{base_url}/search/person",
            params=params,
            timeout=timeout,
            retry_rate_limits=False,
            request_session=services.person_search_session,
        )
    except requests.exceptions.HTTPError as error:
        handle_error(error)

    return [
        {
            "source": Sources.TMDB.value,
            "person_id": str(person["id"]),
            "name": person.get("name") or "",
            "profile_url": (
                f"https://image.tmdb.org/t/p/w500{person['profile_path']}"
                if person.get("profile_path")
                else None
            ),
            "known_for_department": person.get("known_for_department") or None,
        }
        for person in (response.get("results") or [])[:limit]
        if person.get("id") is not None and person.get("name")
    ]


def _normalize_name(value):
    return slugify(str(value or "")).casefold()


def _genre_map(media_type):
    cache_key = f"{Sources.TMDB.value}_{media_type}_genre_map"
    data = cache.get(cache_key)
    if data is None:
        url = f"{base_url}/genre/{media_type}/list"
        try:
            response = services.api_request(Sources.TMDB.value, "GET", url, params=base_params)
        except requests.exceptions.HTTPError as error:
            handle_error(error)
        data = {_normalize_name(genre["name"]): genre["id"] for genre in response.get("genres", [])}
        cache.set(cache_key, data, 60 * 60 * 24 * 7)
    return data


def _genre_name_map(media_type):
    cache_key = f"{Sources.TMDB.value}_{media_type}_genre_name_map"
    data = cache.get(cache_key)
    if data is None:
        url = f"{base_url}/genre/{media_type}/list"
        try:
            response = services.api_request(Sources.TMDB.value, "GET", url, params=base_params)
        except requests.exceptions.HTTPError as error:
            handle_error(error)
        data = {genre["id"]: genre["name"] for genre in response.get("genres", [])}
        cache.set(cache_key, data, 60 * 60 * 24 * 7)
    return data


def _genre_names_from_ids(media_type, genre_ids):
    genre_names = _genre_name_map(media_type)
    return [genre_names[genre_id] for genre_id in genre_ids or [] if genre_id in genre_names]


def _language_name(code):
    if not code:
        return None
    try:
        return get_language_info(code).get("name") or code.upper()
    except KeyError:
        return code.upper()


TV_GENRE_ALIASES = {
    "action": "action-adventure",
    "adventure": "action-adventure",
    "fantasy": "sci-fi-fantasy",
    "science-fiction": "sci-fi-fantasy",
    "sci-fi": "sci-fi-fantasy",
    "scifi": "sci-fi-fantasy",
}


def _genre_id(media_type, genre):
    genres = _genre_map(media_type)
    normalized = _normalize_name(genre)
    genre_id = genres.get(normalized)
    if genre_id or media_type != MediaTypes.TV.value:
        return genre_id
    alias = TV_GENRE_ALIASES.get(normalized)
    return genres.get(alias)


def discover(media_type, *, page=1, genre=None, year=None, watch_region=None, watch_providers=None):
    """Discover TMDB movies/TV shows by genre, year, and/or streaming service.

    ``watch_providers`` keeps titles streamable (stream, free, or ads) on any of
    those providers in ``watch_region``.
    """
    cache_key = f"discover_{Sources.TMDB.value}_{media_type}_{genre}_{year}_{page}_{settings.TMDB_LANG}_{settings.TMDB_NSFW}"
    providers = None
    if watch_providers:
        providers = "|".join(str(provider_id) for provider_id in sorted(watch_providers))
        cache_key = f"{cache_key}_watch_{watch_region}_{providers}"
    data = cache.get(cache_key)
    if data is None:
        params = {
            **base_params,
            "page": page,
            "sort_by": "vote_count.desc",
        }
        if settings.TMDB_NSFW:
            params["include_adult"] = "true"
        if providers:
            from streaming.models import STREAMABLE_MONETIZATIONS

            params["watch_region"] = watch_region
            params["with_watch_providers"] = providers
            params["with_watch_monetization_types"] = "|".join(STREAMABLE_MONETIZATIONS)
        if genre:
            genre_id = _genre_id(media_type, genre)
            if not genre_id:
                msg = f"Unknown TMDB {media_type} genre: {genre}"
                raise ValueError(msg)
            params["with_genres"] = genre_id
        if year:
            params["primary_release_year" if media_type == MediaTypes.MOVIE.value else "first_air_date_year"] = year

        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                f"{base_url}/discover/{media_type}",
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        results = [
            {
                "media_id": media["id"],
                "source": Sources.TMDB.value,
                "media_type": media_type,
                "title": get_title(media),
                "image": get_image_url(media.get("poster_path")),
                "backdrop_path": media.get("backdrop_path"),
                "popularity": media.get("popularity"),
                "vote_count": media.get("vote_count"),
                "release_date": media.get("release_date") or media.get("first_air_date"),
            }
            for media in response.get("results", [])
        ]
        data = helpers.format_search_response(page, 20, response.get("total_results", len(results)), results)
        data["per_page"] = 20
        cache.set(cache_key, data, 60 * 60 * 6)
    return data


def find(external_id, external_source):
    """Search for media on TMDB."""
    cache_key = f"find_{Sources.TMDB.value}_{external_id}_{external_source}"
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/find/{external_id}"

        params = {
            **base_params,
            "external_source": external_source,
        }

        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        cache.set(cache_key, response)
        return response

    return data


def get_cast(credits, limit=15):
    """Return the top cast members with their images."""
    if not credits or "cast" not in credits:
        return None

    cast = []
    for actor in sorted(credits["cast"], key=lambda x: x.get("order", 999))[:limit]:
        cast.append({
            "name": actor["name"],
            "character": actor.get("character"),
            "image": get_image_url(actor.get("profile_path")),
            "person_id": actor.get("id"),
        })
    return cast


def get_crew(credits, limit=15):
    """Return curated crew list prioritising director, producer, writer, cinematography."""
    if not credits or "crew" not in credits:
        return None

    priority = {
        "director": 0,
        "executive producer": 1,
        "producer": 1,
        "co-producer": 1,
        "associate producer": 1,
        "writer": 2,
        "screenplay": 2,
        "story": 2,
        "teleplay": 2,
        "cinematography": 3,
        "director of photography": 3,
        "photography": 3,
    }

    crew_map: dict[str, dict[str, object]] = {}

    for member in credits["crew"]:
        name = (member.get("name") or "").strip()
        if not name:
            continue

        job = (member.get("job") or "").strip()
        priority_key = job.lower()
        rank = priority.get(priority_key, 4)
        order = member.get("order", 999)

        entry = crew_map.setdefault(
            name,
            {
                "name": name,
                "roles": [],
                "rank": rank,
                "order": order,
                "person_id": member.get("id"),
                "image": get_image_url(member.get("profile_path")),
            },
        )

        entry["rank"] = min(entry["rank"], rank)
        entry["order"] = min(entry["order"], order)

        if job and job not in entry["roles"]:
            entry["roles"].append(job)

    grouped = sorted(
        crew_map.values(),
        key=lambda item: (item["rank"], item["order"]),
    )

    trimmed = []
    for entry in grouped[:limit]:
        roles = entry.get("roles", [])
        entry["roles"] = roles
        trimmed.append(
            {
                "name": entry["name"],
                "roles": roles,
                "person_id": entry.get("person_id"),
                "job": (roles or [""])[0],
                "image": entry.get("image"),
            }
        )

    return trimmed


def get_director(credits):
    """Return the director's name from crew."""
    if not credits or "crew" not in credits:
        return None

    directors = [
        crew["name"]
        for crew in credits["crew"]
        if (crew.get("job") or "").lower() == "director"
    ]
    return directors[0] if directors else None


def get_director_id(credits):
    """Return the director's person ID from crew."""
    if not credits or "crew" not in credits:
        return None

    directors = [
        crew for crew in credits["crew"]
        if (crew.get("job") or "").lower() == "director"
    ]
    return directors[0].get("id") if directors else None


def _people_details(people):
    """Return ordered, de-duplicated person details for media metadata."""
    result = []
    seen = set()
    for person in people or []:
        if not isinstance(person, dict):
            continue
        name = (person.get("name") or "").strip()
        if not name:
            continue
        person_id = person.get("id")
        key = ("id", str(person_id)) if person_id is not None else ("name", name.casefold())
        if key in seen:
            continue
        seen.add(key)
        detail = {"name": name}
        if person_id is not None:
            detail["id"] = str(person_id)
        result.append(detail)
    return result


def get_directors(credits):
    """Return all directors from movie credits in TMDB's supplied order."""
    if not credits or "crew" not in credits:
        return []
    return _people_details(
        crew
        for crew in credits["crew"]
        if (crew.get("job") or "").strip().lower() == "director"
    )


def get_creators(creators):
    """Return all TV creators in TMDB's supplied order, with "image" when TMDB has one."""
    details = _people_details(creators)
    images = {
        str(person["id"]): get_image_url(person["profile_path"])
        for person in creators or []
        if isinstance(person, dict) and person.get("id") is not None and person.get("profile_path")
    }
    for detail in details:
        if detail.get("id") in images:
            detail["image"] = images[detail["id"]]
    return details


def get_creator_id(created_by):
    """Return the creator's person ID from created_by."""
    if not created_by:
        return None
    first = created_by[0] if isinstance(created_by, list) else None
    return first.get("id") if first else None


def movie(media_id):
    """Return the metadata for the selected movie from The Movie Database."""
    cache_key = (
        f"{Sources.TMDB.value}_{MOVIE_DETAIL_CACHE_VERSION}_"
        f"{MediaTypes.MOVIE.value}_{media_id}"
    )
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/movie/{media_id}"
        params = {
            **base_params,
            "append_to_response": (
                "recommendations,credits,release_dates,external_ids,watch/providers"
            ),
        }

        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
            collection = response.get("belongs_to_collection") or {}
            collection_id = collection.get("id")
            if collection_id:
                try:
                    collection_response = services.api_request(
                        Sources.TMDB.value,
                        "GET",
                        f"{base_url}/collection/{collection_id}",
                        params={**base_params},
                    )
                except requests.exceptions.HTTPError as error:
                    logger.warning("Failed to get collection: %s", error)
                    collection_response = {}
            else:
                collection_response = {}
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        collection_items = get_collection(collection_response)
        collection_name = collection_response.get("name") or collection.get("name")
        collection_ids = [item["media_id"] for item in collection_items]
        recommended_items = response.get("recommendations", {}).get("results", [])
        filtered_recommendations = [
            item for item in recommended_items if item["id"] not in collection_ids
        ]

        data = {
            "media_id": media_id,
            "source": Sources.TMDB.value,
            "source_url": f"https://www.themoviedb.org/movie/{media_id}",
            "media_type": MediaTypes.MOVIE.value,
            "title": response["title"],
            "max_progress": 1,
            "image": get_image_url(response["poster_path"]),
            "backdrop_path": response.get("backdrop_path"),
            "synopsis": get_synopsis(response["overview"]),
            "genres": get_genres(response["genres"]),
            "score": get_score(response["vote_average"]),
            "score_count": response["vote_count"],
            "revenue": response.get("revenue"),
            "popularity": response.get("popularity"),
            "original_language": get_original_language(
                response.get("original_language"),
                response.get("spoken_languages"),
            ),
            "details": {
                "format": "Movie",
                "release_date": get_start_date(response["release_date"]),
                "status": response["status"],
                "runtime": get_readable_duration(response["runtime"]),
                "rating": get_movie_rating(response.get("release_dates")),
                "studios": get_companies(response["production_companies"]),
                "country": get_country(response["production_countries"]),
                "languages": get_languages(response["spoken_languages"]),
                "director": get_director(response.get("credits")),
                "director_id": get_director_id(response.get("credits")),
                "directors": get_directors(response.get("credits")),
                **(
                    {
                        "series_id": str(collection_id),
                        "series_source": Sources.TMDB.value,
                        "series_media_type": MediaTypes.MOVIE.value,
                        "series_name": collection_name,
                    }
                    if collection_id
                    else {}
                ),
            },
            "cast": get_cast(response.get("credits")),
            "crew": get_crew(response.get("credits")),
            "related": {
                collection_name or "collection": collection_items,
                "recommendations": get_related(
                    filtered_recommendations[:15],
                    MediaTypes.MOVIE.value,
                ),
            },
            "external_links": get_external_links(response.get("external_ids", {}), media_id),
            "providers": response.get("watch/providers", {}).get("results", {}),
        }

        cache.set(cache_key, data)

    return data


def get_cached_seasons(media_id, season_numbers):
    """Check cache for seasons and return cached data and list of uncached seasons."""
    cached_data = {}
    uncached_seasons = []

    for season_number in season_numbers:
        season_cache_key = (
            f"{Sources.TMDB.value}_{MediaTypes.SEASON.value}_{media_id}_{season_number}"
        )
        season_data = cache.get(season_cache_key)
        if season_data:
            cached_data[f"season/{season_number}"] = season_data
        else:
            uncached_seasons.append(season_number)

    return cached_data, uncached_seasons


def enrich_season_with_tv_data(season_data, tv_data, media_id, season_number):
    """Add TV show metadata to season metadata."""
    season_data["media_id"] = media_id
    season_data["source_url"] = (
        f"https://www.themoviedb.org/tv/{media_id}/season/{season_number}"
    )
    season_data["title"] = tv_data["title"]
    season_data["tvdb_id"] = tv_data["tvdb_id"]
    season_data["external_links"] = tv_data["external_links"]
    season_data["genres"] = tv_data["genres"]
    if season_data["synopsis"] == "No synopsis available.":
        season_data["synopsis"] = tv_data["synopsis"]
    return season_data


def fetch_and_cache_seasons(media_id, season_numbers, tv_data):
    """Fetch uncached seasons from API and cache them."""
    url = f"{base_url}/tv/{media_id}"
    # Credits make the show's cast part of the same request (8 seasons: 20 appends).
    base_append = "recommendations,external_ids,watch/providers,credits"
    max_seasons_per_request = 18
    fetched_tv_data = tv_data
    result_data = {}

    for i in range(0, len(season_numbers), max_seasons_per_request):
        season_subset = season_numbers[i : i + max_seasons_per_request]
        append_text = ",".join(
            [
                f"season/{season},season/{season}/watch/providers"
                for season in season_subset
            ],
        )

        params = {
            **base_params,
            "append_to_response": f"{base_append},{append_text}",
        }

        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        # Cache TV metadata if we haven't fetched it yet
        if fetched_tv_data is None:
            fetched_tv_data = process_tv(response)
            tv_cache_key = f"{Sources.TMDB.value}_{DETAIL_CACHE_VERSION}_{MediaTypes.TV.value}_{media_id}"
            cache.set(tv_cache_key, fetched_tv_data)

        # Process and cache each season
        for season_number in season_subset:
            season_key = f"season/{season_number}"
            if season_key not in response:
                msg = (
                    f"Season {season_number} not found in {Sources.TMDB.label} "
                    f"with ID {media_id}"
                )
                not_found_response = requests.Response()
                not_found_response.status_code = 404
                not_found_error = type("Error", (), {"response": not_found_response})
                raise services.ProviderAPIError(msg, error=not_found_error, details=msg)

            season_data = process_season(
                response[season_key],
                response.get(f"{season_key}/watch/providers", {}),
            )
            season_data = enrich_season_with_tv_data(
                season_data,
                fetched_tv_data,
                media_id,
                season_number,
            )

            cache.set(
                f"{Sources.TMDB.value}_{MediaTypes.SEASON.value}_{media_id}_{season_number}",
                season_data,
            )
            result_data[season_key] = season_data

    return result_data, fetched_tv_data


def tv_with_seasons(media_id, season_numbers):
    """Return the metadata for the tv show with seasons appended to the response."""
    if not season_numbers:
        return tv(media_id)

    tv_cache_key = f"{Sources.TMDB.value}_{DETAIL_CACHE_VERSION}_{MediaTypes.TV.value}_{media_id}"
    tv_data = cache.get(tv_cache_key)

    cached_seasons, uncached_seasons = get_cached_seasons(media_id, season_numbers)

    if tv_data is None and not uncached_seasons:
        tv_data = tv(media_id)

    if uncached_seasons:
        fetched_seasons, fetched_tv_data = fetch_and_cache_seasons(
            media_id,
            uncached_seasons,
            tv_data,
        )

        if tv_data is None:
            tv_data = fetched_tv_data

        cached_seasons.update(fetched_seasons)

    return tv_data | cached_seasons


def tv(media_id):
    """Return the metadata for the selected tv show from The Movie Database."""
    cache_key = f"{Sources.TMDB.value}_{DETAIL_CACHE_VERSION}_{MediaTypes.TV.value}_{media_id}"
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/tv/{media_id}"
        params = {
            **base_params,
            "append_to_response": (
                "recommendations,external_ids,credits,content_ratings,watch/providers"
            ),
        }

        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        data = process_tv(response)
        cache.set(cache_key, data)

    return data


def process_tv(response):
    """Process the metadata for the selected tv show from The Movie Database."""
    num_episodes = response["number_of_episodes"]
    next_episode = response.get("next_episode_to_air")
    last_episode = response.get("last_episode_to_air")
    return {
        "media_id": response["id"],
        "source": Sources.TMDB.value,
        "source_url": f"https://www.themoviedb.org/tv/{response['id']}",
        "media_type": MediaTypes.TV.value,
        "title": response["name"],
        "max_progress": num_episodes,
        "image": get_image_url(response["poster_path"]),
        "backdrop_path": response.get("backdrop_path"),
        "synopsis": get_synopsis(response["overview"]),
        "genres": get_genres(response["genres"]),
        "score": get_score(response["vote_average"]),
        "score_count": response["vote_count"],
        "revenue": None,
        "popularity": response.get("popularity"),
        "original_language": get_original_language(
            response.get("original_language"),
            response.get("spoken_languages"),
        ),
        "details": {
            "format": "TV",
            "first_air_date": get_start_date(response["first_air_date"]),
            "last_air_date": response["last_air_date"],
            "status": response["status"],
            "seasons": response["number_of_seasons"],
            "episodes": num_episodes,
            "runtime": get_runtime_tv(response["episode_run_time"]),
            "rating": get_tv_rating(response.get("content_ratings")),
            "studios": get_companies(response["production_companies"]),
            "country": get_country(response["production_countries"]),
            "languages": get_languages(response["spoken_languages"]),
            "creator": get_creator(response.get("created_by")),
            "creator_id": get_creator_id(response.get("created_by")),
            "creators": get_creators(response.get("created_by")),
        },
        "cast": get_cast(response.get("credits")),
        "crew": get_crew(response.get("credits")),
        "related": {
            "seasons": get_related(
                response["seasons"],
                MediaTypes.SEASON.value,
                response,
            ),
            "recommendations": get_related(
                response.get("recommendations", {}).get("results", [])[:15],
                MediaTypes.TV.value,
            ),
        },
        "tvdb_id": response.get("external_ids", {}).get("tvdb_id"),
        "external_links": get_external_links(response.get("external_ids", {})),
        "providers": response.get("watch/providers", {}).get("results", {}),
        "last_episode_season": last_episode["season_number"] if last_episode else None,
        "next_episode_season": next_episode["season_number"] if next_episode else None,
        # The episode TMDB names as next to air, which its season list may not
        # include yet (TV-200, TV-202).
        "next_episode_to_air": (
            {
                "season_number": next_episode["season_number"],
                "episode_number": next_episode.get("episode_number"),
                "air_date": get_start_date(next_episode.get("air_date")),
            }
            if next_episode
            else None
        ),
    }


def forget_tv(media_id, season_numbers=()):
    """Drop the cached show and season details so the next read fetches them again."""
    cache.delete_many(
        [
            f"{Sources.TMDB.value}_{DETAIL_CACHE_VERSION}_{MediaTypes.TV.value}_{media_id}",
            *(
                f"{Sources.TMDB.value}_{MediaTypes.SEASON.value}_{media_id}_{number}"
                for number in season_numbers
            ),
        ],
    )


def process_season(response, providers_response=None):
    """Process the metadata for the selected season from The Movie Database."""
    episodes = response["episodes"]
    num_episodes = len(episodes)

    runtimes = []
    total_runtime = 0
    score_count = 0

    for episode in episodes:
        if episode["runtime"] is not None:
            runtimes.append(episode["runtime"])
            total_runtime += episode["runtime"]
        score_count += episode["vote_count"]

    avg_runtime = (
        get_readable_duration(sum(runtimes) / len(runtimes)) if runtimes else None
    )
    total_runtime = get_readable_duration(total_runtime) if total_runtime else None

    return {
        "source": Sources.TMDB.value,
        "media_type": MediaTypes.SEASON.value,
        "season_title": response["name"],
        "max_progress": episodes[-1]["episode_number"] if episodes else 0,
        "image": get_image_url(response["poster_path"]),
        "season_number": response["season_number"],
        "synopsis": get_synopsis(response["overview"]),
        "score": get_score(response["vote_average"]),
        "score_count": score_count,
        "details": {
            "first_air_date": get_start_date(response["air_date"]),
            "last_air_date": get_end_date(response),
            "episodes": num_episodes,
            "runtime": avg_runtime,
            "total_runtime": total_runtime,
        },
        "providers": (providers_response or {}).get("results", {}),
        "episodes": response["episodes"],
    }


def get_format(media_type):
    """Return media_type capitalized."""
    if media_type == MediaTypes.TV.value:
        return "TV"
    return "Movie"


def get_image_url(path):
    """Return the image URL for the media."""
    # when no image, value from response is null
    # e.g movie: 445290
    if path:
        return f"https://image.tmdb.org/t/p/w500{path}"
    return settings.IMG_NONE


TMDB_IMAGE_PREFIX = "https://image.tmdb.org/t/p/"


def sized_image_url(url, size):
    """Return a TMDB image URL at another size (``w342``, ``original``).

    URLs from other hosts are returned unchanged.
    """
    value = str(url or "")
    if not value.startswith(TMDB_IMAGE_PREFIX):
        return url
    _, _, path = value[len(TMDB_IMAGE_PREFIX):].partition("/")
    return f"{TMDB_IMAGE_PREFIX}{size}/{path}" if path else url


def fetch_image(path, size="original"):
    """Fetch TMDB image bytes for a given poster path.

    Args:
        path: TMDB file path beginning with a leading slash.
        size: Image size key (e.g. ``original``, ``w342``) supported by TMDB CDN.
    """

    if not path:
        raise ValueError("Cannot fetch TMDB image without a path")

    url = f"https://image.tmdb.org/t/p/{size}{path}"
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.content


def get_title(response):
    """Return the title for the media."""
    # tv shows have name instead of title
    try:
        return response["title"]
    except KeyError:
        return response["name"]


def get_start_date(date):
    """Return the start date for the media."""
    # when unknown date, value from response is empty string
    # e.g movie: 445290
    if date == "":
        return None
    return date


def get_end_date(response):
    """Return the last air date for the season."""
    if response["episodes"]:
        return response["episodes"][-1]["air_date"]

    return None


def get_synopsis(text):
    """Return the synopsis for the media."""
    # when unknown synopsis, value from response is empty string
    # e.g movie: 445290
    if text == "":
        return "No synopsis available."
    return text


def get_readable_duration(duration):
    """Convert duration in minutes to a readable format."""
    # if unknown movie runtime, value from response is 0
    # e.g movie: 274613
    if duration:
        hours, minutes = divmod(int(duration), 60)
        return f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"
    return None


def get_runtime_tv(runtime):
    """Return the runtime for the tv show."""
    # when unknown runtime, value from response is empty list
    # e.g: tv:66672
    if runtime:
        return get_readable_duration(runtime[0])
    return None


def season_scores_count(response):
    """Return the scores count for the season."""
    return sum(episode["vote_count"] for episode in response["episodes"])


def get_genres(genres):
    """Return the genres for the media."""
    # when unknown genres, value from response is empty list
    # e.g tv: 24795
    if genres:
        return [genre["name"] for genre in genres]
    return None


def get_country(countries):
    """Return the production country for the media."""
    # when unknown production country, value from response is empty list
    # e.g tv: 24795
    if countries:
        return countries[0]["name"]
    return None


# English names for the ISO 639-1 codes TMDB uses most as an original language, for
# titles whose spoken_languages don't name it. "cn" is TMDB's code for Cantonese.
ORIGINAL_LANGUAGE_NAMES = {
    "af": "Afrikaans", "am": "Amharic", "ar": "Arabic", "bg": "Bulgarian",
    "bn": "Bengali", "bo": "Tibetan", "bs": "Bosnian", "ca": "Catalan",
    "cn": "Cantonese", "cs": "Czech", "cy": "Welsh", "da": "Danish", "de": "German",
    "el": "Greek", "en": "English", "es": "Spanish", "et": "Estonian", "eu": "Basque",
    "fa": "Persian", "fi": "Finnish", "fr": "French", "ga": "Irish", "gl": "Galician",
    "gu": "Gujarati", "he": "Hebrew", "hi": "Hindi", "hr": "Croatian",
    "hu": "Hungarian", "hy": "Armenian", "id": "Indonesian", "is": "Icelandic",
    "it": "Italian", "ja": "Japanese", "ka": "Georgian", "kk": "Kazakh", "km": "Khmer",
    "kn": "Kannada", "ko": "Korean", "ku": "Kurdish", "la": "Latin",
    "lb": "Luxembourgish", "lt": "Lithuanian", "lv": "Latvian", "mk": "Macedonian",
    "ml": "Malayalam", "mn": "Mongolian", "mr": "Marathi", "ms": "Malay",
    "my": "Burmese", "nb": "Norwegian Bokmål", "ne": "Nepali", "nl": "Dutch",
    "no": "Norwegian", "pa": "Punjabi", "pl": "Polish", "ps": "Pashto",
    "pt": "Portuguese", "ro": "Romanian", "ru": "Russian", "si": "Sinhala",
    "sk": "Slovak", "sl": "Slovenian", "sq": "Albanian", "sr": "Serbian",
    "sv": "Swedish", "sw": "Swahili", "ta": "Tamil", "te": "Telugu", "th": "Thai",
    "tl": "Tagalog", "tr": "Turkish", "uk": "Ukrainian", "ur": "Urdu",
    "uz": "Uzbek", "vi": "Vietnamese", "wo": "Wolof", "xh": "Xhosa", "yo": "Yoruba",
    "zh": "Mandarin", "zu": "Zulu",
}
# TMDB's code for a title with no spoken language (silent films and the like).
NO_LANGUAGE_CODE = "xx"


def get_original_language(code, spoken_languages):
    """Return the title's original language as an English name, or None.

    The title's own spoken_languages name it first, then ORIGINAL_LANGUAGE_NAMES,
    then the code itself.
    """
    code = (code or "").strip().lower()
    if not code or code == NO_LANGUAGE_CODE:
        return None
    for language in spoken_languages or []:
        if (language.get("iso_639_1") or "").lower() == code and language.get("english_name"):
            return language["english_name"]
    return ORIGINAL_LANGUAGE_NAMES.get(code, code)


def get_languages(languages):
    """Return the languages for the media."""
    # when unknown spoken languages, value from response is empty list
    # e.g tv: 24795
    if languages:
        return [language["english_name"] for language in languages]
    return None


def get_companies(companies):
    """Return the production companies for the media."""
    # when unknown production companies, value from response is empty list
    # e.g tv: 24795
    if companies:
        return [company["name"] for company in companies[:3]]
    return None


def get_score(score):
    """Return the score for the media with one decimal place."""
    # when unknown score, value from response is 0.0

    return round(score, 1)


def get_related(related_medias, media_type, parent_response=None):
    """Return list of related media for the selected media."""
    related = []
    for media in related_medias:
        data = {
            "source": Sources.TMDB.value,
            "media_type": media_type,
            "image": get_image_url(media.get("poster_path")),
        }
        if media_type == MediaTypes.SEASON.value:
            data["media_id"] = parent_response["id"]
            data["title"] = parent_response["name"]
            data["season_number"] = media["season_number"]
            data["season_title"] = media["name"]
            data["first_air_date"] = get_start_date(media["air_date"])
            data["max_progress"] = media["episode_count"]
        else:
            data["media_id"] = media["id"]
            data["title"] = get_title(media)
            data["release_date"] = media.get("release_date")
        related.append(data)
    return related


def collection(series_id):
    """Return one TMDB movie collection."""
    cache_key = (
        f"{Sources.TMDB.value}_collection_{series_id}_{COLLECTION_CACHE_VERSION}"
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        response = services.api_request(
            Sources.TMDB.value,
            "GET",
            f"{base_url}/collection/{series_id}",
            params={**base_params},
        )
    except requests.exceptions.HTTPError as error:
        handle_error(error)

    items = get_collection(response)
    data = {
        "series_id": str(response.get("id") or series_id),
        "source": Sources.TMDB.value,
        "media_type": MediaTypes.MOVIE.value,
        "name": response.get("name") or "",
        "item_count": len(items),
        "items": items,
    }
    cache.set(cache_key, data)
    return data


def get_collection(collection_response):
    """Format media collection list to match related media."""
    if not collection_response:
        return []

    seen = set()
    parts = []
    for item in collection_response.get("parts") or []:
        if not isinstance(item, dict) or item.get("id") is None:
            continue
        try:
            movie_id = int(item["id"])
        except (TypeError, ValueError):
            continue
        title = item.get("title") or item.get("name")
        key = str(movie_id)
        if not title or key in seen:
            continue
        seen.add(key)
        parts.append(item)

    parts.sort(
        key=lambda item: (
            not bool(item.get("release_date")),
            item.get("release_date") or "",
            (item.get("title") or item.get("name") or "").casefold(),
            int(item["id"]),
        ),
    )
    return get_related(parts, MediaTypes.MOVIE.value)


def filter_providers(all_providers, region):
    """Filter watch providers by region."""
    if not region:
        return []

    if not all_providers:
        return []

    region_providers = all_providers.get(region, {})
    flatrate_providers = region_providers.get("flatrate", [])
    free_providers = region_providers.get("free", [])
    providers = {}
    for provider in [*flatrate_providers, *free_providers]:
        providers[provider.get("provider_id")] = provider

    providers = list(providers.values())
    for provider in providers:
        provider["logo"] = get_image_url(provider.get("logo_path"))

    providers.sort(key=lambda e: e.get("display_priority", 999))
    return providers


def process_episodes(season_metadata, episodes_in_db):
    """Process the episodes for the selected season."""
    episodes_metadata = []

    # Convert the queryset to a dictionary for efficient lookups
    tracked_episodes = {}
    for ep in episodes_in_db:
        episode_number = ep.item.episode_number
        if episode_number not in tracked_episodes:
            tracked_episodes[episode_number] = []
        tracked_episodes[episode_number].append(ep)

    for episode in season_metadata["episodes"]:
        episode_number = episode["episode_number"]

        vote_average = episode.get("vote_average")
        vote_average_percent = (
            round(vote_average * 10)
            if isinstance(vote_average, (int, float))
            else None
        )
        vote_average_out_of_5 = (
            round(vote_average / 2, 1)
            if isinstance(vote_average, (int, float))
            else None
        )

        episodes_metadata.append(
            {
                "media_id": season_metadata["media_id"],
                "media_type": MediaTypes.EPISODE.value,
                "source": Sources.TMDB.value,
                "season_number": season_metadata["season_number"],
                "episode_number": episode_number,
                "air_date": episode["air_date"],  # when unknown, response returns null
                "image": (
                    get_image_url(episode["still_path"])
                    if episode.get("still_path")
                    else None
                ),
                "title": episode["name"],
                "overview": episode["overview"],
                "history": tracked_episodes.get(episode_number, []),
                "runtime": get_readable_duration(episode["runtime"]),
                "vote_average": vote_average,
                "vote_average_out_of_5": vote_average_out_of_5,
                "vote_average_percent": vote_average_percent,
                "vote_count": episode.get("vote_count"),
            },
        )
    return episodes_metadata


def find_next_episode(episode_number, episodes_metadata):
    """Find the next episode number."""
    # Find the current episode in the sorted list
    current_episode_index = None
    for index, episode in enumerate(episodes_metadata):
        if episode["episode_number"] == episode_number:
            current_episode_index = index
            break

    # If episode not found or it's the last episode, return None
    if current_episode_index is None or current_episode_index + 1 >= len(
        episodes_metadata,
    ):
        return None

    # Return the next episode number
    return episodes_metadata[current_episode_index + 1]["episode_number"]


def episode(media_id, season_number, episode_number):
    """Return the metadata for the selected episode from The Movie Database."""
    season_number = int(season_number)
    episode_number = int(episode_number)
    cache_key = (
        f"{Sources.TMDB.value}_{DETAIL_CACHE_VERSION}_{MediaTypes.EPISODE.value}_"
        f"{media_id}_{season_number}_{episode_number}"
    )
    data = cache.get(cache_key)
    if data is not None:
        return data

    url = (
        f"{base_url}/tv/{media_id}/season/{season_number}/"
        f"episode/{episode_number}"
    )
    params = {
        **base_params,
        "append_to_response": "external_ids",
    }
    try:
        response = services.api_request(
            Sources.TMDB.value,
            "GET",
            url,
            params=params,
        )
    except requests.exceptions.HTTPError as error:
        if error.response is None or error.response.status_code != 404:
            handle_error(error)
        msg = (
            f"Episode {episode_number} not found in season {season_number} "
            f"for {Sources.TMDB.label} with ID {media_id}"
        )
        raise services.ProviderAPIError(
            Sources.TMDB.value,
            error=error,
            details=msg,
        ) from None

    tv_metadata = tv_with_seasons(media_id, [season_number])
    season_metadata = tv_metadata[f"season/{season_number}"]
    series_title = season_metadata.get("title") or tv_metadata.get("title") or ""
    season_title = season_metadata.get("season_title") or f"Season {season_number}"
    # An episode's TVDB ID is not a series ID, so the shared TMDB link helper's
    # series dereferrer would be misleading here. IMDb and Wikidata identifiers
    # have media-agnostic destination formats and are safe to expose.
    external_links = {
        name: url
        for name, url in get_external_links(response.get("external_ids", {})).items()
        if name in {"IMDb", "Wikidata"}
    }
    imdb_url = external_links.get("IMDb")
    still_path = response.get("still_path")
    vote_average = response.get("vote_average")
    vote_count = response.get("vote_count") or 0

    data = {
        "media_id": str(media_id),
        "source": Sources.TMDB.value,
        "source_url": (
            f"https://www.themoviedb.org/tv/{media_id}/season/"
            f"{season_number}/episode/{episode_number}"
        ),
        "media_type": MediaTypes.EPISODE.value,
        "title": response.get("name") or f"Episode {episode_number}",
        "subtitle": f"{series_title} • S{season_number} E{episode_number}",
        "series_title": series_title,
        "season_title": season_title,
        "episode_title": response.get("name") or f"Episode {episode_number}",
        "season_number": season_number,
        "episode_number": episode_number,
        "max_progress": 1,
        # Keep the still available to Item persistence while the API serializer
        # presents episode artwork as a backdrop rather than a poster.
        "image": get_image_url(still_path),
        "backdrop_path": still_path,
        "synopsis": get_synopsis(response.get("overview", "")),
        "release_date": get_start_date(response.get("air_date", "")),
        "score": get_score(vote_average) if vote_average and vote_count else None,
        "score_count": vote_count,
        "details": {
            "format": "Episode",
            "series_title": series_title,
            "season_title": season_title,
            "season_number": season_number,
            "episode_number": episode_number,
            "air_date": get_start_date(response.get("air_date", "")),
            "runtime": get_readable_duration(response.get("runtime")),
            "production_code": response.get("production_code") or None,
        },
        "cast": get_cast({"cast": response.get("guest_stars", [])}),
        "crew": get_crew({"crew": response.get("crew", [])}),
        "external_links": external_links,
        "imdb_id": response.get("external_ids", {}).get("imdb_id"),
        # Keep a truthful link-only fallback; the media service can enrich this
        # shape through the optional licensed IMDb API without changing the
        # public media-detail contract.
        "external_ratings": {
            "imdb": {
                "value": None,
                "votes": None,
                "url": imdb_url,
            },
        }
        if imdb_url
        else {},
        "parent": {
            "show": {
                "title": series_title,
                "ref": {
                    "item_id": None,
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.TV.value,
                    "media_id": str(media_id),
                    "season_number": None,
                    "episode_number": None,
                },
            },
            "season": {
                "title": season_title,
                "ref": {
                    "item_id": None,
                    "source": Sources.TMDB.value,
                    "media_type": MediaTypes.SEASON.value,
                    "media_id": str(media_id),
                    "season_number": season_number,
                    "episode_number": None,
                },
            },
        },
    }
    cache.set(cache_key, data)
    return data


def get_creator(creators):
    """Return the creator's name from the list."""
    if creators:
        return creators[0]["name"]
    return None


def get_movie_rating(release_dates):
    """Extract US MPAA rating from movie release dates."""
    if not release_dates or "results" not in release_dates:
        return None
    
    for result in release_dates["results"]:
        if result.get("iso_3166_1") == "US":
            release_dates_list = result.get("release_dates", [])
            if release_dates_list:
                # Get the first US release date with a certification
                for release_date in release_dates_list:
                    certification = release_date.get("certification")
                    if certification:
                        return certification
    return None


def get_tv_rating(content_ratings):
    """Extract US TV rating from content ratings."""
    if not content_ratings or "results" not in content_ratings:
        return None
    
    for result in content_ratings["results"]:
        if result.get("iso_3166_1") == "US":
            rating = result.get("rating")
            if rating:
                return rating
    return None


def _person_credit_role(job, department):
    job = (job or "").strip()
    department = (department or "").strip()
    value = job.lower()
    if department.lower() == "writing" or value in {"screenplay", "story", "writer"}:
        return "Writer"
    if "producer" in value:
        return "Producer"
    if value == "director":
        return "Director"
    return job or department or None


def person_page(person_id):
    """Return person details and credits for the person page."""
    cache_key = f"{Sources.TMDB.value}_person_{person_id}_v9"
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/person/{person_id}"
        params = {
            **base_params,
            "append_to_response": "movie_credits,tv_credits",
        }

        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        credits = []

        for item in response.get("movie_credits", {}).get("cast", []) or []:
            title = item.get("title") or ""
            year = (item.get("release_date") or "")[:4] or None
            slug_val = slugify(title) or "movie"
            url_path = reverse(
                "media_details",
                args=[Sources.TMDB.value, MediaTypes.MOVIE.value, item["id"], slug_val],
            )
            credits.append({
                "media_type": MediaTypes.MOVIE.value,
                "source": Sources.TMDB.value,
                "media_id": str(item["id"]),
                "title": title,
                "image": get_image_url(item.get("poster_path")),
                "role": item.get("character"),
                "credit_role": "Actor",
                "release_date": item.get("release_date"),
                "year": year,
                "genres": _genre_names_from_ids(MediaTypes.MOVIE.value, item.get("genre_ids")),
                "languages": [_language_name(item.get("original_language"))] if item.get("original_language") else [],
                "url": url_path,
                "popularity": item.get("popularity"),
                "vote_average": item.get("vote_average"),
                "vote_count": item.get("vote_count"),
            })

        for item in response.get("movie_credits", {}).get("crew", []) or []:
            title = item.get("title") or ""
            year = (item.get("release_date") or "")[:4] or None
            slug_val = slugify(title) or "movie"
            url_path = reverse(
                "media_details",
                args=[Sources.TMDB.value, MediaTypes.MOVIE.value, item["id"], slug_val],
            )
            credits.append({
                "media_type": MediaTypes.MOVIE.value,
                "source": Sources.TMDB.value,
                "media_id": str(item["id"]),
                "title": title,
                "image": get_image_url(item.get("poster_path")),
                "role": item.get("job"),
                "credit_role": _person_credit_role(item.get("job"), item.get("department")),
                "release_date": item.get("release_date"),
                "year": year,
                "genres": _genre_names_from_ids(MediaTypes.MOVIE.value, item.get("genre_ids")),
                "languages": [_language_name(item.get("original_language"))] if item.get("original_language") else [],
                "url": url_path,
                "popularity": item.get("popularity"),
                "vote_average": item.get("vote_average"),
                "vote_count": item.get("vote_count"),
            })

        for item in response.get("tv_credits", {}).get("cast", []) or []:
            title = item.get("name") or ""
            year = (item.get("first_air_date") or "")[:4] or None
            slug_val = slugify(title) or "tv"
            url_path = reverse(
                "media_details",
                args=[Sources.TMDB.value, MediaTypes.TV.value, item["id"], slug_val],
            )
            credits.append({
                "media_type": MediaTypes.TV.value,
                "source": Sources.TMDB.value,
                "media_id": str(item["id"]),
                "title": title,
                "image": get_image_url(item.get("poster_path")),
                "role": item.get("character"),
                "credit_role": "Actor",
                "release_date": item.get("first_air_date"),
                "year": year,
                "genres": _genre_names_from_ids(MediaTypes.TV.value, item.get("genre_ids")),
                "languages": [_language_name(item.get("original_language"))] if item.get("original_language") else [],
                "url": url_path,
                "popularity": item.get("popularity"),
                "vote_average": item.get("vote_average"),
                "vote_count": item.get("vote_count"),
            })

        for item in response.get("tv_credits", {}).get("crew", []) or []:
            title = item.get("name") or ""
            year = (item.get("first_air_date") or "")[:4] or None
            slug_val = slugify(title) or "tv"
            url_path = reverse(
                "media_details",
                args=[Sources.TMDB.value, MediaTypes.TV.value, item["id"], slug_val],
            )
            credits.append({
                "media_type": MediaTypes.TV.value,
                "source": Sources.TMDB.value,
                "media_id": str(item["id"]),
                "title": title,
                "image": get_image_url(item.get("poster_path")),
                "role": item.get("job"),
                "credit_role": _person_credit_role(item.get("job"), item.get("department")),
                "release_date": item.get("first_air_date"),
                "year": year,
                "genres": _genre_names_from_ids(MediaTypes.TV.value, item.get("genre_ids")),
                "languages": [_language_name(item.get("original_language"))] if item.get("original_language") else [],
                "url": url_path,
                "popularity": item.get("popularity"),
                "vote_average": item.get("vote_average"),
                "vote_count": item.get("vote_count"),
            })

        # Consolidate multiple credits for the same title (e.g. actor + producer) into one card
        by_key = {}
        for c in credits:
            key = (c["source"], c["media_type"], c["media_id"])
            if key not in by_key:
                by_key[key] = {
                    "media_type": c["media_type"],
                    "source": c["source"],
                    "media_id": c["media_id"],
                    "title": c["title"],
                    "image": c["image"],
                    "release_date": c.get("release_date"),
                    "year": c["year"],
                    "genres": c.get("genres") or [],
                    "languages": c.get("languages") or [],
                    "url": c["url"],
                    "popularity": c.get("popularity"),
                    "vote_average": c.get("vote_average"),
                    "vote_count": c.get("vote_count"),
                    "roles": [],
                    "credit_roles": [],
                }
            role = c.get("role")
            if role and role not in by_key[key]["roles"]:
                by_key[key]["roles"].append(role)
            credit_role = c.get("credit_role")
            if credit_role and credit_role not in by_key[key]["credit_roles"]:
                by_key[key]["credit_roles"].append(credit_role)
        credits = list(by_key.values())

        # Sort by vote_count (desc), then title; uses only TMDB data from person credits
        def _sort_key(x):
            return (-float(x.get("vote_count") or 0), (x.get("title") or ""))

        credits.sort(key=_sort_key)

        data = {
            "source": Sources.TMDB.value,
            "person_id": str(person_id),
            "name": response.get("name") or "",
            "image": get_image_url(response.get("profile_path")),
            "biography": (response.get("biography") or "").strip() or None,
            "known_for_department": response.get("known_for_department"),
            "birth_date": response.get("birthday"),
            "death_date": response.get("deathday"),
            "place_of_birth": response.get("place_of_birth"),
            "popularity": response.get("popularity"),
            "credits": credits,
        }

        cache.set(cache_key, data)

    return data


def get_poster_images(media_id, media_type, season_number=None):
    """Get all available poster images for a movie, TV show, or season from TMDB.
    
    Args:
        media_id: TMDB ID of the media
        media_type: Either 'movie', 'tv', or 'season'
        season_number: Season number (required if media_type is 'season')
        
    Returns:
        List of poster image URLs and metadata
    """
    if media_type == 'season' and season_number is None:
        raise ValueError("season_number is required when media_type is 'season'")
    
    # Create cache key based on media type
    if media_type == 'season':
        cache_key = f"{Sources.TMDB.value}_season_posters_{media_id}_{season_number}"
        url = f"{base_url}/tv/{media_id}/season/{season_number}/images"
    else:
        cache_key = f"{Sources.TMDB.value}_{media_type}_posters_{media_id}"
        url = f"{base_url}/{media_type}/{media_id}/images"
    
    data = cache.get(cache_key)
    
    if data is None:
        params = {
            **base_params,
            # Remove language filtering to get ALL posters
        }
        # Remove the language parameter entirely to get all languages
        del params["language"]
        
        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        # Extract and format poster data
        posters = []
        for poster in response.get("posters", []):
            file_path = poster["file_path"]
            posters.append({
                "url": f"https://image.tmdb.org/t/p/original{file_path}",
                "thumbnail_url": f"https://image.tmdb.org/t/p/w342{file_path}",
                "width": poster["width"],
                "height": poster["height"],
                "aspect_ratio": poster["aspect_ratio"],
                "vote_average": poster.get("vote_average", 0),
                "vote_count": poster.get("vote_count", 0),
                "language": poster.get("iso_639_1")
            })
            
        # Sort by vote average (highest first), then by vote count
        data = sorted(
            posters,
            key=lambda x: (x["vote_average"], x["vote_count"]),
            reverse=True
        )
        
        # Cache for 24 hours - posters don't change often
        cache.set(cache_key, data, 86400)
        
    return data


def get_backdrop_images(media_id, media_type):
    """Get all available backdrop images for a movie or TV show from TMDB."""
    if media_type not in [MediaTypes.MOVIE.value, MediaTypes.TV.value]:
        raise ValueError("Backdrop images are only available for movies and TV shows")

    cache_key = f"{Sources.TMDB.value}_{media_type}_backdrops_{media_id}"
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/{media_type}/{media_id}/images"
        params = {**base_params}
        del params["language"]

        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        backdrops = []
        for backdrop in response.get("backdrops", []):
            file_path = backdrop["file_path"]
            backdrops.append({
                "url": f"https://image.tmdb.org/t/p/original{file_path}",
                "thumbnail_url": f"https://image.tmdb.org/t/p/w780{file_path}",
                "width": backdrop["width"],
                "height": backdrop["height"],
                "aspect_ratio": backdrop["aspect_ratio"],
                "vote_average": backdrop.get("vote_average", 0),
                "vote_count": backdrop.get("vote_count", 0),
                "language": backdrop.get("iso_639_1"),
            })

        data = sorted(
            backdrops,
            key=lambda x: (x["vote_average"], x["vote_count"]),
            reverse=True,
        )
        cache.set(cache_key, data, 86400)

    return data


def get_season_backdrop_images(media_id, season_number):
    """Return all episode stills for a season as backdrop options."""
    season_number = int(season_number)
    cache_key = f"{Sources.TMDB.value}_season_backdrops_{media_id}_{season_number}"
    data = cache.get(cache_key)

    if data is None:
        season = tv_with_seasons(media_id, [season_number])[f"season/{season_number}"]
        params = {**base_params}
        params.pop("language", None)
        seen = set()
        backdrops = []

        for episode in season.get("episodes", []):
            episode_number = episode.get("episode_number")
            if episode_number is None:
                continue
            url = f"{base_url}/tv/{media_id}/season/{season_number}/episode/{episode_number}/images"

            try:
                response = services.api_request(
                    Sources.TMDB.value,
                    "GET",
                    url,
                    params=params,
                )
            except requests.exceptions.HTTPError as error:
                handle_error(error)

            for still in response.get("stills", []):
                file_path = still.get("file_path")
                if not file_path or file_path in seen:
                    continue
                seen.add(file_path)
                backdrops.append(
                    {
                        "url": f"https://image.tmdb.org/t/p/original{file_path}",
                        "thumbnail_url": f"https://image.tmdb.org/t/p/w780{file_path}",
                        "width": still.get("width", 0),
                        "height": still.get("height", 0),
                        "aspect_ratio": still.get("aspect_ratio", 1.778),
                        "vote_average": still.get("vote_average", 0),
                        "vote_count": still.get("vote_count", 0),
                        "language": still.get("iso_639_1"),
                        "episode_number": episode_number,
                    },
                )

        data = backdrops
        cache.set(cache_key, data, 86400)

    return data


def get_episode_backdrop_images(media_id, season_number, episode_number):
    """Return all TMDB stills for one episode as backdrop options."""
    season_number = int(season_number)
    episode_number = int(episode_number)
    cache_key = (
        f"{Sources.TMDB.value}_episode_backdrops_{media_id}_"
        f"{season_number}_{episode_number}"
    )
    data = cache.get(cache_key)

    if data is None:
        url = (
            f"{base_url}/tv/{media_id}/season/{season_number}/"
            f"episode/{episode_number}/images"
        )
        params = {**base_params}
        params.pop("language", None)

        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        data = sorted(
            [
                {
                    "url": (
                        "https://image.tmdb.org/t/p/original"
                        f"{still['file_path']}"
                    ),
                    "thumbnail_url": (
                        "https://image.tmdb.org/t/p/w780"
                        f"{still['file_path']}"
                    ),
                    "width": still.get("width", 0),
                    "height": still.get("height", 0),
                    "aspect_ratio": still.get("aspect_ratio", 1.778),
                    "vote_average": still.get("vote_average", 0),
                    "vote_count": still.get("vote_count", 0),
                    "language": still.get("iso_639_1"),
                    "episode_number": episode_number,
                }
                for still in response.get("stills", [])
                if still.get("file_path")
            ],
            key=lambda image: (image["vote_average"], image["vote_count"]),
            reverse=True,
        )
        cache.set(cache_key, data, 86400)

    return data


def get_title_logos(media_id, media_type):
    """Return all title logos for a movie or TV show from TMDB."""
    if media_type not in [MediaTypes.MOVIE.value, MediaTypes.TV.value]:
        raise ValueError("Title logos are only available for movies and TV shows")

    cache_key = f"{Sources.TMDB.value}_{media_type}_logos_{media_id}_v1"
    data = cache.get(cache_key)
    if data is None:
        url = f"{base_url}/{media_type}/{media_id}/images"
        params = {**base_params}
        params.pop("language", None)

        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        logos = []
        for logo in response.get("logos", []):
            file_path = logo["file_path"]
            logos.append(
                {
                    "url": f"https://image.tmdb.org/t/p/w500{file_path}",
                    "thumbnail_url": f"https://image.tmdb.org/t/p/w300{file_path}",
                    "width": logo.get("width") or 0,
                    "height": logo.get("height") or 0,
                    "aspect_ratio": logo.get("aspect_ratio"),
                    "vote_average": logo.get("vote_average") or 0,
                    "vote_count": logo.get("vote_count") or 0,
                    "language": logo.get("iso_639_1"),
                    "style": None,
                }
            )

        data = sorted(
            logos,
            key=lambda item: (
                item["vote_average"],
                item["vote_count"],
                item["width"],
            ),
            reverse=True,
        )

        cache.set(cache_key, data, 86400)

    return data


def get_title_logo(media_id, media_type):
    """Return the best localized title logo for a movie or TV show."""
    logos = get_title_logos(media_id, media_type)
    lang = getattr(settings, "TMDB_LANG", "en") or "en"
    candidates = [logo for logo in logos if logo.get("language") == lang]
    if not candidates:
        candidates = [logo for logo in logos if logo.get("language") is None]
    if not candidates:
        return None
    logo = candidates[0]
    return {
        "url": logo["url"],
        "width": logo["width"],
        "height": logo["height"],
        "aspect_ratio": logo["aspect_ratio"],
    }


def watch_provider_regions():
    """Return the available watch provider regions from The Movie Database."""
    cache_key = f"{Sources.TMDB.value}_watch_provider_regions"
    data = cache.get(cache_key)

    if data is None:
        url = f"{base_url}/watch/providers/regions"
        params = {**base_params}

        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        data = [("", "Disabled")]
        regions = response.get("results", [])
        for region in sorted(regions, key=lambda r: r.get("english_name", "")):
            key = region.get("iso_3166_1")
            name = region.get("english_name") or key
            if key:
                data.append((key, name))

        cache.set(cache_key, data)

    return data


def get_changed_ids(media_type):
    """Return changed TMDB ids for the given media type over the last days."""
    url = f"{base_url}/{media_type}/changes"
    end_date = timezone.localdate()
    start_date = end_date - timedelta(days=3)
    changed_ids = set()
    page = 1

    while True:
        params = {
            **base_params,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "page": page,
        }

        try:
            response = services.api_request(
                Sources.TMDB.value,
                "GET",
                url,
                params=params,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        changed_ids.update(str(result["id"]) for result in response.get("results", []))

        total_pages = response.get("total_pages", 1)
        if page >= total_pages:
            break
        page += 1

    return changed_ids


def tv_changes():
    """Return changed TV ids from TMDB for the last days across all pages."""
    return get_changed_ids(MediaTypes.TV.value)


def movie_changes():
    """Return changed movie ids from TMDB for the last days across all pages."""
    return get_changed_ids(MediaTypes.MOVIE.value)
