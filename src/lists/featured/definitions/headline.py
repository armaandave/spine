"""The six lists live before the featured-lists engine; installed builds show them.

Their identities and positions are stored in production and must never change.
"""

from datetime import timedelta

from app.providers import nytbooks
from lists.featured.registry import FeaturedList, Guards

LETTERBOXD_TOP_500 = FeaturedList(
    key="letterboxd-top-500",
    name="Letterboxd's Top 500 Films",
    media_types=("movie",),
    kind="community",
    position=1,
    adapter="mdblist",
    params={"external": "155837"},
    cadence=timedelta(days=1),
    source_name="Letterboxd",
    source_url="https://letterboxd.com/official/list/letterboxds-top-500-films/",
    identity=("mdblist", "155837"),
    description="The 500 highest-rated narrative feature films on Letterboxd.",
    headline=True,
    guards=Guards(require_exact_count=True, min_resolve_rate=0.95),
)

NYT_TAGS = ("NYT Best Sellers", "Books")
NYT_MONTHLY = {"business-books", "graphic-books-and-manga"}  # NYT moved these to monthly

NYT_CHARTS = [
    FeaturedList(
        key=f"nyt-{chart['slug']}",
        name=chart["display_name"],
        media_types=("book",),
        kind="chart",
        position=chart["featured_position"],
        adapter="nyt",
        params={
            "slug": chart["slug"],
            "fallback_name": chart["fallback_name"],
            **({"monthly": True} if chart["slug"] in NYT_MONTHLY else {}),
            "min_entries": 10,  # NYT charts list 10 or 15 books
        },
        cadence=timedelta(days=1),
        source_name="The New York Times",
        source_url=f"https://www.nytimes.com/books/best-sellers/{chart['slug']}/",
        identity=("nyt", chart["slug"]),
        description="The current New York Times Best Sellers list.",
        headline=True,
        tags=NYT_TAGS,
    )
    for chart in nytbooks.FEATURED_CHARTS
]

DEFINITIONS = [LETTERBOXD_TOP_500, *NYT_CHARTS]
