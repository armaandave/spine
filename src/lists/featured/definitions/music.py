"""Music lists (positions 700-749). A Spine music item is a MusicBrainz release group.

Every list sets ``max_title_searches=0``: the adapters already ran their own strict
album search (no singles, edition markers removed), and the generic title search
can't tell an album from a single of the same name.
"""

from datetime import timedelta

from lists.featured.registry import FeaturedList, Guards

DAILY = timedelta(days=1)
EVERY_OTHER_DAY = timedelta(days=2)  # weekly charts: the card still reads "weekly"
WEEKLY = timedelta(days=7)
# Yearly lists: re-checked about monthly so a new edition, or a winner MusicBrainz
# editors add after the ceremony, appears within weeks. Over 32 days reads "yearly".
YEARLY = timedelta(days=33)

MUSIC = ("music",)
ID_GUARDS = Guards(min_resolve_rate=0.9, max_title_searches=0)
CHART_GUARDS = Guards(min_resolve_rate=0.6, max_title_searches=0)
MB = "https://musicbrainz.org/series/"


def _series(key, name, position, series, *, kind="canon", source_name, source_url, description, **fields):
    params = {"series": series} if isinstance(series, str) else series
    return FeaturedList(
        key=key,
        name=name,
        media_types=MUSIC,
        kind=kind,
        position=position,
        adapter="musicbrainz_series",
        params=params,
        cadence=fields.pop("cadence", None),
        source_name=source_name,
        source_url=source_url,
        description=description,
        guards=fields.pop("guards", ID_GUARDS),
        **fields,
    )


CHARTS = [
    FeaturedList(
        key="apple-music-top-albums-us",
        name="Apple Music Top Albums: US",
        media_types=MUSIC,
        kind="chart",
        position=700,
        adapter="apple_music",
        params={"country": "us", "limit": 100, "min_entries": 90},
        cadence=DAILY,
        source_name="Apple Music",
        source_url="https://rss.marketingtools.apple.com/",
        description="The most-played albums on Apple Music in the US today.",
        guards=CHART_GUARDS,
    ),
    FeaturedList(
        key="apple-music-top-albums-uk",
        name="Apple Music Top Albums: UK",
        media_types=MUSIC,
        kind="chart",
        position=701,
        adapter="apple_music",
        params={"country": "gb", "limit": 100, "min_entries": 90},
        cadence=DAILY,
        source_name="Apple Music",
        source_url="https://rss.marketingtools.apple.com/",
        description="The most-played albums on Apple Music in the UK today.",
        guards=CHART_GUARDS,
    ),
    FeaturedList(
        key="official-charts-uk-albums",
        name="Official Albums Chart Top 100",
        media_types=MUSIC,
        kind="chart",
        position=702,
        adapter="official_charts",
        params={"chart": "albums-chart", "min_entries": 95},  # a top 100
        # The chart comes out on Fridays; a weekly cadence counted from the first sync could
        # lag it by six days. Re-reads are cheap: MusicBrainz answers are cached.
        cadence=EVERY_OTHER_DAY,
        source_name="Official Charts",
        source_url="https://www.officialcharts.com/charts/albums-chart/",
        description="The UK's best-selling and most-streamed albums of the week.",
        guards=CHART_GUARDS,
    ),
    FeaturedList(
        key="listenbrainz-top-albums-week",
        name="ListenBrainz Top Albums of the Week",
        media_types=MUSIC,
        kind="chart",
        position=703,
        adapter="listenbrainz",
        params={"range": "week", "limit": 50, "min_entries": 40},
        cadence=DAILY,  # the stats land a day or two after the week ends
        source_name="ListenBrainz",
        source_url="https://listenbrainz.org/statistics/?range=week",
        description="The albums ListenBrainz users played most last week.",
        guards=CHART_GUARDS,
    ),
    FeaturedList(
        key="listenbrainz-top-albums-all-time",
        name="ListenBrainz Top Albums of All Time",
        media_types=MUSIC,
        kind="community",
        position=704,
        adapter="listenbrainz",
        params={"range": "all_time", "limit": 100, "min_entries": 70},  # 100 rows less singles
        cadence=WEEKLY,
        source_name="ListenBrainz",
        source_url="https://listenbrainz.org/statistics/?range=all_time",
        description="The albums ListenBrainz users have played most of all time.",
        guards=CHART_GUARDS,
    ),
    FeaturedList(
        key="1001-albums-generator-highest-rated",
        name="1001 Albums Generator: Highest Rated",
        media_types=MUSIC,
        kind="community",
        position=705,
        adapter="albums_generator",
        params={"limit": 100, "min_votes": 100, "min_entries": 90},
        cadence=DAILY,
        source_name="1001 Albums Generator",
        source_url="https://1001albumsgenerator.com/",
        description=(
            "The 100 albums from the 1001 Albums You Must Hear Before You Die canon that "
            "the 1001 Albums Generator community rates highest."
        ),
        guards=CHART_GUARDS,
    ),
]

