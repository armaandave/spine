"""Film and TV live sources: Letterboxd and IMDb through MDBList, MDBList's official
charts, Netflix Top 10, Trakt, TMDB trending and the Golden Globes.

Positions: film 160-189; TV and lists holding both 260-289.

Weekly sources (Netflix publishes on Tuesdays, Trakt's box office on Mondays) are
checked every two days, so a new edition lands within two days of release.
"""

from datetime import timedelta

from lists.featured.registry import FeaturedList, Guards

DAILY = timedelta(days=1)
EVERY_OTHER_DAY = timedelta(days=2)
WEEKLY = timedelta(days=7)

LETTERBOXD_GUARDS = Guards(require_exact_count=True, min_resolve_rate=0.95)
TOP_250_GUARDS = Guards(min_resolve_rate=0.95)
CHART_GUARDS = Guards(min_resolve_rate=0.8)
TITLE_ONLY_GUARDS = Guards(min_resolve_rate=0.7)
AWARD_GUARDS = Guards(min_resolve_rate=0.9, max_title_searches=100)


def _letterboxd(key, slug, name, description, position, media_types=("movie",)):
    url = f"https://letterboxd.com/official/list/{slug}/"
    return FeaturedList(
        key=key,
        name=name,
        media_types=media_types,
        kind="community",
        position=position,
        adapter="mdblist",
        params={"external": url},
        cadence=DAILY,
        source_name="Letterboxd",
        source_url=url,
        description=description,
        guards=LETTERBOXD_GUARDS,
    )


LETTERBOXD = [
    _letterboxd(
        "letterboxd-top-250-documentary",
        "top-250-documentary-films",
        "Letterboxd's Top 250 Documentary Films",
        "The 250 highest-rated documentary films on Letterboxd.",
        160,
    ),
    _letterboxd(
        "letterboxd-top-250-science-fiction",
        "top-250-science-fiction-films",
        "Letterboxd's Top 250 Science Fiction Films",
        "The 250 highest-rated science fiction films on Letterboxd.",
        161,
    ),
    _letterboxd(
        "letterboxd-top-250-horror",
        "top-250-horror-films",
        "Letterboxd's Top 250 Horror Films",
        "The 250 highest-rated horror films on Letterboxd.",
        162,
    ),
    _letterboxd(
        "letterboxd-top-250-animated",
        "top-250-animated-films",
        "Letterboxd's Top 250 Animated Films",
        "The 250 highest-rated animated films on Letterboxd.",
        163,
    ),
    _letterboxd(
        "letterboxd-top-250-romantic-comedy",
        "top-250-romantic-comedy-films",
        "Letterboxd's Top 250 Romantic Comedies",
        "The 250 highest-rated romantic comedies on Letterboxd.",
        164,
    ),
    _letterboxd(
        "letterboxd-top-100-anime-films",
        "top-100-anime-films",
        "Letterboxd's Top 100 Anime Films",
        "The 100 highest-rated anime films on Letterboxd.",
        165,
    ),
    _letterboxd(
        "letterboxd-top-250-miniseries",
        "top-250-miniseries",
        "Letterboxd's Top 250 Miniseries",
        "The 250 highest-rated miniseries on Letterboxd.",
        260,
        media_types=("tv",),
    ),
]

IMDB = [
    FeaturedList(
        key="imdb-top-250-movies",
        name="IMDb Top 250 Movies",
        media_types=("movie",),
        kind="community",
        position=166,
        adapter="mdblist",
        params={"list_id": "101881", "fallback_ids": ["12306"]},
        cadence=DAILY,
        source_name="IMDb",
        source_url="https://www.imdb.com/chart/top/",
        description="The 250 top-rated movies as voted by IMDb users.",
        guards=TOP_250_GUARDS,
    ),
    FeaturedList(
        key="imdb-top-250-tv",
        name="IMDb Top 250 TV Shows",
        media_types=("tv",),
        kind="community",
        position=261,
        adapter="mdblist",
        params={"list_id": "94576", "fallback_ids": ["63804"]},
        cadence=DAILY,
        source_name="IMDb",
        source_url="https://www.imdb.com/chart/toptv/",
        description="The 250 top-rated TV shows as voted by IMDb users.",
        guards=TOP_250_GUARDS,
    ),
]

