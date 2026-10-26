import logging
from datetime import UTC, datetime
from enum import IntEnum

import requests
from celery.exceptions import SoftTimeLimitExceeded
from django.conf import settings
from django.core.cache import cache
from django.utils.text import slugify
from django.utils import timezone

from app import helpers
from app.models import MediaTypes, Sources
from app.providers import services
from app.providers.search_rank import rank_results

logger = logging.getLogger(__name__)
base_url = "https://api.igdb.com/v4"
COMPANY_CACHE_TTL = 60 * 60 * 24
COMPANY_CATALOG_CACHE_TTL = 60 * 60 * 24
IGDB_BATCH_SIZE = 500
COLLECTION_CACHE_VERSION = "v1"
SEARCH_FIELDS = "fields name,cover.image_id,total_rating_count,total_rating,first_release_date,game_type;"


class ExternalGameSource(IntEnum):
    """External game source IDs from IGDB API."""

    STEAM = 1
    GOG = 5
    YOUTUBE = 10
    MICROSOFT = 11
    APPLE = 13
    TWITCH = 14
    ANDROID = 15
    AMAZON_ASIN = 20
    AMAZON_LUNA = 22
    AMAZON_ADG = 23
    EPIC_GAME_STORE = 26
    OCULUS = 28
    UTOMIK = 29
    ITCH_IO = 30
    XBOX_MARKETPLACE = 31
    KARTRIDGE = 32
    PLAYSTATION_STORE_US = 36
    FOCUS_ENTERTAINMENT = 37
    XBOX_GAME_PASS_ULTIMATE_CLOUD = 54
    GAMEJOLT = 55


def handle_error(error):
    """Handle IGDB API errors."""
    error_resp = error.response
    status_code = error_resp.status_code

    # Invalid access token, expired or revoked
    if status_code == requests.codes.unauthorized:
        logger.warning(
            "%s: Invalid access token, refreshing",
            Sources.IGDB.label,
        )
        cache.delete(f"{Sources.IGDB.value}_access_token")
        return {"retry": True}

    try:
        error_json = error_resp.json()
    except requests.exceptions.JSONDecodeError as json_error:
        logger.exception("Failed to decode JSON response")
        raise services.ProviderAPIError(Sources.IGDB.value, error) from json_error

    # Invalid keys
    if status_code in (requests.codes.bad_request, requests.codes.forbidden):
        try:
            details = error_json.get("message").capitalize()
            if details:
                raise services.ProviderAPIError(
                    Sources.IGDB.value,
                    error,
                    details,
                )
        # it can be other error format
        except (KeyError, AttributeError):
            logger.exception("Unexpected error format from IGDB API")
            raise services.ProviderAPIError(Sources.IGDB.value, error) from None

    raise services.ProviderAPIError(Sources.IGDB.value, error)


def get_access_token(*, timeout=None):
    """Return the access token for the IGDB API."""
    access_token = cache.get(f"{Sources.IGDB.value}_access_token")
    if access_token is None:
        url = "https://id.twitch.tv/oauth2/token"
        data = {
            "client_id": settings.IGDB_ID,
            "client_secret": settings.IGDB_SECRET,
            "grant_type": "client_credentials",
        }

        try:
            response = services.api_request(
                Sources.IGDB.value,
                "POST",
                url,
                data=data,
                timeout=timeout,
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)
            # handle_error only returns on a 401; a fresh token can't fix refused credentials
            raise services.ProviderAPIError(Sources.IGDB.value, error) from None

        access_token = response["access_token"]
        cache.set(
            f"{Sources.IGDB.value}_access_token",
            access_token,
            response["expires_in"] - 60,
        )  # 1 min buffer to avoid using an expired token
    return access_token


