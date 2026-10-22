import logging
from decimal import Decimal, InvalidOperation

import requests
from django.conf import settings
from django.core.cache import cache

from app import helpers
from app.models import Item, MediaTypes, Sources
from app.providers import services
from app.providers.search_rank import rank_results

logger = logging.getLogger(__name__)

base_url = "https://api.hardcover.app/v1/graphql"
MAX_SEARCH_QUERY_LENGTH = 50


def cap_search_query(query):
    """Limit long book queries before sending them to Hardcover search."""
    query = str(query or "")

    if len(query) <= MAX_SEARCH_QUERY_LENGTH:
        return query

    capped_query = query[:MAX_SEARCH_QUERY_LENGTH]
    if query[MAX_SEARCH_QUERY_LENGTH].isspace():
        return capped_query.rstrip()

    word_boundary = capped_query.rfind(" ")
    if word_boundary == -1:
        return capped_query

    return capped_query[:word_boundary].rstrip()


def handle_error(error):
    """Handle Hardcover API errors."""
    error_resp = error.response
    status_code = error_resp.status_code

    try:
        error_json = error_resp.json()
    except requests.exceptions.JSONDecodeError as json_error:
        logger.exception("Failed to decode JSON response")
        raise services.ProviderAPIError(Sources.HARDCOVER.value, error) from json_error

    if status_code == requests.codes.unauthorized:
        details = error_json["error"]
        raise services.ProviderAPIError(Sources.HARDCOVER.value, error, details)

    raise services.ProviderAPIError(Sources.HARDCOVER.value, error)