AWARDS_AND_CURATORS = [
    _series(
        "pitchfork-best-new-album",
        "Pitchfork Best New Album",
        706,
        {"series": "9e28cd79-7a4c-4f19-ac8c-7818568c4c1f", "order": "date", "window_days": 365},
        kind="curator",
        cadence=WEEKLY,
        is_ranked=False,
        source_name="Pitchfork",
        source_url="https://pitchfork.com/reviews/best/albums/",
        description="Every album Pitchfork named Best New Album in the last 12 months, newest first.",
    ),
    _series(
        "grammy-album-of-the-year",
        "Grammy Award for Album of the Year",
        707,
        {"series": "64249380-b076-4a9d-aa41-e617d81fa1c9", "order": "winners"},
        kind="award",
        cadence=YEARLY,
        is_ranked=False,
        source_name="The Recording Academy",
        source_url="https://www.grammy.com/awards/",
        description="Every Grammy Album of the Year winner, newest first.",
    ),
    _series(
        "mercury-prize-winners",
        "Mercury Prize Winners",
        708,
        {"series": "b498d904-681d-41a2-b578-8281cddf4146", "order": "winners"},
        kind="award",
        cadence=YEARLY,
        is_ranked=False,
        source_name="Mercury Prize",
        source_url="https://www.mercuryprize.com/",
        description="Every Mercury Prize album of the year, newest first.",
    ),
    _series(
        "pitchfork-best-albums-of-the-year",
        "Pitchfork's Best Albums of the Year",
        709,
        {
            "latest": {
                "query": "Pitchfork: The 50 Best Albums of",
                "pattern": r"Pitchfork: The 50 Best Albums of (\d{4})",
                "min_entries": 50,
            },
        },
        cadence=YEARLY,
        source_name="Pitchfork",
        source_url="https://pitchfork.com/features/lists-and-guides/",
        description="Pitchfork's 50 best albums of the latest year.",
    ),
    _series(
        "the-needle-drop-top-albums-of-the-year",
        "The Needle Drop's Top 50 Albums of the Year",
        710,
        {
            "latest": {
                "query": "TheNeedleDrop: Top 50 Albums of",
                "pattern": r"TheNeedleDrop: Top 50 Albums of (\d{4})",
                "min_entries": 50,
            },
        },
        kind="curator",
        cadence=YEARLY,
        source_name="The Needle Drop",
        source_url="https://www.youtube.com/@theneedledrop",
        description="Anthony Fantano's 50 favourite albums of the latest year.",
    ),
]