def external_game(external_id, source=ExternalGameSource.STEAM):
    """Find IGDB game by external ID using the external_game endpoint.

    Args:
        external_id (str): The external ID (e.g., Steam App ID)
        source (ExternalGameSource): The external game source (defaults to Steam)

    Returns:
        int or None: IGDB game ID if found, None otherwise
    """
    cache_key = f"external_game_{Sources.IGDB.value}_{source}_{external_id}"
    data = cache.get(cache_key)

    if data is None:
        access_token = get_access_token()
        url = f"{base_url}/external_games"
        query = (
            f'fields game; where uid = "{external_id}" & '
            f"external_game_source = {source};"
        )
        headers = {
            "Client-ID": settings.IGDB_ID,
            "Authorization": f"Bearer {access_token}",
        }

        try:
            response = services.api_request(
                Sources.IGDB.value,
                "POST",
                url,
                data=query,
                headers=headers,
            )
        except requests.exceptions.HTTPError as error:
            error_resp = handle_error(error)
            if error_resp and error_resp.get("retry"):
                # Retry the request with the new access token
                headers["Authorization"] = f"Bearer {get_access_token()}"
                response = services.api_request(
                    Sources.IGDB.value,
                    "POST",
                    url,
                    data=query,
                    headers=headers,
                )

        # Return the IGDB game ID if found, None otherwise
        if response and len(response) > 0:
            data = response[0].get("game")
            logger.debug(
                "Found IGDB match for external ID %s (source: %s): %s",
                external_id,
                source.name,
                data,
            )
        else:
            data = None
            logger.debug(
                "No IGDB match found for external ID %s (source: %s)",
                external_id,
                source.name,
            )

        cache.set(cache_key, data)

    return data


def external_game_uid(media_id, source=ExternalGameSource.STEAM):
    """Return an external platform UID for an IGDB game."""
    missing = object()
    cache_key = f"{Sources.IGDB.value}_external_game_uid_{source}_{media_id}"
    data = cache.get(cache_key, missing)
    if data is missing:
        response = _post_igdb(
            f"{base_url}/external_games",
            f"fields uid; where game = {media_id} & external_game_source = {source}; limit 1;",
            _api_headers(),
        )
        data = response[0].get("uid") if response else None
        cache.set(cache_key, data, 86400)
    return data


def steam_app_id(media_id):
    """Return the Steam app ID linked to an IGDB game, when IGDB has one."""
    uid = str(external_game_uid(media_id, ExternalGameSource.STEAM) or "").strip()
    return str(int(uid)) if uid.isdigit() and int(uid) > 0 else None


def search(query, page, *, preserve_ranking_fields=False, timeout=None):
    """Search for games on IGDB."""
    rank_suffix = "_rank" if preserve_ranking_fields else ""
    cache_key = f"search_{Sources.IGDB.value}_{MediaTypes.GAME.value}_v2_{query}_{page}{rank_suffix}"
    data = cache.get(cache_key)

    if data is None:
        search_query = str(query).replace("\\", "\\\\").replace('"', '\\"')
        access_token = get_access_token(timeout=timeout)
        search_url = f"{base_url}/games"
        count_url = f"{base_url}/games/count"
        headers = {
            "Client-ID": settings.IGDB_ID,
            "Authorization": f"Bearer {access_token}",
        }

        base_conditions = "where game_type = (0,1,2,3,4,5,6,7,8,9,10)"

        if not settings.IGDB_NSFW:
            base_conditions += " & themes != (42)"

        offset = (page - 1) * settings.PER_PAGE

        search_body = (
            f'search "{search_query}";'
            f"{SEARCH_FIELDS}"
            f"limit {settings.PER_PAGE};"
            f"offset {offset};"
            f"{base_conditions};"
        )
        count_body = (
            f'search "{search_query}";'
            f"{base_conditions};"
        )

        try:
            search_results = services.api_request(
                Sources.IGDB.value,
                "POST",
                search_url,
                data=search_body,
                headers=headers,
                timeout=timeout,
            )
            count_response = (
                {"count": len(search_results)}
                if preserve_ranking_fields
                else services.api_request(
                    Sources.IGDB.value,
                    "POST",
                    count_url,
                    data=count_body,
                    headers=headers,
                    timeout=timeout,
                )
            )

        except requests.exceptions.HTTPError as error:
            error_resp = handle_error(error)
            if error_resp and error_resp.get("retry"):
                # Retry the request with the new access token
                headers["Authorization"] = f"Bearer {get_access_token(timeout=timeout)}"
                search_results = services.api_request(
                    Sources.IGDB.value,
                    "POST",
                    search_url,
                    data=search_body,
                    headers=headers,
                    timeout=timeout,
                )
                count_response = (
                    {"count": len(search_results)}
                    if preserve_ranking_fields
                    else services.api_request(
                        Sources.IGDB.value,
                        "POST",
                        count_url,
                        data=count_body,
                        headers=headers,
                        timeout=timeout,
                    )
                )

        total_results = count_response.get("count", 0)

        if not total_results:
            search_results, total_results = _search_names(
                query,
                base_conditions,
                offset,
                headers,
                count=not preserve_ranking_fields,
                timeout=timeout,
            )

        results = [
            {
                "media_id": media["id"],
                "source": Sources.IGDB.value,
                "media_type": MediaTypes.GAME.value,
                "title": media["name"],
                "image": get_image_url(media),
                "total_rating_count": media.get("total_rating_count"),
                "total_rating": media.get("total_rating"),
                "first_release_date": media.get("first_release_date"),
                "game_type": media.get("game_type"),
            }
            for media in search_results
        ]
        results = rank_results(
            query,
            results,
            MediaTypes.GAME.value,
            preserve_ranking_fields=preserve_ranking_fields,
        )

        data = helpers.format_search_response(
            page,
            settings.PER_PAGE,
            total_results,
            results,
        )

        cache.set(cache_key, data)

    return data


