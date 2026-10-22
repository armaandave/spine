"""Live book and manga lists: charts, trending lists and book clubs.

Positions: books 340-369, manga 460-479. Comics (420-429) have none yet: ICv2's
BookScan chart parses cleanly, but its English volume titles matched only 4 of 14
series. The five headline NYT charts live in ``headline.py``; every other active NYT
chart is here, read from one shared ``lists/overview.json`` response
(``adapters/nyt_overview.py``).
"""

from datetime import timedelta

from lists.featured.registry import FeaturedList, Guards

DAILY = timedelta(days=1)
WEEKLY = timedelta(days=7)
NYT_TAGS = ("NYT Best Sellers", "Books")

# (slug, name, position, description). NYT publishes the "-monthly" and audio charts
# monthly; the adapter labels each edition by week or month from the overview.
NYT_OVERVIEW_CHARTS = (
    ("hardcover-fiction", "NYT Hardcover Fiction", 340, "The week's best-selling hardcover novels."),
    ("hardcover-nonfiction", "NYT Hardcover Nonfiction", 341, "The week's best-selling hardcover nonfiction."),
    ("trade-fiction-paperback", "NYT Paperback Fiction", 342, "The week's best-selling paperback novels."),
    ("paperback-nonfiction-monthly", "NYT Paperback Nonfiction", 343, "The month's best-selling paperback nonfiction."),
    ("young-adult-hardcover", "NYT Young Adult Hardcover", 344, "The week's best-selling young adult hardcovers."),
    ("young-adult-paperback-monthly", "NYT Young Adult Paperback", 345, "The month's best-selling young adult paperbacks."),
    (
        "childrens-middle-grade-hardcover",
        "NYT Children's Middle Grade Hardcover",
        346,
        "The week's best-selling middle grade hardcovers.",
    ),
    ("middle-grade-paperback-monthly", "NYT Middle Grade Paperback", 347, "The month's best-selling middle grade paperbacks."),
    ("picture-books", "NYT Children's Picture Books", 348, "The week's best-selling picture books."),
    ("series-books", "NYT Children's & Young Adult Series", 349, "The week's best-selling children's and young adult series."),
    ("audio-fiction", "NYT Audio Fiction", 350, "The month's best-selling fiction audiobooks."),
    ("audio-nonfiction", "NYT Audio Nonfiction", 351, "The month's best-selling nonfiction audiobooks."),
    ("audio-childrens", "NYT Audio Children's", 352, "The month's best-selling children's audiobooks."),
    (
        "audio-advice-how-to-and-miscellaneous",
        "NYT Audio Advice, How-To & Misc.",
        353,
        "The month's best-selling advice and how-to audiobooks.",
    ),
)

NYT = [
    FeaturedList(
        key=f"nyt-{slug}",
        name=name,
        media_types=("book",),
        kind="chart",
        position=position,
        adapter="nyt_overview",
        params={"slug": slug},
        cadence=DAILY,
        source_name="The New York Times",
        source_url=f"https://www.nytimes.com/books/best-sellers/{slug}/",
        identity=("nyt", slug),
        description=description,
        tags=NYT_TAGS,
    )
    for slug, name, position, description in NYT_OVERVIEW_CHARTS
]

APPLE_BOOKS = [
    FeaturedList(
        key=key,
        name=name,
        media_types=("book",),
        kind="chart",
        position=position,
        adapter="apple_books",
        params={"feed": feed, "limit": 50, "country": "us"},
        cadence=DAILY,
        # Self-published free books often have no ISBN cover: a title search each,
        # on Hardcover and then Open Library. Later runs hit the resolve cache.
        guards=Guards(max_title_searches=120),
        soft_time_limit=900,  # Open Library search is limited to 20 requests a minute
        source_name="Apple Books",
        source_url=source_url,
        description=description,
    )
    for key, name, position, feed, source_url, description in (
        (
            "apple-books-top-paid",
            "Apple Books Top Paid",
            354,
            "books/top-paid",
            "https://books.apple.com/us/charts",
            "The 50 best-selling paid books on Apple Books in the US.",
        ),
        (
            "apple-books-top-free",
            "Apple Books Top Free",
            355,
            "books/top-free",
            "https://books.apple.com/us/charts",
            "The 50 most-downloaded free books on Apple Books in the US.",
        ),
        (
            "apple-books-top-audiobooks",
            "Apple Books Top Audiobooks",
            356,
            "audio-books/top",
            "https://books.apple.com/us/charts/audiobooks",
            "The 50 best-selling audiobooks on Apple Books in the US.",
        ),
    )
]

