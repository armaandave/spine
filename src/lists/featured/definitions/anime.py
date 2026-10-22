"""Live anime lists from MyAnimeList. Positions 600–629.

AniList's API has been switched off since 2026-10-21, so there are no AniList lists.
"""

from datetime import timedelta

from lists.featured.registry import FeaturedList, Guards

ANIME = ("anime",)
MAL = "MyAnimeList"


def _mal(key, name, position, params, source_url, description, kind="community"):
    return FeaturedList(
        key=key,
        name=name,
        media_types=ANIME,
        kind=kind,
        position=position,
        adapter="mal_anime",
        params=params,
        cadence=timedelta(days=1),
        source_name=MAL,
        source_url=source_url,
        description=description,
        guards=Guards(min_resolve_rate=0.95),
    )


DEFINITIONS = [
    _mal(
        "mal-top-anime",
        "MyAnimeList: Top Anime",
        600,
        {"ranking": "all", "min_entries": 90},
        "https://myanimelist.net/topanime.php",
        "The highest-rated anime on MyAnimeList.",
    ),
    _mal(
        "mal-top-airing-anime",
        "MyAnimeList: Top Airing Anime",
        601,
        {"ranking": "airing", "min_entries": 90},
        "https://myanimelist.net/topanime.php?type=airing",
        "The highest-rated anime airing now on MyAnimeList.",
        kind="chart",
    ),
    _mal(
        "mal-most-popular-anime",
        "MyAnimeList: Most Popular Anime",
        602,
        {"ranking": "bypopularity", "min_entries": 90},
        "https://myanimelist.net/topanime.php?type=bypopularity",
        "The anime on the most MyAnimeList members' lists.",
    ),
    _mal(
        "mal-this-season-anime",
        "MyAnimeList: This Season",
        603,
        {"season": "current", "limit": 50, "min_entries": 25},  # premieres only; a season has 100+
        "https://myanimelist.net/anime/season",
        "This season's new anime, by how many MyAnimeList members are following them.",
        kind="chart",
    ),
]