def _search_names(query, base_conditions, offset, headers, *, count, timeout):
    """Return games whose names contain every word of the query, most rated first.

    IGDB's search only matches whole words, so it finds nothing for a word that's
    still being typed, like "cyberpun".
    """
    words = [word.replace("\\", "\\\\").replace('"', '\\"') for word in str(query).split()]
    if not words:
        return [], 0
    conditions = base_conditions + "".join(f' & name ~ *"{word}"*' for word in words)
    results = _post_igdb(
        f"{base_url}/games",
        f"{SEARCH_FIELDS}"
        "sort total_rating_count desc;"
        f"limit {settings.PER_PAGE};"
        f"offset {offset};"
        f"{conditions};",
        headers,
        timeout=timeout,
    )
    if not count:
        return results, len(results)
    response = _post_igdb(f"{base_url}/games/count", f"{conditions};", headers, timeout=timeout)
    return results, response.get("count", 0)


def _normalize_name(value):
    return slugify(str(value or "")).casefold()


def _api_headers():
    return {
        "Client-ID": settings.IGDB_ID,
        "Authorization": f"Bearer {get_access_token()}",
    }


def _post_igdb(url, body, headers, *, timeout=None):
    try:
        return services.api_request(Sources.IGDB.value, "POST", url, data=body, headers=headers, timeout=timeout)
    except requests.exceptions.HTTPError as error:
        error_resp = handle_error(error)
        if error_resp and error_resp.get("retry"):
            headers["Authorization"] = f"Bearer {get_access_token(timeout=timeout)}"
            return services.api_request(
                Sources.IGDB.value,
                "POST",
                url,
                data=body,
                headers=headers,
                timeout=timeout,
            )
        raise


def _name_id_map(endpoint):
    cache_key = f"{Sources.IGDB.value}_{endpoint}_name_id_map"
    data = cache.get(cache_key)
    if data is None:
        response = _post_igdb(
            f"{base_url}/{endpoint}",
            "fields name; limit 500; sort name asc;",
            _api_headers(),
        )
        data = {_normalize_name(item.get("name")): item["id"] for item in response if item.get("name")}
        cache.set(cache_key, data, 60 * 60 * 24 * 7)
    return data


def _resolve_id(endpoint, name):
    value = _name_id_map(endpoint).get(_normalize_name(name))
    if not value:
        msg = f"Unknown IGDB {endpoint[:-1]}: {name}"
        raise ValueError(msg)
    return value


