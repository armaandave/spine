"""Awards and curated lists read from Wikipedia tables (adapter ``wikipedia_table``).

Awards are unranked, newest first, each entry noted with its year(s). Wikipedia is the
source for these because the official sites block scripts or have no history, and
because terms forbid reading some originals directly (Goodreads, Billboard).
"""

from datetime import timedelta

from lists.featured.registry import FeaturedList, Guards

YEARLY = timedelta(days=33)  # reads as "yearly"; a new winner shows up within a month
MONTHLY = timedelta(days=30)
WEEKLY = timedelta(days=7)


def wikipedia_url(title, site="en"):
    """Return the article URL for a page title."""
    return f"https://{site}.wikipedia.org/wiki/{title.replace(' ', '_')}"


def award(key, name, media_type, position, page, *, source_name, description, columns, **options):
    """Return an all-time award winners list read from one Wikipedia page."""
    guards = options.pop("guards", Guards(min_resolve_rate=0.7, max_title_searches=120))
    winners = options.pop("winners", "only")
    return FeaturedList(
        key=key,
        name=name,
        media_types=(media_type,),
        kind="award",
        position=position,
        adapter="wikipedia_table",
        params={"pages": [page], "columns": columns, "winners": winners, **options},
        cadence=YEARLY,
        source_name=source_name,
        source_url=wikipedia_url(page),
        description=description,
        is_ranked=False,
        guards=guards,
    )


# TV ---------------------------------------------------------------------------------

EMMY_COLUMNS = {"year": "Year", "title": "Program"}
EMMYS = [
    award(
        "emmys-outstanding-drama-series",
        "Emmy Award for Outstanding Drama Series",
        "tv",
        230,
        "Primetime Emmy Award for Outstanding Drama Series",
        source_name="Television Academy",
        description="Every series that won the Primetime Emmy for Outstanding Drama Series, newest first.",
        columns=EMMY_COLUMNS,
        strip_parenthetical=True,
        match_year=False,
        min_entries=60,
    ),
    award(
        "emmys-outstanding-comedy-series",
        "Emmy Award for Outstanding Comedy Series",
        "tv",
        231,
        "Primetime Emmy Award for Outstanding Comedy Series",
        source_name="Television Academy",
        description="Every series that won the Primetime Emmy for Outstanding Comedy Series, newest first.",
        columns=EMMY_COLUMNS,
        strip_parenthetical=True,
        match_year=False,
        min_entries=60,
    ),
    award(
        "emmys-outstanding-limited-series",
        "Emmy Award for Outstanding Limited or Anthology Series",
        "tv",
        232,
        "Primetime Emmy Award for Outstanding Limited or Anthology Series",
        source_name="Television Academy",
        description=(
            "Every winner of the Primetime Emmy for Outstanding Limited or Anthology Series "
            "(earlier Outstanding Miniseries), newest first."
        ),
        columns=EMMY_COLUMNS,
        strip_parenthetical=True,
        match_year=False,
        min_entries=40,
    ),
]

# Games ------------------------------------------------------------------------------

GAME_COLUMNS = {"year": "Year", "title": "Game", "creator": r"Developer(\(s\))?"}
GAMES = [
    award(
        "golden-joystick-game-of-the-year",
        "Golden Joystick Award for Ultimate Game of the Year",
        "game",
        530,
        "Golden Joystick Award for Game of the Year",
        source_name="Golden Joystick Awards",
        description="Every Golden Joystick Game of the Year, now Ultimate Game of the Year, newest first.",
        columns=GAME_COLUMNS,
        winners="all",
        strip_parenthetical=True,  # "Kick Off 2 (16-bit computers)"
        min_entries=30,
    ),
    award(
        "dice-game-of-the-year",
        "D.I.C.E. Award for Game of the Year",
        "game",
        531,
        "D.I.C.E. Award for Game of the Year",
        source_name="Academy of Interactive Arts & Sciences",
        description="Every D.I.C.E. Game of the Year, newest first.",
        columns=GAME_COLUMNS,
        min_entries=25,
    ),
    award(
        "bafta-games-best-game",
        "BAFTA Games Award for Best Game",
        "game",
        532,
        "British Academy Games Award for Best Game",
        source_name="BAFTA",
        description="Every BAFTA Games Award for Best Game, newest first.",
        columns=GAME_COLUMNS,
        min_entries=20,
    ),
    award(
        "gdc-game-of-the-year",
        "Game Developers Choice Award for Game of the Year",
        "game",
        533,
        "Game Developers Choice Award for Game of the Year",
        source_name="Game Developers Conference",
        description="Every Game Developers Choice Game of the Year, newest first.",
        columns=GAME_COLUMNS,
        min_entries=20,
    ),
]

# Anime ------------------------------------------------------------------------------

