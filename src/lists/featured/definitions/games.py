"""Live game lists: Steam and Twitch charts, IGDB popularity, The Steam Awards.

Positions 540–569.
"""

from datetime import timedelta

from lists.featured.registry import FeaturedList, Guards

GAME = ("game",)

CHARTS = [
    FeaturedList(
        key="steam-weekly-top-sellers",
        name="Steam Weekly Top Sellers",
        media_types=GAME,
        kind="chart",
        position=540,
        adapter="steam",
        params={"chart": "weekly_top_sellers", "country": "US"},
        cadence=timedelta(days=2),  # weekly chart, checked every other day so a new week lands soon
        source_name="Steam",
        source_url="https://store.steampowered.com/charts/topsellers/US",
        description="Last week's best-selling games on Steam in the US, by revenue. Hardware, DLC and preorders left out.",
        guards=Guards(min_resolve_rate=0.8),
    ),
    FeaturedList(
        key="steam-most-played",
        name="Steam Most Played",
        media_types=GAME,
        kind="chart",
        position=541,
        adapter="steam",
        params={"chart": "most_played"},
        cadence=timedelta(days=1),
        source_name="Steam",
        source_url="https://store.steampowered.com/charts/mostplayed",
        description="Yesterday's most-played games on Steam, by peak concurrent players.",
        guards=Guards(min_resolve_rate=0.8),
    ),
    FeaturedList(
        key="twitch-top-games",
        name="Twitch Top Games",
        media_types=GAME,
        kind="chart",
        position=542,
        adapter="twitch",
        params={"limit": 100},
        cadence=timedelta(hours=1),
        source_name="Twitch",
        source_url="https://www.twitch.tv/directory?sort=VIEWER_COUNT",
        description="The games with the most viewers on Twitch right now.",
        guards=Guards(min_resolve_rate=0.8),
    ),
    FeaturedList(
        key="igdb-most-wishlisted-upcoming",
        name="Most Wishlisted Upcoming Games",
        media_types=GAME,
        kind="chart",
        position=543,
        adapter="igdb_popularity",
        params={"popularity_type": 10, "limit": 50},
        cadence=timedelta(days=1),
        source_name="IGDB",
        source_url="https://www.igdb.com/",
        description="The unreleased games with the most Steam wishlists, via IGDB.",
        guards=Guards(min_resolve_rate=0.9),
    ),
    FeaturedList(
        key="igdb-playing",
        name="What IGDB Members Are Playing",
        media_types=GAME,
        kind="community",
        position=544,
        adapter="igdb_popularity",
        params={"popularity_type": 3, "limit": 50},
        cadence=timedelta(days=1),
        source_name="IGDB",
        source_url="https://www.igdb.com/",
        description="The games most marked as playing on IGDB.",
        guards=Guards(min_resolve_rate=0.9),
    ),
    FeaturedList(
        key="igdb-want-to-play",
        name="IGDB's Most Wanted",
        media_types=GAME,
        kind="community",
        position=545,
        adapter="igdb_popularity",
        params={"popularity_type": 2, "limit": 50},
        cadence=timedelta(days=1),
        source_name="IGDB",
        source_url="https://www.igdb.com/",
        description="The games most added to IGDB members' want-to-play lists.",
        guards=Guards(min_resolve_rate=0.9),
    ),
]

STEAM_AWARDS_YEARS = (2025, 2024, 2023)

STEAM_AWARDS = [
    FeaturedList(
        key="steam-awards-game-of-the-year",
        name="The Steam Awards: Game of the Year",
        media_types=GAME,
        kind="award",
        position=550,
        adapter="steam_awards",
        params={"category": "GOTY", "since": 2018},
        cadence=timedelta(days=7),  # picks up each new year's winner (announced around New Year)
        source_name="Steam",
        source_url="https://store.steampowered.com/steamawards/",
        description="Every Game of the Year winner of The Steam Awards, voted by Steam players, since the category began in 2018.",
        is_ranked=False,
    ),
    *[
        FeaturedList(
            key=f"steam-awards-{year}",
            name=f"The Steam Awards {year}",
            media_types=GAME,
            kind="award",
            position=551 + index,
            adapter="steam_awards",
            params={"year": year},
            cadence=None,  # a past year's winners don't change
            source_name="Steam",
            source_url=f"https://store.steampowered.com/steamawards/{year}",
            description=f"The winners of every category of The Steam Awards {year}, voted by Steam players.",
            is_ranked=False,
        )
        for index, year in enumerate(STEAM_AWARDS_YEARS)
    ],
]

DEFINITIONS = [*CHARTS, *STEAM_AWARDS]