def discover(*, page=1, page_size=None, genre=None, year=None, platform=None):
    """Discover games by genre, release year, and/or platform."""
    page_size = page_size or settings.PER_PAGE
    cache_key = f"discover_{Sources.IGDB.value}_{MediaTypes.GAME.value}_{genre}_{year}_{platform}_{page}_{page_size}"
    data = cache.get(cache_key)
    if data is None:
        conditions = ["game_type = (0,1,2,3,4,5,6,7,8,9,10)"]
        if not settings.IGDB_NSFW:
            conditions.append("themes != (42)")
        if genre:
            conditions.append(f"genres = ({_resolve_id('genres', genre)})")
        if platform:
            conditions.append(f"platforms = ({_resolve_id('platforms', platform)})")
        if year:
            start = int(datetime(int(year), 1, 1, tzinfo=UTC).timestamp())
            end = int(datetime(int(year) + 1, 1, 1, tzinfo=UTC).timestamp())
            conditions.append(f"first_release_date >= {start}")
            conditions.append(f"first_release_date < {end}")

        where_clause = " & ".join(conditions)
        offset = (page - 1) * page_size
        headers = _api_headers()
        search_results = _post_igdb(
            f"{base_url}/games",
            "fields name,cover.image_id,total_rating_count,total_rating,first_release_date,game_type;"
            f"where {where_clause};"
            "sort total_rating_count desc;"
            f"limit {page_size};"
            f"offset {offset};",
            headers,
        )
        count_response = _post_igdb(
            f"{base_url}/games/count",
            f"where {where_clause};",
            headers,
        )
        results = [
            {
                "media_id": media["id"],
                "source": Sources.IGDB.value,
                "media_type": MediaTypes.GAME.value,
                "title": media["name"],
                "image": get_image_url(media),
                "total_rating_count": media.get("total_rating_count"),
                "total_rating": media.get("total_rating"),
                "release_date": get_start_date(media),
                "first_release_date": media.get("first_release_date"),
                "game_type": media.get("game_type"),
            }
            for media in search_results
        ]
        data = helpers.format_search_response(page, page_size, count_response.get("count", len(results)), results)
        data["per_page"] = page_size
        cache.set(cache_key, data, 60 * 60 * 6)
    return data


