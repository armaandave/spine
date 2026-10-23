"""This season's award nights, read live from Wikipedia (``award_page``).

Each list starts as the nominees in ``static/nominees/<key>.csv`` and shows winners
about a minute after Wikipedia names them. Windows are built from each ceremony's
local time and run from about an hour before it to a few hours after; between
windows the lists are re-read every few hours, which catches late page edits.
Ceremonies whose nominees aren't announced yet have empty files and
``featured=False``; ``docs/featured-lists/LIVE_AWARDS.md`` says how to fill them in.
"""

from datetime import timedelta

from lists.featured.registry import FeaturedList, Guards, live_window

CADENCE = timedelta(hours=3)
GUARDS = Guards(max_title_searches=0)  # nominees are pre-matched; never search by title
INTERVAL = timedelta(seconds=30)


def award(key, name, media_types, position, *, pages, window, source_name, source_url, description, **params):
    """Return one ceremony's definition; ``window`` is ``(local start, zone, duration)``."""
    start, zone, duration = window
    featured = params.pop("featured", True)
    return FeaturedList(
        key=key,
        name=name,
        media_types=media_types,
        kind="award",
        position=position,
        adapter="award_page",
        params={"pages": pages, **params},
        cadence=CADENCE,
        source_name=source_name,
        source_url=source_url,
        description=description,
        is_ranked=False,
        featured=featured,
        live_windows=(live_window(start, zone, duration, interval=INTERVAL, label=name),),
        guards=GUARDS,
        soft_time_limit=300,
    )


DEFINITIONS = [
    # Winner revealed about 21:45-22:00 GMT (livestream from 21:30 GMT in 2025).
    award(
        "booker-prize-2026",
        "The Booker Prize 2026",
        ("book",),
        390,
        pages=["2026 Booker Prize"],
        window=("2026-11-09 19:30", "Europe/London", timedelta(hours=5)),
        aliases={"The Booker Prize": ["Nominees", "Shortlist", "Booker Prize"]},
        edition="2026",
        source_name="The Booker Prizes",
        source_url="https://thebookerprizes.com/the-booker-library/prize-years/2026",
        description="The six novels on the 2026 Booker Prize shortlist. The winner rises to the top "
        "minutes after it's announced at Old Billingsgate, London, on 9 November.",
    ),
    # Ceremony and livestream at 8pm EST at Cipriani Wall Street.
    award(
        "national-book-awards-2026",
        "National Book Awards 2026",
        ("book",),
        391,
        pages=["2026 National Book Awards"],
        window=("2026-11-18 19:00", "America/New_York", timedelta(hours=6)),
        edition="2026",
        source_name="National Book Foundation",
        source_url="https://www.nationalbook.org/awards2026/",
        description="The 25 finalists in fiction, nonfiction, poetry, translated literature and young "
        "people's literature. Winners rise to the top as Wikipedia names them after the 18 November ceremony.",
    ),
    # Troxy, London: the show starts at 5:30pm GMT.
    award(
        "golden-joystick-awards-2026",
        "Golden Joystick Awards 2026",
        ("game",),
        590,
        pages=[
            {"title": "Golden Joystick Awards", "section": "2026"},
            "2026 Golden Joystick Awards",
            "Golden Joystick Awards 2026",
        ],
        window=("2026-11-11 16:30", "Europe/London", timedelta(hours=6)),
        aliases={
            "Still Playing - Mobile": ["Still Playing Award (Mobile)", "Still Playing Award - Mobile"],
            "Still Playing - PC & Console": ["Still Playing Award (PC & Console)", "Still Playing Award - PC & Console"],
            "Best Indie Game - Self-published": ["Best Indie Game - Self Published"],
        },
        titles={
            "GTA Online": ["Grand Theft Auto Online"],
            "HITMAN World of Assassination": ["Hitman 3"],
        },
        edition="2026",
        source_name="GamesRadar+",
        source_url="https://www.gamesradar.com/goldenjoystickawards/",
        description="Every game nominated for the 2026 Golden Joystick Awards, voted by players. "
        "Winners rise to the top during the show on 11 November.",
    ),
    # Nominees come out in mid-November; the Opening Act pre-show (with awards) starts at 4:30pm PT.
    award(
        "the-game-awards-2026",
        "The Game Awards 2026",
        ("game",),
        591,
        pages=["The Game Awards 2026"],
        window=("2026-12-10 15:30", "America/Los_Angeles", timedelta(hours=6, minutes=30)),
        edition="2026",
        featured=False,
        source_name="The Game Awards",
        source_url="https://thegameawards.com/",
        description="Every nominee at The Game Awards 2026. Winners rise to the top during the "
        "show on 10 December.",
    ),
    # Nominations 2026-12-07; the show is at 5pm PST on CBS.
    award(
        "golden-globes-2027-film",
        "Golden Globes 2027: Film",
        ("movie",),
        190,
        pages=["84th Golden Globes", "84th Golden Globe Awards"],
        window=("2027-01-10 16:00", "America/Los_Angeles", timedelta(hours=6)),
        edition="2027",
        featured=False,
        source_name="Golden Globes",
        source_url="https://goldenglobes.com/",
        description="The film nominees at the 84th Golden Globes. Winners rise to the top during the "
        "show on 10 January.",
    ),
    award(
        "golden-globes-2027-tv",
        "Golden Globes 2027: Television",
        ("tv",),
        290,
        pages=["84th Golden Globes", "84th Golden Globe Awards"],
        window=("2027-01-10 16:00", "America/Los_Angeles", timedelta(hours=6)),
        edition="2027",
        featured=False,
        source_name="Golden Globes",
        source_url="https://goldenglobes.com/",
        description="The television nominees at the 84th Golden Globes. Winners rise to the top during "
        "the show on 10 January.",
    ),
    # Nominations 2026-11-16. Most album awards go out at the Premiere Ceremony (12:30pm PT in 2026),
    # the rest on the telecast (5pm PT in 2026); 2027's times aren't published yet.
    award(
        "grammy-awards-2027",
        "Grammy Awards 2027",
        ("music",),
        790,
        pages=["69th Annual Grammy Awards"],
        window=("2027-02-07 11:30", "America/Los_Angeles", timedelta(hours=11)),
        edition="2027",
        featured=False,
        source_name="Recording Academy",
        source_url="https://www.grammy.com/",
        description="The album nominees at the 69th Grammy Awards. Winners rise to the top as they're "
        "announced on 7 February.",
    ),
    # Nominations 2027-01-21. 4pm PT on 14 March is PDT: daylight saving starts that morning.
    award(
        "oscars-2027",
        "The Oscars 2027",
        ("movie",),
        191,
        pages=["99th Academy Awards"],
        window=("2027-03-14 15:00", "America/Los_Angeles", timedelta(hours=7)),
        edition="2027",
        featured=False,
        source_name="The Academy",
        source_url="https://www.oscars.org/",
        description="Every film nominated at the 99th Academy Awards. Winners rise to the top during "
        "the ceremony on 14 March.",
    ),
]