# MovieMeter and JustWatch rank movies and shows separately, so each is two lists:
# one combined list would show two #1s.
MDBLIST_CHARTS = [
    FeaturedList(
        key="imdb-moviemeter-movies",
        name="IMDb's Most Popular Movies",
        media_types=("movie",),
        kind="chart",
        position=167,
        adapter="mdblist",
        params={"official": "moviemeter", "mediatype": "movie"},
        cadence=DAILY,
        source_name="IMDb",
        source_url="https://www.imdb.com/chart/moviemeter/",
        description="IMDb's MovieMeter: the movies people are looking at on IMDb right now.",
        guards=CHART_GUARDS,
    ),
    FeaturedList(
        key="imdb-moviemeter-tv",
        name="IMDb's Most Popular TV Shows",
        media_types=("tv",),
        kind="chart",
        position=262,
        adapter="mdblist",
        params={"official": "moviemeter", "mediatype": "show"},
        cadence=DAILY,
        source_name="IMDb",
        source_url="https://www.imdb.com/chart/tvmeter/",
        description="IMDb's TV Meter: the shows people are looking at on IMDb right now.",
        guards=CHART_GUARDS,
    ),
    FeaturedList(
        key="justwatch-streaming-charts-movies",
        name="JustWatch Streaming Charts: Movies",
        media_types=("movie",),
        kind="chart",
        position=168,
        adapter="mdblist",
        params={"official": "justwatch-streaming-charts", "mediatype": "movie"},
        cadence=DAILY,
        source_name="JustWatch",
        source_url="https://www.justwatch.com/us/streaming-charts",
        description="The movies most watched across streaming services, by JustWatch.",
        guards=CHART_GUARDS,
    ),
    FeaturedList(
        key="justwatch-streaming-charts-tv",
        name="JustWatch Streaming Charts: TV Shows",
        media_types=("tv",),
        kind="chart",
        position=263,
        adapter="mdblist",
        params={"official": "justwatch-streaming-charts", "mediatype": "show"},
        cadence=DAILY,
        source_name="JustWatch",
        source_url="https://www.justwatch.com/us/streaming-charts",
        description="The shows most watched across streaming services, by JustWatch.",
        guards=CHART_GUARDS,
    ),
]

TRAKT = [
    FeaturedList(
        key="trakt-weekend-box-office",
        name="Weekend Box Office",
        media_types=("movie",),
        kind="chart",
        position=169,
        adapter="trakt",
        params={"chart": "boxoffice"},
        cadence=EVERY_OTHER_DAY,
        source_name="Trakt",
        source_url="https://trakt.tv/movies/boxoffice",
        description="The top 10 movies at the US box office last weekend.",
        guards=CHART_GUARDS,
    ),
    FeaturedList(
        key="trakt-trending-movies",
        name="Trending Movies on Trakt",
        media_types=("movie",),
        kind="chart",
        position=170,
        adapter="trakt",
        params={"chart": "trending", "media": "movies", "limit": 20},
        cadence=DAILY,
        source_name="Trakt",
        source_url="https://trakt.tv/movies/trending",
        description="The movies the most Trakt members are watching right now.",
        guards=CHART_GUARDS,
    ),
    FeaturedList(
        key="trakt-trending-shows",
        name="Trending Shows on Trakt",
        media_types=("tv",),
        kind="chart",
        position=264,
        adapter="trakt",
        params={"chart": "trending", "media": "shows", "limit": 20},
        cadence=DAILY,
        source_name="Trakt",
        source_url="https://trakt.tv/shows/trending",
        description="The shows the most Trakt members are watching right now.",
        guards=CHART_GUARDS,
    ),
]

TMDB = [
    FeaturedList(
        key="tmdb-trending-movies-week",
        name="Trending Movies This Week",
        media_types=("movie",),
        kind="chart",
        position=171,
        adapter="tmdb_trending",
        params={"media_type": "movie", "window": "week"},
        cadence=DAILY,
        source_name="TMDB",
        source_url="https://www.themoviedb.org/movie",
        description="The movies trending on The Movie Database this week.",
        guards=CHART_GUARDS,
    ),
    FeaturedList(
        key="tmdb-trending-tv-week",
        name="Trending TV This Week",
        media_types=("tv",),
        kind="chart",
        position=265,
        adapter="tmdb_trending",
        params={"media_type": "tv", "window": "week"},
        cadence=DAILY,
        source_name="TMDB",
        source_url="https://www.themoviedb.org/tv",
        description="The shows trending on The Movie Database this week.",
        guards=CHART_GUARDS,
    ),
]


def _netflix(key, name, media_type, position, description, **params):
    return FeaturedList(
        key=key,
        name=name,
        media_types=(media_type,),
        kind="chart",
        position=position,
        adapter="netflix",
        params=params,
        cadence=EVERY_OTHER_DAY,
        source_name="Netflix",
        source_url="https://www.netflix.com/tudum/top10" + ("/most-popular" if params["file"] == "most-popular" else ""),
        description=description,
        guards=TITLE_ONLY_GUARDS,
    )