def game(media_id):
    """Return the metadata for the selected game from IGDB."""
    cache_key = f"{Sources.IGDB.value}_{MediaTypes.GAME.value}_{media_id}_v4"
    data = cache.get(cache_key)
    if data is None:
        access_token = get_access_token()
        url = f"{base_url}/multiquery"
        multiquery = (
            'query games "GameData" {'
            "fields name,cover.image_id,artworks.image_id,artworks.width,artworks.height,"
            "url,summary,game_type,first_release_date,total_rating,total_rating_count,"
            "genres.name,themes.name,platforms.name,age_ratings.category,age_ratings.rating,"
            "franchises.name,collection.name,collections.name,"
            "collections.games.name,collections.games.cover.image_id,"
            "collections.games.game_type,collections.games.first_release_date,"
            "collections.games.version_parent,"
            "involved_companies.company.id,involved_companies.company.name,"
            "involved_companies.developer,involved_companies.publisher,"
            "parent_game.name,parent_game.cover.image_id,"
            "remasters.name,remasters.cover.image_id,"
            "remakes.name,remakes.cover.image_id,"
            "expansions.name,expansions.cover.image_id,"
            "standalone_expansions.name,standalone_expansions.cover.image_id,"
            "expanded_games.name,expanded_games.cover.image_id,"
            "similar_games.name,similar_games.cover.image_id,"
            "dlcs.name,dlcs.cover.image_id;"
            f"where id = {media_id};"
            "};"
            'query game_time_to_beats "TTBData" {'
            "fields hastily,normally,completely;"
            f"where game_id = {media_id};"
            "};"
        )
        headers = {
            "Client-ID": settings.IGDB_ID,
            "Authorization": f"Bearer {access_token}",
        }

        try:
            response = services.api_request(
                Sources.IGDB.value,
                "POST",
                url,
                data=multiquery,
                headers=headers,
            )
        except requests.exceptions.HTTPError as error:
            error_resp = handle_error(error)
            if error_resp and error_resp.get("retry"):
                # Retry the request with the new access token
                headers["Authorization"] = f"Bearer {get_access_token()}"
                response = services.api_request(
                    Sources.IGDB.value,
                    "POST",
                    url,
                    data=multiquery,
                    headers=headers,
                )

        results = {item["name"]: item.get("result", []) for item in response}

        # Check if response is empty (no results found)
        game_results = results.get("GameData", [])
        if not game_results:
            services.raise_not_found_error(
                Sources.IGDB.value,
                media_id,
                "game",
            )
        game_response = game_results[0]  # response is a list with a single element

        ttb_results = results.get("TTBData", [])
        time_to_beat = None
        if ttb_results:
            entry = ttb_results[0]  # response is a list with a single element
            ttb_data = {k: v for k, v in entry.items() if k != "id" and v is not None}
            time_to_beat = ttb_data or None

        # Get all related items individually
        remasters = get_related(game_response.get("remasters"))
        remakes = get_related(game_response.get("remakes"))
        expansions = get_related(game_response.get("expansions"))
        dlcs = get_related(game_response.get("dlcs"))
        standalone_expansions = get_related(
            game_response.get("standalone_expansions"),
        )
        expanded_games = get_related(game_response.get("expanded_games"))
        recommendations = get_related(game_response.get("similar_games"))
        collections = game_response.get("collections") or []
        primary_collection = next(
            (collection for collection in collections if collection.get("id") is not None),
            game_response.get("collection"),
        )
        collection_games = get_collection_games(
            [primary_collection] if primary_collection else [],
        )
        collection_id = primary_collection.get("id") if primary_collection else None
        collection_name = get_name(primary_collection)

        data = {
            "media_id": game_response["id"],
            "source": Sources.IGDB.value,
            "source_url": game_response["url"],
            "media_type": MediaTypes.GAME.value,
            "title": game_response["name"],
            "max_progress": None,
            "image": get_image_url(game_response),
            "artworks": game_response.get("artworks") or [],
            "synopsis": game_response.get("summary", "No synopsis available."),
            "genres": get_list(game_response, "genres"),
            "score": get_score(game_response),
            "score_count": game_response.get("total_rating_count"),
            "details": {
                "format": get_game_type(game_response["game_type"]),
                "release_date": get_start_date(game_response),
                "age_rating": get_primary_age_rating(game_response),
                "age_ratings": get_age_ratings(game_response),
                "franchise": get_list(game_response, "franchises", first=True),
                "franchises": get_list(game_response, "franchises"),
                "collection": collection_name,
                **(
                    {
                        "series_id": str(collection_id),
                        "series_source": Sources.IGDB.value,
                        "series_media_type": MediaTypes.GAME.value,
                        "series_name": collection_name,
                    }
                    if collection_id and collection_name
                    else {}
                ),
                "themes": get_list(game_response, "themes"),
                "platforms": get_list(game_response, "platforms"),
                "companies": get_companies(game_response),
                "developer": get_developer(game_response),
                "company_credits": get_company_credits(game_response),
            },
            "related": {
                "parent_game": get_parent(game_response.get("parent_game")),
                "collection": collection_games,
                "remasters": remasters,
                "remakes": remakes,
                "expansions": expansions,
                "dlcs": dlcs,
                "standalone_expansions": standalone_expansions,
                "expanded_games": expanded_games,
                "recommendations": recommendations,
            },
            "time_to_beat": time_to_beat,
        }
        cache.set(cache_key, data)
    return data


def get_image_url(response):
    """Return the image URL for the media."""
    # when no image, cover is not present in the response
    # e.g game: 287348
    try:
        return f"https://images.igdb.com/igdb/image/upload/t_original/{response['cover']['image_id']}.jpg"
    except KeyError:
        return settings.IMG_NONE


def get_game_type(game_type_id):
    """Return the game_type of the game."""
    game_type_mapping = {
        0: "Main game",
        1: "DLC",
        2: "Expansion",
        3: "Bundle",
        4: "Standalone expansion",
        5: "Mod",
        6: "Episode",
        7: "Season",
        8: "Remake",
        9: "Remaster",
        10: "Expanded game",
        11: "Port",
        12: "Fork",
        13: "Pack",
        14: "Update",
    }
    return game_type_mapping.get(game_type_id)