ANIME = [
    award(
        "crunchyroll-anime-of-the-year",
        "Crunchyroll Anime Awards: Anime of the Year",
        "anime",
        630,
        "Crunchyroll Anime Award for Anime of the Year",
        source_name="Crunchyroll Anime Awards",
        description="Every Crunchyroll Anime Awards Anime of the Year, newest first.",
        columns={"year": "Year", "title": "Anime", "creator": r"Studio(\(s\))?"},
        min_entries=8,
        guards=Guards(min_resolve_rate=0.7),
    ),
]

# Comics -----------------------------------------------------------------------------

EISNER_COLUMNS = {"year": "Year", "title": "Title"}
COMIC_GUARDS = Guards(min_resolve_rate=0.6, max_title_searches=40)  # Comic Vine allows ~190 calls an hour
COMICS = [
    award(
        "eisner-best-graphic-album-new",
        "Eisner Award for Best Graphic Album: New",
        "comic",
        430,
        "Eisner Award for Best Graphic Album — New",
        source_name="Eisner Awards",
        description="Every Eisner Award for Best Graphic Album (New), newest first.",
        columns=EISNER_COLUMNS,
        strip_parenthetical=True,
        min_entries=25,
        guards=COMIC_GUARDS,
    ),
    award(
        "eisner-best-continuing-series",
        "Eisner Award for Best Continuing Series",
        "comic",
        431,
        "Eisner Award for Best Continuing Series",
        source_name="Eisner Awards",
        description="Every Eisner Award for Best Continuing Series, newest first.",
        columns=EISNER_COLUMNS,
        strip_parenthetical=True,
        match_year=False,
        min_entries=30,
        guards=COMIC_GUARDS,
    ),
]

# Manga ------------------------------------------------------------------------------

KONO_MANGA_JA = "このマンガがすごい!"


def kono_manga_top_10(key, name, position, heading, edition_label):
    """Return the latest Kono Manga ga Sugoi! top 10 from Japanese Wikipedia."""
    return FeaturedList(
        key=key,
        name=name,
        media_types=("manga",),
        kind="award",
        position=position,
        adapter="wikipedia_table",
        params={
            "site": "ja",
            "pages": [KONO_MANGA_JA],
            "tables": {"heading": f"^{heading}$"},
            "columns": {"rank": "順位", "title": "タイトル", "creator": "作者"},
            "latest_only": True,
            "order": "source",
            "match_year": False,
            "min_entries": 100,
            "mangaupdates_search": True,
        },
        cadence=YEARLY,
        source_name="Kono Manga ga Sugoi!",
        source_url=wikipedia_url(KONO_MANGA_JA, "ja"),
        description=f"The latest Kono Manga ga Sugoi! {edition_label} top 10, chosen by manga critics and staff.",
        guards=Guards(min_resolve_rate=0.6),
    )


MANGA = [
    kono_manga_top_10(
        "kono-manga-ga-sugoi-mens-top-10",
        "Kono Manga ga Sugoi! Top 10: Men's",
        480,
        "オトコ版",
        "men's (otoko) edition",
    ),
    kono_manga_top_10(
        "kono-manga-ga-sugoi-womens-top-10",
        "Kono Manga ga Sugoi! Top 10: Women's",
        481,
        "オンナ版",
        "women's (onna) edition",
    ),
    award(
        "kono-manga-ga-sugoi-number-ones",
        "Kono Manga ga Sugoi! Number Ones",
        "manga",
        482,
        "Kono Manga ga Sugoi!",
        source_name="Kono Manga ga Sugoi!",
        description="Every manga ranked first in Kono Manga ga Sugoi!'s men's and women's editions, newest first.",
        columns={"year": "Year"},
        titles=[{"header": r"Men ver\.", "label": "Men's"}, {"header": r"Women ver\.", "label": "Women's"}],
        winners="all",
        split_creator=" by ",
        match_year=False,
        min_entries=30,
        mangaupdates_search=True,
    ),
    award(
        "manga-taisho",
        "Manga Taishō Winners",
        "manga",
        483,
        "Manga Taishō",
        source_name="Manga Taishō",
        description="Every winner of the Manga Taishō, the booksellers' manga award, newest first.",
        columns={"year": "Award year", "title": "Title", "creator": r"Author(\(s\))?"},
        match_year=False,
        min_entries=15,
        mangaupdates_search=True,
    ),
]

# Books ------------------------------------------------------------------------------

NBA_COLUMNS = {"year": "Year", "title": r"Title(\(s\))?", "creator": "Author", "result": "Result"}
NBA_TRANSLATED_COLUMNS = {"title": "Title", "creator": "Author", "result": "Result"}
BOOK_GUARDS = Guards(min_resolve_rate=0.7, max_title_searches=120)


def national_book_award(  # noqa: PLR0913
    key,
    category,
    position,
    *,
    columns=NBA_COLUMNS,
    description=None,
    min_entries=30,
    **options,
):
    """Return one National Book Award category's winners."""
    page = f"National Book Award for {category}"
    return award(
        key,
        page,
        "book",
        position,
        page,
        source_name="National Book Foundation",
        description=description or f"Every winner of the National Book Award for {category}, newest first.",
        columns=columns,
        min_entries=min_entries,
        guards=BOOK_GUARDS,
        main_title=True,
        **options,
    )