CANON = [
    _series(
        "rolling-stone-500-greatest-albums",
        "Rolling Stone's 500 Greatest Albums of All Time",
        711,
        "6a4b53b9-2756-4afe-93f2-306039d41910",
        source_name="Rolling Stone",
        source_url="https://www.rollingstone.com/music/music-lists/best-albums-of-all-time-1062063/",
        description="Rolling Stone's 2020 ranking of the 500 greatest albums of all time.",
    ),
    _series(
        "apple-music-100-best-albums",
        "Apple Music's 100 Best Albums",
        712,
        "d1d985e6-94ea-4c68-a7ac-63b98b6c0318",
        source_name="Apple Music",
        source_url="https://100best.music.apple.com/us",
        description="Apple Music's 2024 ranking of the 100 best albums ever made.",
    ),
    _series(
        "guardian-100-best-albums-ever",
        "The Guardian's 100 Best Albums Ever",
        713,
        "acf170fe-b358-4140-9e32-93a9c7afa544",
        source_name="The Guardian",
        source_url=f"{MB}acf170fe-b358-4140-9e32-93a9c7afa544",
        description="The Guardian's 1997 list of the 100 best albums ever.",
    ),
    _series(
        "1001-albums-you-must-hear",
        "1001 Albums You Must Hear Before You Die",
        714,
        "4bc2a338-e1d8-4546-8a61-640da8aaf888",
        is_ranked=False,
        source_name="1001 Albums You Must Hear Before You Die",
        source_url=f"{MB}4bc2a338-e1d8-4546-8a61-640da8aaf888",
        description="The 2005 edition of Robert Dimery's book, in its chronological order.",
    ),
    _series(
        "pitchfork-200-best-albums-2010s",
        "Pitchfork's 200 Best Albums of the 2010s",
        715,
        "ecae5db8-a33e-45d3-a345-9acab6d5c559",
        source_name="Pitchfork",
        source_url="https://pitchfork.com/features/lists-and-guides/the-200-best-albums-of-the-2010s/",
        description="Pitchfork's 2019 ranking of the decade's 200 best albums.",
    ),
    _series(
        "pitchfork-100-best-albums-2020s-so-far",
        "Pitchfork's 100 Best Albums of the 2020s So Far",
        716,
        "e546b910-b88f-4c92-b928-29943a68dca8",
        source_name="Pitchfork",
        source_url="https://pitchfork.com/features/lists-and-guides/the-100-best-albums-of-the-2020s-so-far/",
        description="Pitchfork's ranking of the best albums of the 2020s so far.",
    ),
    _series(
        "pitchfork-150-best-albums-1990s",
        "Pitchfork's 150 Best Albums of the 1990s",
        717,
        "4d544556-8519-4a20-b854-af57256d9717",
        source_name="Pitchfork",
        source_url="https://pitchfork.com/features/lists-and-guides/the-best-albums-of-the-1990s/",
        description="Pitchfork's 2022 ranking of the 150 best albums of the 1990s.",
    ),
    _series(
        "pitchfork-200-best-albums-1980s",
        "Pitchfork's 200 Best Albums of the 1980s",
        718,
        "2d7fadbe-6e29-471c-adb9-1d5f78c26b63",
        source_name="Pitchfork",
        source_url="https://pitchfork.com/features/lists-and-guides/the-200-best-albums-of-the-1980s/",
        description="Pitchfork's 2018 ranking of the 200 best albums of the 1980s.",
    ),
    _series(
        "pitchfork-100-best-albums-1970s",
        "Pitchfork's 100 Best Albums of the 1970s",
        719,
        "f2e5e744-d9a7-41f5-bc95-6ee787122bc1",
        source_name="Pitchfork",
        source_url="https://pitchfork.com/features/lists-and-guides/5932-top-100-albums-of-the-1970s/",
        description="Pitchfork's 2004 ranking of the 100 best albums of the 1970s.",
    ),
    _series(
        "pitchfork-200-best-albums-1960s",
        "Pitchfork's 200 Best Albums of the 1960s",
        720,
        "efbe4c84-5f83-470f-be53-ef4089ef3010",
        source_name="Pitchfork",
        source_url="https://pitchfork.com/features/lists-and-guides/the-200-best-albums-of-the-1960s/",
        description="Pitchfork's 2017 ranking of the 200 best albums of the 1960s.",
    ),
]

DEFINITIONS = [*CHARTS, *AWARDS_AND_CURATORS, *CANON]