def get_start_date(response):
    """Return the start date of the game."""
    # when no release date, first_release_date is not present in the response
    # e.g game: 210710
    try:
        return timezone.datetime.fromtimestamp(
            response["first_release_date"],
            tz=timezone.get_current_timezone(),
        ).strftime("%Y-%m-%d")
    except KeyError:
        return None


def get_list(response, field, first=False):
    """Return the list of names from a list of dictionaries."""
    # when no data of field, field is not present in the response
    # e.g game: 25222
    try:
        values = [item["name"] for item in response[field]]
        return values[0] if values and first else values
    except KeyError:
        return None


def get_name(value):
    return value.get("name") if isinstance(value, dict) else None


AGE_RATING_CATEGORY = {
    1: "ESRB",
    2: "PEGI",
    3: "CERO",
    4: "USK",
    5: "GRAC",
    6: "ClassInd",
    7: "ACB",
}

AGE_RATING_VALUE = {
    1: "3",
    2: "7",
    3: "12",
    4: "16",
    5: "18",
    6: "RP",
    7: "EC",
    8: "E",
    9: "E10+",
    10: "T",
    11: "M",
    12: "AO",
    13: "A",
    14: "B",
    15: "C",
    16: "D",
    17: "Z",
    18: "0",
    19: "6",
    20: "12",
    21: "16",
    22: "18",
    23: "All",
    24: "12",
    25: "15",
    26: "18",
    27: "Testing",
    28: "L",
    29: "10",
    30: "12",
    31: "14",
    32: "16",
    33: "18",
    34: "G",
    35: "PG",
    36: "M",
    37: "MA15+",
    38: "R18+",
    39: "RC",
}


def get_age_ratings(response):
    ratings = []
    for rating in response.get("age_ratings", []):
        category = AGE_RATING_CATEGORY.get(rating.get("category"))
        value = AGE_RATING_VALUE.get(rating.get("rating"), str(rating.get("rating") or ""))
        if category and value:
            ratings.append(f"{category} {value}")
    return ratings or None


def get_primary_age_rating(response):
    ratings = get_age_ratings(response) or []
    return next((rating for rating in ratings if rating.startswith("ESRB ")), ratings[0] if ratings else None)


def get_companies(response):
    """Return the companies involved in the game."""
    # when no companies, involved_companies is not present in the response
    # e.g game: 238417
    try:
        return ", ".join(
            company["company"]["name"] for company in response["involved_companies"]
        )
    except KeyError:
        return None


def get_developer(response):
    """Return the primary developer(s) of the game."""
    # when no companies, involved_companies is not present in the response
    try:
        developers = [
            company["company"]["name"]
            for company in response.get("involved_companies", [])
            if company.get("developer", False)
        ]
        if developers:
            # Return first developer, or join multiple with comma
            return developers[0] if len(developers) == 1 else ", ".join(developers)
        return None
    except (KeyError, TypeError):
        return None


def get_company_credits(response):
    """Return ordered, de-duplicated IGDB company credits with their roles."""
    credits = {}
    for involvement in response.get("involved_companies", []) or []:
        company = involvement.get("company") or {}
        company_id = company.get("id")
        name = company.get("name")
        if company_id is None or not name:
            continue

        key = str(company_id)
        credit = credits.setdefault(
            key,
            {
                "id": key,
                "source": Sources.IGDB.value,
                "name": name,
                "roles": [],
            },
        )
        if involvement.get("developer") and "Developer" not in credit["roles"]:
            credit["roles"].append("Developer")
        if involvement.get("publisher") and "Publisher" not in credit["roles"]:
            credit["roles"].append("Publisher")
    return list(credits.values())


def company(company_id):
    """Return an IGDB company profile, including its developed/published game IDs."""
    company_id = int(company_id)
    cache_key = f"{Sources.IGDB.value}_company_{company_id}_v1"
    data = cache.get(cache_key)
    if data is None:
        response = _post_igdb(
            f"{base_url}/companies",
            "fields id,name,description,logo.image_id,logo.url,logo.width,logo.height,"
            "country,start_date,status.name,company_size.name,parent.id,parent.name,"
            "url,websites.url,developed,published;"
            f"where id = {company_id}; limit 1;",
            _api_headers(),
        )
        if not response:
            services.raise_not_found_error(Sources.IGDB.value, company_id, "company")
        data = response[0]
        cache.set(cache_key, data, COMPANY_CACHE_TTL)
    return data