TRENDING = [
    FeaturedList(
        key="hardcover-trending",
        name="Trending on Hardcover",
        media_types=("book",),
        kind="chart",
        position=357,
        adapter="hardcover_trending",
        params={"duration": "month", "limit": 25},
        cadence=DAILY,
        source_name="Hardcover",
        source_url="https://hardcover.app/trending",
        description="The books Hardcover readers are adding most this month.",
    ),
    FeaturedList(
        key="openlibrary-trending-daily",
        name="Trending on Open Library Today",
        media_types=("book",),
        kind="chart",
        position=358,
        adapter="openlibrary_trending",
        params={"period": "daily", "limit": 50},
        cadence=DAILY,
        source_name="Open Library",
        source_url="https://openlibrary.org/trending/daily",
        description="The books Open Library readers are logging most today.",
    ),
    FeaturedList(
        key="openlibrary-trending-weekly",
        name="Trending on Open Library This Week",
        media_types=("book",),
        kind="chart",
        position=359,
        adapter="openlibrary_trending",
        params={"period": "weekly", "limit": 50},
        cadence=DAILY,
        source_name="Open Library",
        source_url="https://openlibrary.org/trending/weekly",
        description="The books Open Library readers are logging most this week.",
    ),
]

# Book clubs: every pick, newest first; a title + author search per pick, so the
# first run needs a bigger search budget (later runs hit the resolve cache).
BOOK_CLUB_GUARDS = Guards(max_title_searches=300)
BOOK_CLUBS = [
    FeaturedList(
        key="reeses-book-club",
        name="Reese's Book Club Picks",
        media_types=("book",),
        kind="curator",
        position=360,
        adapter="reeses_book_club",
        params={},
        cadence=WEEKLY,
        source_name="Reese's Book Club",
        source_url="https://reesesbookclub.com/our-picks/",
        description="Every Reese's Book Club pick, newest first.",
        is_ranked=False,
        guards=BOOK_CLUB_GUARDS,
        soft_time_limit=1200,
    ),
    FeaturedList(
        key="read-with-jenna",
        name="Read with Jenna Picks",
        media_types=("book",),
        kind="curator",
        position=361,
        adapter="read_with_jenna",
        params={},
        cadence=WEEKLY,
        source_name="TODAY",
        source_url="https://www.today.com/shop/read-jenna-book-club-list-today-s-jenna-bush-hager-t164652",
        description="Every Read with Jenna book club pick, newest first.",
        is_ranked=False,
        guards=BOOK_CLUB_GUARDS,
        soft_time_limit=1200,
    ),
]

MANGA = [
    FeaturedList(
        key="mal-top-manga",
        name="MyAnimeList Top Manga",
        media_types=("manga",),
        kind="community",
        position=460,
        adapter="mal_manga",
        params={"ranking_type": "manga", "limit": 50},
        cadence=DAILY,
        source_name="MyAnimeList",
        source_url="https://myanimelist.net/topmanga.php?type=manga",
        description="The highest-rated manga on MyAnimeList.",
    ),
    FeaturedList(
        key="mal-most-popular-manga",
        name="MyAnimeList Most Popular Manga",
        media_types=("manga",),
        kind="community",
        position=461,
        adapter="mal_manga",
        params={"ranking_type": "bypopularity", "limit": 50},
        cadence=DAILY,
        source_name="MyAnimeList",
        source_url="https://myanimelist.net/topmanga.php?type=bypopularity",
        description="The manga the most MyAnimeList members have on their lists.",
    ),
    FeaturedList(
        key="mangaupdates-top-rated",
        name="MangaUpdates Top Rated Manga",
        media_types=("manga",),
        kind="community",
        position=462,
        adapter="mangaupdates_top",
        params={"orderby": "rating", "type": ["Manga"], "limit": 50},
        cadence=DAILY,
        source_name="MangaUpdates",
        source_url="https://www.mangaupdates.com/series?orderby=rating&type=manga",
        description="The highest-rated manga on MangaUpdates.",
    ),
]

DEFINITIONS = [*NYT, *APPLE_BOOKS, *TRENDING, *BOOK_CLUBS, *MANGA]