NATIONAL_BOOK_AWARDS = [
    national_book_award("national-book-award-fiction", "Fiction", 370),
    national_book_award(
        "national-book-award-nonfiction",
        "Nonfiction",
        371,
        # The single Nonfiction award (1950-1963, 1984 on). The page also tabulates the
        # 1935-1940 awards and the 1964-1983 category awards (Arts and Letters, History,
        # the Sciences…), which were separate prizes.
        tables={"caption": r"Nonfiction winners and finalists|nonfiction books, 19[56]0.1963"},
        description="Every winner of the National Book Award for Nonfiction (1950-1963 and since 1984), newest first.",
    ),
    national_book_award(
        "national-book-award-poetry",
        "Poetry",
        372,
        strip_parenthetical=True,  # "The Far Field (posth.)", "New and Selected Poems (vol. 1 of two)"
    ),
    national_book_award(
        "national-book-award-translated-literature",
        "Translated Literature",
        373,
        columns=NBA_TRANSLATED_COLUMNS,
        min_entries=5,
    ),
    national_book_award(
        "national-book-award-young-peoples-literature",
        "Young People's Literature",
        374,
        description=(
            "Every winner of the National Book Award for Young People's Literature, newest first, "
            "with the children's book awards it grew out of."
        ),
    ),
]

OPRAH = FeaturedList(
    key="oprahs-book-club",
    name="Oprah's Book Club",
    media_types=("book",),
    kind="curator",
    position=375,
    adapter="wikipedia_table",
    params={
        "pages": ["Oprah's Book Club", "Oprah's Book Club 2.0"],
        "columns": {"year": "Date", "date": "Date", "title": "Title", "creator": "Author"},
        "min_entries": 100,
        "main_title": True,
    },
    cadence=MONTHLY,
    source_name="Oprah's Book Club",
    source_url=wikipedia_url("Oprah's Book Club 2.0"),
    description="Every Oprah's Book Club selection since 1996, newest first.",
    is_ranked=False,
    guards=Guards(min_resolve_rate=0.7, max_title_searches=150),
)

GOODREADS_PAGE = "Goodreads Choice Awards"
GOODREADS_CATEGORIES = (
    ("fiction", "Fiction", r"^Fiction$", 376),
    ("mystery-thriller", "Mystery & Thriller", r"^Mystery (&|and) Thriller$", 377),
    ("historical-fiction", "Historical Fiction", r"^Historical Fiction$", 378),
    ("fantasy", "Fantasy", r"^Fantasy$", 379),
    ("romance", "Romance", r"^Romance$", 380),
    ("science-fiction", "Science Fiction", r"^Science Fiction$", 381),
    ("horror", "Horror", r"^Horror$", 382),
    ("nonfiction", "Nonfiction", r"^Nonfiction$", 383),
    ("young-adult-fiction", "Young Adult Fiction", r"^Young Adult Fiction$", 384),
)
GOODREADS = [
    award(
        f"goodreads-choice-{slug}",
        f"Goodreads Choice Awards: {label}",
        "book",
        position,
        GOODREADS_PAGE,
        source_name="Goodreads Choice Awards",
        description=f"Every Goodreads Choice Award winner for {label}, voted by readers, newest first.",
        columns={"year": "Year", "category": "Category", "title": "Title", "creator": "Author"},
        category=pattern,
        winners="all",
        main_title=True,
        min_entries=10,
        guards=BOOK_GUARDS,
    )
    for slug, label, pattern, position in GOODREADS_CATEGORIES
]

# Music ------------------------------------------------------------------------------

BILLBOARD_PAGE = "List of Billboard 200 number-one albums of {year}"
BILLBOARD = FeaturedList(
    key="billboard-200-number-one-albums",
    name="Billboard 200 Number-One Albums This Year",
    media_types=("music",),
    kind="chart",
    position=750,
    adapter="wikipedia_table",
    params={
        "pages": [BILLBOARD_PAGE],
        "fallback_previous_year": True,
        "musicbrainz_search": True,
        "tables": {"heading": "Chart history"},
        "columns": {"date": "Issue date", "title": "Album", "creator": r"Artist(\(s\))?"},
    },
    cadence=WEEKLY,
    source_name="Billboard",
    source_url="https://en.wikipedia.org/wiki/List_of_Billboard_200_number-one_albums",
    description="This year's number-one albums on the Billboard 200, the latest first.",
    is_ranked=False,
    guards=Guards(min_resolve_rate=0.6, drop_check_min_previous=1000),  # resets every January
)

DEFINITIONS = [
    *EMMYS,
    *NATIONAL_BOOK_AWARDS,
    OPRAH,
    *GOODREADS,
    *COMICS,
    *MANGA,
    *GAMES,
    *ANIME,
    BILLBOARD,
]