def company_catalog(company_id, role):
    """Return normalized game records for one IGDB company role."""
    if role not in {"developed", "published"}:
        raise ValueError("role must be developed or published.")

    company_data = company(company_id)
    cache_key = f"{Sources.IGDB.value}_company_catalog_{company_data['id']}_{role}_v2"
    data = cache.get(cache_key)
    if data is None:
        game_ids = list(dict.fromkeys(company_data.get(role) or []))
        games = _games_for_ids(game_ids)
        data = [_company_game_summary(game, role) for game in games]
        cache.set(cache_key, data, COMPANY_CATALOG_CACHE_TTL)
    return data


def company_catalog_count(company_data, role):
    """Return the provider-advertised count for a company catalogue role."""
    return len(set(company_data.get(role) or []))


def _games_for_ids(game_ids):
    if not game_ids:
        return []

    games_by_id = {}
    for index in range(0, len(game_ids), IGDB_BATCH_SIZE):
        batch = game_ids[index : index + IGDB_BATCH_SIZE]
        ids = ",".join(str(int(game_id)) for game_id in batch)
        response = _post_igdb(
            f"{base_url}/games",
            "fields id,name,cover.image_id,cover.width,cover.height,first_release_date,"
            "total_rating,total_rating_count,genres.name,platforms.name,game_type;"
            f"where id = ({ids}); limit {IGDB_BATCH_SIZE};",
            _api_headers(),
        )
        games_by_id.update({game["id"]: game for game in response or [] if game.get("id") is not None})
    return [games_by_id[game_id] for game_id in game_ids if game_id in games_by_id]


def _company_game_summary(game, role):
    return {
        "media_id": game["id"],
        "source": Sources.IGDB.value,
        "media_type": MediaTypes.GAME.value,
        "title": game.get("name") or "",
        "image": get_image_url(game),
        "release_date": get_start_date(game),
        "genres": get_list(game, "genres") or [],
        "platforms": get_list(game, "platforms") or [],
        "vote_average": get_score(game),
        "vote_count": game.get("total_rating_count"),
        "roles": ["Developer" if role == "developed" else "Publisher"],
        "credit_roles": ["Developer" if role == "developed" else "Publisher"],
        "game_type": get_game_type(game.get("game_type")),
    }


def get_game_covers(media_id):
    """Get all available cover images for a game from IGDB.
    
    Args:
        media_id: IGDB game ID
        
    Returns:
        List of cover image URLs and metadata
    """
    cache_key = f"{Sources.IGDB.value}_game_covers_{media_id}"
    data = cache.get(cache_key)
    
    if data is None:
        access_token = get_access_token()
        headers = {
            "Client-ID": settings.IGDB_ID,
            "Authorization": f"Bearer {access_token}",
        }
        
        images = []
        
        # Get covers
        try:
            url = f"{base_url}/covers"
            covers_data = (
                f"fields image_id,game;"
                f"where game = {media_id};"
            )
            response = services.api_request(
                Sources.IGDB.value,
                "POST",
                url,
                data=covers_data,
                headers=headers,
            )
            logger.info("IGDB covers response for game %s: %s covers found", media_id, len(response) if response else 0)
            
            if response:
                for cover in response:
                    image_id = cover.get("image_id")
                    if image_id:
                        images.append({
                            "url": f"https://images.igdb.com/igdb/image/upload/t_original/{image_id}.jpg",
                            "thumbnail_url": f"https://images.igdb.com/igdb/image/upload/t_cover_big/{image_id}.jpg",
                            "width": 0,
                            "height": 0,
                            "aspect_ratio": 0.667,
                            "vote_average": 0,
                            "vote_count": 0,
                            "language": None,
                            "image_id": image_id,  # Store image_id for comparison
                        })
            else:
                logger.warning("No covers returned from IGDB for game %s", media_id)
        except SoftTimeLimitExceeded:
            raise
        except Exception as e:
            logger.error("Error fetching covers for game %s: %s", media_id, e, exc_info=True)
        
        # Cache for 24 hours
        cache.set(cache_key, images, 86400)
        data = images
    
    return data