def search(query, page, *, preserve_ranking_fields=False, timeout=None):
    """Search for books on Hardcover."""
    query = cap_search_query(query)
    match_suffix = "_match" if preserve_ranking_fields else ""
    cache_key = (
        f"search_{Sources.HARDCOVER.value}_{MediaTypes.BOOK.value}_{query}_{page}{match_suffix}"
    )
    data = cache.get(cache_key)

    if data is None:
        search_query = """
        query SearchBooks($query: String!, $per_page: Int!, $page: Int!) {
          search(
            query: $query,
            query_type: "Book",
            per_page: $per_page,
            page: $page,
          ) {
            results
          }
        }
        """

        variables = {
            "query": query,
            "per_page": settings.PER_PAGE,
            "page": page,
        }

        try:
            response = services.api_request(
                Sources.HARDCOVER.value,
                "POST",
                base_url,
                params={"query": search_query, "variables": variables},
                headers={"Authorization": settings.HARDCOVER_API},
                timeout=timeout,
            )
        except requests.exceptions.HTTPError as error:
            response = handle_error(error)

        hits = response["data"]["search"]["results"]["hits"]
        results = [
            {
                "media_id": hit["document"]["id"],
                "source": Sources.HARDCOVER.value,
                "media_type": MediaTypes.BOOK.value,
                "title": hit["document"]["title"],
                "image": get_image_url(hit["document"]),
                "ratings_count": hit["document"].get("ratings_count"),
                "rating": hit["document"].get("rating"),
                "edition_count": hit["document"].get("edition_count"),
                "first_publish_year": hit["document"].get("first_publish_year"),
                "author_name": hit["document"].get("author_name"),
            }
            for hit in hits
        ]
        total_results = response["data"]["search"]["results"]["found"]
        results = rank_results(
            query,
            results,
            MediaTypes.BOOK.value,
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


def search_people(query, *, limit=10, timeout=None):
    """Search Hardcover authors and return normalized person references."""
    search_query = """
    query SearchAuthors($query: String!, $per_page: Int!, $page: Int!) {
      search(
        query: $query,
        query_type: "Author",
        per_page: $per_page,
        page: $page
      ) {
        results
      }
    }
    """
    try:
        response = services.api_request(
            Sources.HARDCOVER.value,
            "POST",
            base_url,
            params={
                "query": search_query,
                "variables": {
                    "query": cap_search_query(query),
                    "per_page": limit,
                    "page": 1,
                },
            },
            headers={"Authorization": settings.HARDCOVER_API},
            timeout=timeout,
            retry_rate_limits=False,
            request_session=services.person_search_session,
        )
    except requests.exceptions.HTTPError as error:
        handle_error(error)

    results = response.get("data", {}).get("search", {}).get("results") or {}
    return [
        {
            "source": Sources.HARDCOVER.value,
            "person_id": str(author["id"]),
            "name": author.get("name") or "",
            "profile_url": (author.get("image") or {}).get("url") or None,
            "known_for_department": "Author",
        }
        for hit in results.get("hits") or []
        if (author := hit.get("document") or {})
        if author.get("id") is not None and author.get("name")
    ]


def lookup_book_by_isbn(isbn):
    """Return the Hardcover book related to an exact edition ISBN."""
    normalized = str(isbn or "").strip().upper()
    if not normalized:
        return None

    cache_key = f"isbn_{Sources.HARDCOVER.value}_{normalized}"
    cached = cache.get(cache_key)
    if cached is False:
        return None
    if cached is not None:
        return cached

    query = """
    query BookByISBN($isbn: String!) {
      editions(
        where: {
          _or: [
            {isbn_13: {_eq: $isbn}},
            {isbn_10: {_eq: $isbn}}
          ]
        },
        order_by: {users_count: desc},
        limit: 1
      ) {
        isbn_10
        isbn_13
        pages
        book {
          id
          title
          pages
          cached_image(path: "url")
          cached_contributors(path: "[0]['author']['name']")
        }
      }
    }
    """

    try:
        response = services.api_request(
            Sources.HARDCOVER.value,
            "POST",
            base_url,
            params={"query": query, "variables": {"isbn": normalized}},
            headers={"Authorization": settings.HARDCOVER_API},
        )
    except requests.exceptions.HTTPError as error:
        handle_error(error)
    except requests.RequestException as error:
        raise services.ProviderAPIError(Sources.HARDCOVER.value, error) from error

    if response.get("errors"):
        logger.warning("Hardcover ISBN lookup failed for %s: %s", normalized, response["errors"])
        return None

    editions = response.get("data", {}).get("editions") or []
    if not editions:
        cache.set(cache_key, False, timeout=24 * 60 * 60)
        return None

    edition = editions[0]
    book_data = edition.get("book") or {}
    if not book_data.get("id"):
        cache.set(cache_key, False, timeout=24 * 60 * 60)
        return None

    total_pages = edition.get("pages") or book_data.get("pages")
    result = {
        "media_id": str(book_data["id"]),
        "source": Sources.HARDCOVER.value,
        "media_type": MediaTypes.BOOK.value,
        "title": book_data.get("title") or normalized,
        "image": book_data.get("cached_image") or settings.IMG_NONE,
        "max_progress": total_pages,
        "total_pages": total_pages,
        "author_name": book_data.get("cached_contributors"),
        "matched_isbn": normalized,
    }
    cache.set(cache_key, result)
    return result


def discover(*, page=1, page_size=None, genre=None, year=None):
    """Browse Hardcover books by genre/tag and/or release year."""
    per_page = page_size or settings.PER_PAGE
    offset = (page - 1) * per_page
    where = _discover_where(genre=genre, year=year)
    cache_key = f"discover_{Sources.HARDCOVER.value}_{MediaTypes.BOOK.value}_{genre}_{year}_{page}_{per_page}"
    data = cache.get(cache_key)

    if data is None:
        discover_query = """
        query DiscoverBooks($where: books_bool_exp!, $limit: Int!, $offset: Int!) {
          books(
            where: $where,
            limit: $limit,
            offset: $offset,
            order_by: {ratings_count: desc}
          ) {
            id
            title
            cached_image(path: "url")
            ratings_count
            rating
            editions_count
            release_year
            author_name: cached_contributors(path: "[0]['author']['name']")
          }
          books_aggregate(where: $where) {
            aggregate {
              count
            }
          }
        }
        """

        try:
            response = services.api_request(
                Sources.HARDCOVER.value,
                "POST",
                base_url,
                params={
                    "query": discover_query,
                    "variables": {"where": where, "limit": per_page, "offset": offset},
                },
                headers={"Authorization": settings.HARDCOVER_API},
            )
        except requests.exceptions.HTTPError as error:
            response = handle_error(error)

        rows = response["data"]["books"]
        results = [
            {
                "media_id": row["id"],
                "source": Sources.HARDCOVER.value,
                "media_type": MediaTypes.BOOK.value,
                "title": row["title"],
                "image": row.get("cached_image") or settings.IMG_NONE,
                "ratings_count": row.get("ratings_count"),
                "rating": row.get("rating"),
                "edition_count": row.get("editions_count"),
                "first_publish_year": row.get("release_year"),
                "author_name": row.get("author_name"),
                "release_date": str(row["release_year"]) if row.get("release_year") else None,
            }
            for row in rows
        ]
        total_results = response["data"]["books_aggregate"]["aggregate"]["count"]
        data = helpers.format_search_response(page, per_page, total_results, results)
        data["per_page"] = per_page
        cache.set(cache_key, data)

    return data


def _discover_where(*, genre=None, year=None):
    clauses = []
    if genre:
        clauses.append(
            {"cached_tags": {"_contains": {"Genre": [{"tag": genre}]}}},
        )
    if year:
        clauses.append({"release_year": {"_eq": int(year)}})
    return {"_and": clauses} if clauses else {}


def book(media_id):
    """Get metadata for a book from Hardcover."""
    cache_key = f"{Sources.HARDCOVER.value}_{MediaTypes.BOOK.value}_{media_id}_v7"
    data = cache.get(cache_key)

    if data is None:
        book_query = """
        query GetBookDetails($book_id: Int!) {
          books_by_pk(id: $book_id) {
            id
            title
            cached_image(path: "url")
            description
            cached_tags(path: "Genre")
            rating
            ratings_count
            pages
            release_date
            slug
            canonical_id
            cached_featured_series
            cached_contributors(path: "[0]['author']['name']")
            contributions(where: {contributable_type: {_eq: "Book"}}) {
              contribution
              author {
                id
                name
                cached_image(path: "url")
              }
            }
            default_cover_edition {
              edition_format
              isbn_13
              isbn_10
              release_date
              publisher {
                name
              }
            }
          }
        }
        """

        variables = {
            "book_id": int(media_id),
        }

        try:
            response = services.api_request(
                Sources.HARDCOVER.value,
                "POST",
                base_url,
                params={"query": book_query, "variables": variables},
                headers={"Authorization": settings.HARDCOVER_API},
            )
        except requests.exceptions.HTTPError as error:
            logger.error("HTTP error fetching book %s: %s", media_id, error)
            handle_error(error)

        if "errors" in response:
            logger.error("GraphQL errors for book %s: %s", media_id, response["errors"])
            logger.error("Full response: %s", response)
            return None
        
        if "data" not in response:
            logger.error("No 'data' key in response for book %s. Full response: %s", media_id, response)
            return None

        book_data = response["data"].get("books_by_pk")
        if not book_data:
            services.raise_not_found_error(
                Sources.HARDCOVER.value,
                media_id,
                "book",
            )
            
        logger.info("Hardcover book data for ID %s: title=%s, has_default_cover_edition=%s, language=%s", 
                   media_id, 
                   book_data.get("title"),
                   bool(book_data.get("default_cover_edition")),
                   book_data.get("language"))
        
        edition_details = get_edition_details(book_data.get("default_cover_edition"))
        publishers = get_publishers(book_data)
        isbns = get_isbns_from_book(book_data)

        authors_for_details = get_authors(book_data)

        # Prefer release_date from default_cover_edition if available, otherwise use book's release_date
        default_edition = book_data.get("default_cover_edition")
        edition_release_date = default_edition.get("release_date") if default_edition else None
        book_release_date = book_data.get("release_date")
        
        # Use edition date if available, otherwise fall back to book date
        raw_release_date = edition_release_date or book_release_date
        release_date = format_release_date(raw_release_date)
        
        logger.info("Raw release_date from Hardcover for book %s: book=%s, edition=%s, using=%s", 
                   media_id, book_release_date, edition_release_date, raw_release_date)
        logger.info("Extracted details for book %s: publishers=%s, isbns=%s, release_date=%s", 
                   media_id, publishers, isbns, release_date)
        
        recommendations = None
        featured_series = get_featured_series(book_data.get("cached_featured_series"))
        series_items = get_series_books(featured_series.get("id")) if featured_series else []

        # Hardcover's API has no recommendations field (the books_by_pk and top-level
        # recommendations queries fail schema validation), so suggest more books by
        # the same author instead.
        recommendations = []
        author_name = book_data.get("cached_contributors")
        if author_name:
            try:
                author_query = """
                query BooksByAuthor($author: String!, $exclude_id: Int!) {
                  books(
                    where: {
                      id: {_neq: $exclude_id},
                      contributions: {author: {name: {_eq: $author}}}
                    },
                    limit: 10,
                    order_by: {users_count: desc}
                  ) {
                    id
                    title
                    cached_image(path: "url")
                  }
                }
                """
                author_resp = services.api_request(
                    Sources.HARDCOVER.value,
                    "POST",
                    base_url,
                    params={"query": author_query, "variables": {"author": author_name, "exclude_id": int(media_id)}},
                    headers={"Authorization": settings.HARDCOVER_API},
                )
                author_books = author_resp["data"].get("books", [])
                # Convert to recommendations format
                recommendations = [{"item_book": book} for book in author_books]
            except Exception as e:
                logger.warning(f"Author fallback failed: {e}")

        related = {
            "recommendations": get_recommendations(recommendations),
        }
        if featured_series and series_items:
            related[featured_series["name"]] = series_items

        data = {
            "media_id": book_data["id"],
            "source": Sources.HARDCOVER.value,
            "source_url": f"https://hardcover.app/books/{book_data['slug']}",
            "media_type": MediaTypes.BOOK.value,
            "title": book_data["title"],
            "max_progress": book_data.get("pages"),
            "image": book_data.get("cached_image") or settings.IMG_NONE,
            "synopsis": book_data.get("description") or "No synopsis available.",
            "genres": get_tags(book_data.get("cached_tags")),
            "score": get_ratings(book_data.get("rating")),
            "score_count": book_data.get("ratings_count", 0),
            "details": {
                "format": edition_details.get("format"),
                "number_of_pages": book_data.get("pages"),
                "publish_date": release_date,
                "release_date": release_date,  # Formatted release date for details display
                "author": book_data.get("cached_contributors"),
                "authors": authors_for_details,
                "publishers": publishers,
                "isbn": isbns,
                "series_id": featured_series.get("id") if featured_series else None,
                "series_name": featured_series.get("name") if featured_series else None,
                "series_position": featured_series.get("position") if featured_series else None,
            },
            "related": related,
        }

        cache.set(cache_key, data)

    return data


def saved_book(media_id):
    """Return a basic page for a saved book while Hardcover is rate limited."""
    item = Item.objects.filter(
        source=Sources.HARDCOVER.value,
        media_type=MediaTypes.BOOK.value,
        media_id=str(media_id),
    ).first()
    if not item:
        return None
    release_date = item.release_date.isoformat() if item.release_date else None
    return {
        "media_id": int(media_id),
        "source": Sources.HARDCOVER.value,
        "source_url": None,
        "media_type": MediaTypes.BOOK.value,
        "title": item.title,
        "max_progress": item.total_pages,
        "image": item.image or settings.IMG_NONE,
        "synopsis": "No synopsis available.",
        "genres": [],
        "score": None,
        "score_count": 0,
        "details": {
            "number_of_pages": item.total_pages,
            "release_date": release_date,
        },
        "related": {},
    }


def get_featured_series(series_data):
    """Return the primary Hardcover series id/name from cached_featured_series."""
    if isinstance(series_data, list):
        series_data = series_data[0] if series_data else None
    if not isinstance(series_data, dict) or not series_data.get("id"):
        return None

    position = series_data.get("position")
    series = series_data.get("series") or series_data
    if not isinstance(series, dict) or not series.get("id"):
        return None

    name = series.get("name") or series.get("title")
    if not name:
        return None

    result = {
        "id": series["id"],
        "name": name,
        "position": position,
    }
    if series.get("primary_books_count") is not None:
        result["primary_books_count"] = series["primary_books_count"]
    return result


def get_authors(book_data):
    """Return Hardcover author refs for a book detail payload."""
    authors = []
    seen = set()
    for contribution in book_data.get("contributions") or []:
        role = contribution.get("contribution")
        if role and role != "Author":
            continue
        author = contribution.get("author") or {}
        author_id = author.get("id")
        name = author.get("name")
        if not name:
            continue
        key = author_id or name
        if key in seen:
            continue
        seen.add(key)
        item = {"name": name}
        if author_id:
            item["person_id"] = str(author_id)
            item["source"] = Sources.HARDCOVER.value
        if author.get("cached_image"):
            item["image_url"] = author["cached_image"]
        authors.append(item)

    if authors:
        return authors

    author_name = book_data.get("cached_contributors")
    if isinstance(author_name, str) and author_name.strip():
        return [{"name": author_name.strip()}]
    return None


AUTHOR_BOOK_LIMIT = 500


def person_page(person_id):
    """Return Hardcover author details and book credits for the person page."""
    cache_key = f"{Sources.HARDCOVER.value}_person_{person_id}_v5"
    data = cache.get(cache_key)

    if data is None:
        query = """
        query GetAuthor($author_id: Int!) {
          authors(where: {id: {_eq: $author_id}}, limit: 1) {
            id
            name
            bio
            born_date
            born_year
            death_date
            death_year
            slug
            alias_id
            books_count
            cached_image(path: "url")
            contributions(
              where: {
                contributable_type: {_eq: "Book"},
                book: {
                  book_status_id: {_eq: 1},
                  canonical_id: {_is_null: true},
                  compilation: {_eq: false},
                  is_partial_book: {_eq: false}
                }
              },
              limit: 100
            ) {
              contribution
              book {
                id
                title
                book_status_id
                canonical_id
                compilation
                is_partial_book
                cached_image(path: "url")
                release_year
                release_date
                rating
                ratings_count
                reviews_count
                users_count
                cached_featured_series
              }
            }
          }
          aliases: authors(where: {alias_id: {_eq: $author_id}}, limit: 50) {
            id
            name
          }
        }
        """
        try:
            response = services.api_request(
                Sources.HARDCOVER.value,
                "POST",
                base_url,
                params={"query": query, "variables": {"author_id": int(person_id)}},
                headers={"Authorization": settings.HARDCOVER_API},
            )
        except requests.exceptions.HTTPError as error:
            handle_error(error)

        author = (response.get("data", {}).get("authors") or [None])[0]
        if not author:
            services.raise_not_found_error(Sources.HARDCOVER.value, person_id, "person")

        author["aliases"] = response.get("data", {}).get("aliases") or []
        credits = get_author_books(author)
        data = {
            "source": Sources.HARDCOVER.value,
            "person_id": str(author.get("id") or person_id),
            "name": author.get("name") or "",
            "image": author.get("cached_image") or settings.IMG_NONE,
            "biography": (author.get("bio") or "").strip() or None,
            "known_for_department": "Author",
            "birth_date": author.get("born_date") or (str(author["born_year"]) if author.get("born_year") else None),
            "death_date": author.get("death_date") or (str(author["death_year"]) if author.get("death_year") else None),
            "place_of_birth": None,
            "popularity": author.get("books_count"),
            "series": get_author_series(credits),
            "credits": credits,
        }

        cache.set(cache_key, data)

    return data


def get_author_books(author):
    """Return active canonical Hardcover books for an author."""
    books = []
    try:
        books = fetch_author_books(author)
    except Exception as error:  # noqa: BLE001
        logger.warning("Hardcover author books fallback for %s failed: %s", author.get("id"), error)

    # Keep the profile relation as a fallback because Hardcover data can be incomplete in either direction.
    for contribution in author.get("contributions") or []:
        if contribution.get("book"):
            books.append({
                **contribution["book"],
                "matched_contributions": [contribution],
            })

    best_by_id = {}
    for book_data in books:
        credit = author_book_credit(book_data, author)
        if not credit:
            continue
        current = best_by_id.get(credit["media_id"])
        if current is None or author_book_sort_key(credit) < author_book_sort_key(current):
            best_by_id[credit["media_id"]] = credit

    return sorted(best_by_id.values(), key=author_book_sort_key)


def fetch_author_books(author):
    """Fetch an author's active canonical books by stable author id."""
    author_ids = [
        int(author_id)
        for author_id in [
            author.get("id"),
            *(alias.get("id") for alias in author.get("aliases") or []),
        ]
        if author_id
    ]
    query = """
    query GetAuthorBooks($author_ids: [Int!]!, $limit: Int!) {
      books(
        where: {
          book_status_id: {_eq: 1},
          canonical_id: {_is_null: true},
          compilation: {_eq: false},
          is_partial_book: {_eq: false},
          contributions: {author: {id: {_in: $author_ids}}}
        },
        limit: $limit,
        order_by: [{users_count: desc}, {ratings_count: desc}, {reviews_count: desc}, {title: asc}]
      ) {
        id
        title
        book_status_id
        canonical_id
        compilation
        is_partial_book
        cached_image(path: "url")
        release_year
        release_date
        rating
        ratings_count
        reviews_count
        users_count
        cached_featured_series
        contributions(where: {author: {id: {_in: $author_ids}}}) {
          contribution
          author {
            id
            name
          }
        }
      }
    }
    """
    response = services.api_request(
        Sources.HARDCOVER.value,
        "POST",
        base_url,
        params={
            "query": query,
            "variables": {
                "author_ids": author_ids,
                "limit": AUTHOR_BOOK_LIMIT,
            },
        },
        headers={"Authorization": settings.HARDCOVER_API},
    )
    data = response.get("data") or {}
    return data.get("books") or []


def author_book_credit(book_data, author):
    """Format one Hardcover book as an iOS-ready person credit."""
    book_id = book_data.get("id")
    if (
        not book_id
        or book_data.get("book_status_id", 1) != 1
        or book_data.get("canonical_id")
        or book_data.get("compilation")
        or book_data.get("is_partial_book")
    ):
        return None
    roles = author_book_roles(book_data, author)
    year = book_data.get("release_year")
    return {
        "media_type": MediaTypes.BOOK.value,
        "source": Sources.HARDCOVER.value,
        "media_id": str(book_id),
        "title": book_data.get("title") or "",
        "image": book_data.get("cached_image") or settings.IMG_NONE,
        "roles": roles or ["Author"],
        "year": str(year) if year else None,
        "release_date": book_data.get("release_date"),
        "rating": book_data.get("rating"),
        "ratings_count": book_data.get("ratings_count") or 0,
        "reviews_count": book_data.get("reviews_count") or 0,
        "users_count": book_data.get("users_count") or 0,
        "is_author_role": "Author" in roles or not roles,
        "series": get_featured_series(book_data.get("cached_featured_series")),
    }


def author_book_roles(book_data, author):
    """Return roles matching this author or one of their aliases."""
    author_ids = {
        str(author_id)
        for author_id in [
            author.get("id"),
            *(alias.get("id") for alias in author.get("aliases") or []),
        ]
        if author_id
    }
    author_names = {
        name
        for name in [
            author.get("name"),
            *(alias.get("name") for alias in author.get("aliases") or []),
        ]
        if name
    }
    roles = []
    for contribution in book_data.get("matched_contributions") or book_data.get("contributions") or []:
        contributor = contribution.get("author") or {}
        if (
            str(contributor.get("id") or "") not in author_ids
            and contributor.get("name") not in author_names
        ):
            continue
        role = contribution.get("contribution") or "Author"
        if role not in roles:
            roles.append(role)
    return roles


def author_book_sort_key(item):
    """Sort true authored books first, then popularity, then title."""
    return (
        0 if item.get("is_author_role") else 1,
        -float(item.get("users_count") or 0),
        -float(item.get("ratings_count") or 0),
        -float(item.get("reviews_count") or 0),
        item.get("title") or "",
    )


def get_author_series(credits):
    """Group an author's canonical books into their primary Hardcover series."""
    grouped = {}
    for credit in credits:
        if not credit.get("is_author_role"):
            continue
        series = credit.get("series") or {}
        position = _primary_series_position(
            series.get("position"),
            series.get("primary_books_count"),
        )
        series_id = series.get("id")
        if position is None or not series_id:
            continue
        group = grouped.setdefault(
            str(series_id),
            {
                "series_id": str(series_id),
                "name": series.get("name") or "",
                "book_count": series.get("primary_books_count"),
                "books": [],
            },
        )
        group["books"].append({**credit, "position": position})

    result = []
    for series in grouped.values():
        best_by_position = {}
        for book in series["books"]:
            current = best_by_position.get(book["position"])
            if current is None or book.get("users_count", 0) > current.get("users_count", 0):
                best_by_position[book["position"]] = book
        series["books"] = sorted(best_by_position.values(), key=lambda book: book["position"])
        series["book_count"] = series["book_count"] or len(series["books"])
        if series["book_count"] > 1:
            result.append(series)
    return sorted(
        result,
        key=lambda series: (
            -sum(float(book.get("users_count") or 0) for book in series["books"]),
            series["name"].casefold(),
        ),
    )


def get_series_books(series_id):
    """Fetch and format Hardcover series books for related carousels."""
    return series_page(series_id)["books"]


def series_page(series_id):
    """Return one Hardcover series with only its primary numbered books."""
    cache_key = f"{Sources.HARDCOVER.value}_series_{series_id}_v1"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    query = """
    query GetSeriesBooks($series_id: Int!) {
      series_by_pk(id: $series_id) {
        id
        name
        primary_books_count
        book_series(order_by: {position: asc}) {
          position
          compilation
          book {
            id
            title
            slug
            users_read_count
            cached_image(path: "url")
          }
        }
      }
    }
    """

    try:
        response = services.api_request(
            Sources.HARDCOVER.value,
            "POST",
            base_url,
            params={"query": query, "variables": {"series_id": int(series_id)}},
            headers={"Authorization": settings.HARDCOVER_API},
        )
    except Exception as e:
        logger.warning("Series books failed: %s", e)
        return {
            "series_id": str(series_id),
            "source": Sources.HARDCOVER.value,
            "name": "",
            "book_count": 0,
            "books": [],
        }

    series = response.get("data", {}).get("series_by_pk") or {}
    books = _primary_series_books(
        series.get("book_series") or [],
        series.get("primary_books_count"),
    )
    data = {
        "series_id": str(series.get("id") or series_id),
        "source": Sources.HARDCOVER.value,
        "name": series.get("name") or "",
        "book_count": series.get("primary_books_count") or len(books),
        "books": books,
    }
    cache.set(cache_key, data)
    return data


def _primary_series_books(rows, primary_books_count):
    """Choose the most-read canonical book for each primary series position."""
    best_by_position = {}
    for row in rows:
        book_data = row.get("book")
        position = _primary_series_position(row.get("position"), primary_books_count)
        if not book_data or position is None or row.get("compilation"):
            continue
        item = {
            "position": position,
            "users_read_count": book_data.get("users_read_count") or 0,
            "book": book_data,
        }
        current = best_by_position.get(position)
        if not current or item["users_read_count"] > current["users_read_count"]:
            best_by_position[position] = item

    return [
        {
            "media_id": row["book"]["id"],
            "source": Sources.HARDCOVER.value,
            "media_type": MediaTypes.BOOK.value,
            "title": row["book"]["title"],
            "image": row["book"].get("cached_image") or settings.IMG_NONE,
            "position": row["position"],
        }
        for row in sorted(best_by_position.values(), key=lambda item: item["position"])
    ]


def _primary_series_position(position, primary_books_count):
    try:
        value = Decimal(str(position))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if value != value.to_integral_value() or value < 1:
        return None
    if primary_books_count is not None:
        try:
            if value > int(primary_books_count):
                return None
        except (TypeError, ValueError):
            return None
    return int(value)


def format_release_date(release_date):
    """Format release date from Hardcover API.
    
    Hardcover may return:
    - Full date string (YYYY-MM-DD)
    - Just a year (YYYY)
    - None
    
    If it's just a year, return just the year.
    If it's a full date, return it as-is.
    """
    if not release_date:
        return None
    
    # If it's already a string, check the format
    if isinstance(release_date, str):
        # Check if it's just a year (4 digits)
        if len(release_date) == 4 and release_date.isdigit():
            return release_date
        # Check if it's a date with January 1st (likely just a year stored as date)
        if release_date.endswith("-01-01"):
            year = release_date[:4]
            if year.isdigit():
                logger.debug("Release date %s appears to be just a year, returning %s", release_date, year)
                return year
        # Return the date as-is if it's a proper date
        return release_date
    
    # If it's a date object, format it
    if hasattr(release_date, 'year'):
        # If it's January 1st, it's likely just a year
        if release_date.month == 1 and release_date.day == 1:
            return str(release_date.year)
        return release_date.strftime("%Y-%m-%d")
    
    return str(release_date)


def get_tags(tags_data):
    """Get processed tags/genres from API data."""
    if not tags_data:
        return None
    return [tag["tag"] for tag in tags_data]


def get_ratings(rating_data):
    """Get processed rating from API data."""
    if not rating_data:
        return None
    return round(float(rating_data), 1)


def get_edition_details(edition_data):
    """Get processed edition details from API data."""
    if not edition_data:
        return {}

    isbns = []
    if edition_data.get("isbn_10"):
        isbns.append(edition_data["isbn_10"])
    if edition_data.get("isbn_13"):
        isbns.append(edition_data["isbn_13"])

    publisher_name = None
    if edition_data.get("publisher"):
        publisher_name = edition_data["publisher"].get("name")

    return {
        "format": edition_data.get("edition_format") or "Unknown",
        "publisher": publisher_name,
        "isbn": isbns or None,
        "release_date": edition_data.get("release_date"),
    }


def get_publishers(book_data):
    """Get list of unique publishers from book data."""
    publishers = set()
    
    # Get publisher from default cover edition
    default_edition = book_data.get("default_cover_edition")
    if default_edition and default_edition.get("publisher"):
        publisher_name = default_edition["publisher"].get("name")
        if publisher_name:
            publishers.add(publisher_name)
    
    # Note: We're only getting publisher from default_cover_edition for now
    # If we need publishers from all editions, we'd need a separate query
    
    result = list(publishers) if publishers else None
    logger.debug("Extracted publishers: %s", result)
    return result


def get_languages(book_data):
    """Get list of languages from book data."""
    language = book_data.get("language")
    
    if not language:
        logger.debug("No language found in book data")
        return None
    
    # Language might be a string or a list
    if isinstance(language, list):
        languages = [lang for lang in language if lang]
    else:
        languages = [language] if language else []
    
    # Convert language codes to readable names if needed
    language_names = {
        "en": "English",
        "es": "Spanish",
        "fr": "French",
        "de": "German",
        "it": "Italian",
        "pt": "Portuguese",
        "ru": "Russian",
        "ja": "Japanese",
        "zh": "Chinese",
        "ar": "Arabic",
        "hi": "Hindi",
        "ko": "Korean",
        "nl": "Dutch",
        "pl": "Polish",
        "sv": "Swedish",
        "no": "Norwegian",
        "da": "Danish",
        "fi": "Finnish",
        "el": "Greek",
        "he": "Hebrew",
        "tr": "Turkish",
        "cs": "Czech",
        "hu": "Hungarian",
        "ro": "Romanian",
    }
    
    result = []
    for lang in languages:
        # If it's already a readable name, use it
        if len(lang) > 2 or lang not in language_names:
            result.append(lang)
        else:
            # Convert code to name
            result.append(language_names.get(lang, lang.upper()))
    
    logger.debug("Extracted languages: %s (from %s)", result, language)
    return result if result else None


def get_isbns_from_book(book_data):
    """Get all ISBNs from book data."""
    isbns = []
    
    # Get ISBNs from default cover edition
    default_edition = book_data.get("default_cover_edition")
    if default_edition:
        if default_edition.get("isbn_13"):
            isbns.append(default_edition["isbn_13"])
        if default_edition.get("isbn_10"):
            isbns.append(default_edition["isbn_10"])
    
    # Note: For now we only get ISBNs from default_cover_edition
    # If we need ISBNs from all editions, we can use the existing get_book_isbns function
    
    result = isbns if isbns else None
    logger.debug("Extracted ISBNs: %s", result)
    return result


def get_recommendations(recommendations_data):
    """Get processed recommendations from API data."""
    if not recommendations_data:
        return []

    return [
        {
            "media_id": rec["item_book"]["id"],
            "source": Sources.HARDCOVER.value,
            "title": rec["item_book"]["title"],
            "media_type": MediaTypes.BOOK.value,
            "image": rec["item_book"].get("cached_image") or settings.IMG_NONE,
        }
        for rec in recommendations_data
        if rec.get("item_book")
    ]


def get_image_url(response):
    """Get the cover image URL for a book."""
    if response.get("image") and response["image"].get("url"):
        return response["image"]["url"]
    return settings.IMG_NONE


def get_book_isbns(media_id):
    """
    Get ISBNs for a book from Hardcover, including editions.
    
    Args:
        media_id: Hardcover book ID
        
    Returns:
        List of ISBN-10 and ISBN-13 numbers
    """
    query = """
    query GetBookISBNs($book_id: Int!) {
      books_by_pk(id: $book_id) {
        editions {
          isbn_10
          isbn_13
        }
        default_cover_edition {
          isbn_10
          isbn_13
        }
      }
    }
    """
    
    variables = {"book_id": int(media_id)}
    
    try:
        response = services.api_request(
            Sources.HARDCOVER.value,
            "POST",
            base_url,
            params={"query": query, "variables": variables},
            headers={"Authorization": settings.HARDCOVER_API},
        )
        
        if "errors" in response:
            logger.error("GraphQL errors fetching ISBNs: %s", response["errors"])
            return []
        
        book_data = response["data"]["books_by_pk"]
        isbns = []
        
        # Get ISBNs from default cover edition first
        default_edition = book_data.get("default_cover_edition")
        if default_edition:
            if default_edition.get("isbn_13"):
                isbns.append(default_edition["isbn_13"])
            if default_edition.get("isbn_10"):
                isbns.append(default_edition["isbn_10"])
        
        # Get ISBNs from other editions
        editions = book_data.get("editions", [])
        for edition in editions:
            if edition.get("isbn_13") and edition["isbn_13"] not in isbns:
                isbns.append(edition["isbn_13"])
            if edition.get("isbn_10") and edition["isbn_10"] not in isbns:
                isbns.append(edition["isbn_10"])
        
        return isbns
        
    except Exception as e:
        logger.error(f"Error fetching ISBNs for book {media_id}: {e}")
        return []