NETFLIX = [
    _netflix(
        "netflix-top-10-films-english",
        "Netflix Top 10: Films (English)",
        "movie",
        172,
        "The most-watched English-language films on Netflix worldwide last week.",
        file="weekly",
        category="Films (English)",
    ),
    _netflix(
        "netflix-top-10-films-non-english",
        "Netflix Top 10: Films (Non-English)",
        "movie",
        173,
        "The most-watched non-English-language films on Netflix worldwide last week.",
        file="weekly",
        category="Films (Non-English)",
    ),
    _netflix(
        "netflix-most-popular-films",
        "Netflix's Most Popular Films",
        "movie",
        174,
        "The most-watched Netflix films ever, by views in their first 91 days.",
        file="most-popular",
        categories=["Films (English)", "Films (Non-English)"],
    ),
    _netflix(
        "netflix-top-10-tv-english",
        "Netflix Top 10: TV (English)",
        "tv",
        266,
        "The most-watched English-language shows on Netflix worldwide last week.",
        file="weekly",
        category="TV (English)",
    ),
    _netflix(
        "netflix-top-10-tv-non-english",
        "Netflix Top 10: TV (Non-English)",
        "tv",
        267,
        "The most-watched non-English-language shows on Netflix worldwide last week.",
        file="weekly",
        category="TV (Non-English)",
    ),
    _netflix(
        "netflix-most-popular-tv",
        "Netflix's Most Popular TV",
        "tv",
        268,
        "The most-watched Netflix shows ever, by views of a season in its first 91 days.",
        file="most-popular",
        categories=["TV (English)", "TV (Non-English)"],
    ),
]


def _globes(key, name, media_type, position, category, description):
    return FeaturedList(
        key=key,
        name=name,
        media_types=(media_type,),
        kind="award",
        position=position,
        adapter="goldenglobes",
        params={"category": category},
        cadence=WEEKLY,
        source_name="Golden Globes",
        source_url="https://goldenglobes.com/winners-nominees/",
        description=description,
        is_ranked=False,
        guards=AWARD_GUARDS,
    )


GOLDEN_GLOBES = [
    _globes(
        "golden-globes-best-motion-picture-drama",
        "Golden Globe: Best Motion Picture – Drama",
        "movie",
        175,
        5,
        "Every winner of the Golden Globe for Best Motion Picture – Drama, newest first.",
    ),
    _globes(
        "golden-globes-best-motion-picture-musical-or-comedy",
        "Golden Globe: Best Motion Picture – Musical or Comedy",
        "movie",
        176,
        7,
        "Every winner of the Golden Globe for Best Motion Picture – Musical or Comedy, newest first.",
    ),
    _globes(
        "golden-globes-best-television-series-drama",
        "Golden Globe: Best Television Series – Drama",
        "tv",
        269,
        33,
        "Every winner of the Golden Globe for Best Television Series – Drama, newest first.",
    ),
    _globes(
        "golden-globes-best-television-series-musical-or-comedy",
        "Golden Globe: Best Television Series – Musical or Comedy",
        "tv",
        270,
        35,
        "Every winner of the Golden Globe for Best Television Series – Musical or Comedy, newest first.",
    ),
]

NIELSEN = [
    FeaturedList(
        key="nielsen-streaming-top-10",
        name="Nielsen Streaming Top 10",
        media_types=("tv", "movie"),
        kind="chart",
        position=271,
        adapter="nielsen",
        params={"chart": "overall"},
        cadence=EVERY_OTHER_DAY,
        source_name="Nielsen",
        source_url="https://www.nielsen.com/data-center/top-ten/",
        description="The most-watched titles on US streaming by minutes viewed, about two weeks behind.",
        guards=TITLE_ONLY_GUARDS,
    ),
    FeaturedList(
        key="nielsen-streaming-top-10-movies",
        name="Nielsen Streaming Top 10: Movies",
        media_types=("movie",),
        kind="chart",
        position=177,
        adapter="nielsen",
        params={"chart": "movies"},
        cadence=EVERY_OTHER_DAY,
        source_name="Nielsen",
        source_url="https://www.nielsen.com/data-center/top-ten/",
        description="The most-watched movies on US streaming by minutes viewed, about two weeks behind.",
        guards=TITLE_ONLY_GUARDS,
    ),
]

DEFINITIONS = [*LETTERBOXD, *IMDB, *MDBLIST_CHARTS, *TRAKT, *TMDB, *NETFLIX, *GOLDEN_GLOBES, *NIELSEN]