def get_game_backdrops(media_id):
    """Get available IGDB artwork images for game backdrops."""
    cache_key = f"{Sources.IGDB.value}_game_backdrops_{media_id}"
    data = cache.get(cache_key)
    if data is None:
        response = _post_igdb(
            f"{base_url}/artworks",
            f"fields image_id,game,width,height; where game = {media_id};",
            _api_headers(),
        )
        data = [
            igdb_image_option(artwork, thumbnail_size="t_screenshot_big", fallback_aspect_ratio=1.778)
            for artwork in response or []
            if artwork.get("image_id")
        ]
        cache.set(cache_key, data, 86400)
    return data


def igdb_image_option(image, thumbnail_size="t_cover_big", fallback_aspect_ratio=0.667):
    image_id = image["image_id"]
    width = image.get("width") or 0
    height = image.get("height") or 0
    return {
        "url": f"https://images.igdb.com/igdb/image/upload/t_original/{image_id}.jpg",
        "thumbnail_url": f"https://images.igdb.com/igdb/image/upload/{thumbnail_size}/{image_id}.jpg",
        "width": width,
        "height": height,
        "aspect_ratio": round(width / height, 3) if width and height else fallback_aspect_ratio,
        "vote_average": 0,
        "vote_count": 0,
        "language": None,
        "image_id": image_id,
    }


def get_score(response):
    """Return the score of the game."""
    # when no score, total_rating is not present in the response
    try:
        score = response["total_rating"]  # returns e.g 92.70730625238252
        return round(score, 1)
    except KeyError:
        return None


def get_parent(parent_game):
    """Return the parent game to the selected game."""
    if parent_game:
        return [
            {
                "source": Sources.IGDB.value,
                "media_id": parent_game["id"],
                "media_type": MediaTypes.GAME.value,
                "title": parent_game["name"],
                "image": get_image_url(parent_game),
            },
        ]
    return []


def get_related(related_medias):
    """Return the related games to the selected game."""
    if related_medias:
        return [
            {
                "source": Sources.IGDB.value,
                "media_id": game["id"],
                "media_type": MediaTypes.GAME.value,
                "title": game["name"],
                "image": get_image_url(game),
            }
            for game in related_medias
        ]
    return []


def get_collection_games(collections):
    games = {}
    for collection in collections or []:
        for game in collection.get("games") or []:
            if game.get("game_type") != 0 or game.get("version_parent") is not None:
                continue
            games[game["id"]] = {
                "source": Sources.IGDB.value,
                "media_id": game["id"],
                "media_type": MediaTypes.GAME.value,
                "title": game["name"],
                "image": get_image_url(game),
                "release_date": get_start_date(game),
            }
    return sorted(
        games.values(),
        key=lambda game: (
            not bool(game.get("release_date")),
            game.get("release_date") or "",
            game["title"].casefold(),
            int(game["media_id"]),
        ),
    )


def collection(series_id):
    """Return one IGDB game collection without alternate editions."""
    cache_key = (
        f"{Sources.IGDB.value}_collection_{series_id}_{COLLECTION_CACHE_VERSION}"
    )
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        collection_id = int(series_id)
    except (TypeError, ValueError):
        services.raise_not_found_error(
            Sources.IGDB.value,
            series_id,
            "collection",
        )

    response = _post_igdb(
        f"{base_url}/collections",
        (
            "fields name,games.name,games.cover.image_id,games.game_type,"
            "games.first_release_date,games.version_parent;"
            f"where id = {collection_id}; limit 1;"
        ),
        _api_headers(),
    )
    if not response:
        services.raise_not_found_error(
            Sources.IGDB.value,
            series_id,
            "collection",
        )

    raw_collection = response[0]
    items = get_collection_games([raw_collection])
    data = {
        "series_id": str(raw_collection.get("id") or collection_id),
        "source": Sources.IGDB.value,
        "media_type": MediaTypes.GAME.value,
        "name": raw_collection.get("name") or "",
        "item_count": len(items),
        "items": items,
    }
    cache.set(cache_key, data)
    return data
