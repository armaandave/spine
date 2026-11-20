"""How Search names the places featured lists come from, and which it names first.

Each definition's ``source_name`` ("The Academy") shows as the name people know
("Oscars", from ``SOURCES``) with that source's icon, a file in
``static/img/sources/``. A source missing from ``SOURCES`` shows its own name and no
icon. Source names that show as one name ("Recording Academy", "The Recording
Academy") count as one source.

A media type's row on Search names its first sources: the ones in ``LEAD`` in that
order, then the rest by how many of the row's lists they made, then by name.
``problems()`` (run by the registry tests) catches names that match no list, icons
with no file and repeated names.
"""

from collections import Counter
from pathlib import Path

from django.conf import settings
from django.templatetags.static import static

ICON_FOLDER = "img/sources"

# source_name: (the name Search shows, "" to keep source_name; icon file stem, "" for none)
SOURCES = {
    "1001 Albums Generator": ("", "albumsgenerator"),
    "Academy of Interactive Arts & Sciences": ("D.I.C.E. Awards", "dice"),
    "American Film Institute": ("AFI", "afi"),
    "American Library Association": ("ALA", "ala"),
    "Apple Books": ("", "applebooks"),
    "Apple Music": ("", "applemusic"),
    "BAFTA": ("", "bafta"),
    "BBC": ("", "bbc"),
    "BBC Culture": ("", "bbc"),
    "BFI Sight and Sound": ("Sight and Sound", "bfi"),
    "Barack Obama": ("", "obama"),
    "Berlinale": ("", "berlinale"),
    "Billboard": ("", "billboard"),
    "Crunchyroll Anime Awards": ("Crunchyroll", "crunchyroll"),
    "Eisner Awards": ("", "eisner"),
    "Festival de Cannes": ("Cannes", "cannes"),
    "Game Developers Conference": ("GDC", "gdc"),
    "GamesRadar+": ("", "gamesradar"),
    "Golden Globes": ("", "goldenglobes"),
    "Golden Joystick Awards": ("Golden Joysticks", "gamesradar"),
    "Goodreads Choice Awards": ("Goodreads", "goodreads"),
    "Hardcover": ("", "hardcover"),
    "IGDB": ("", "igdb"),
    "IGN": ("", "ign"),
    "IMDb": ("", "imdb"),
    "JustWatch": ("", "justwatch"),
    "Kono Manga ga Sugoi!": ("", "konomanga"),
    "La Biennale di Venezia": ("Venice", "venice"),
    "Le Monde": ("", "lemonde"),
    "Letterboxd": ("", "letterboxd"),
    "Library of Congress": ("", "loc"),
    "ListenBrainz": ("", "listenbrainz"),
    "Manga Taishō": ("", "mangataisho"),
    "MangaUpdates": ("", "mangaupdates"),
    "Mercury Prize": ("", "mercury"),
    "Museum of Modern Art": ("MoMA", "moma"),
    "MyAnimeList": ("", "mal"),
    "National Book Foundation": ("National Book Awards", "nationalbook"),
    "Netflix": ("", "netflix"),
    "Nielsen": ("", "nielsen"),
    "Official Charts": ("", "officialcharts"),
    "Open Library": ("", "openlibrary"),
    "Pitchfork": ("", "pitchfork"),
    "Recording Academy": ("Grammys", "grammy"),
    "Reese's Book Club": ("", "reese"),
    "Rolling Stone": ("", "rollingstone"),
    "Sight and Sound": ("", "bfi"),
    "Steam": ("", "steam"),
    "TIME": ("", "time"),
    "TMDB": ("", "tmdb"),
    "TODAY": ("", "today"),
    "Television Academy": ("Emmys", "emmys"),
    "The Academy": ("Oscars", "oscars"),
    "The Booker Prizes": ("Booker", "booker"),
    "The Criterion Collection": ("Criterion", "criterion"),
    "The Game Awards": ("", "tga"),
    "The Guardian": ("", "guardian"),
    "The Hugo Awards": ("Hugo Awards", "hugo"),
    "The Needle Drop": ("", "needledrop"),
    "The New York Times": ("NYT", "nyt"),
    "The Nobel Prize": ("Nobel Prize", "nobel"),
    "The Pulitzer Prizes": ("Pulitzer", "pulitzer"),
    "The Recording Academy": ("Grammys", "grammy"),
    "The Strong National Museum of Play": ("The Strong", "strong"),
    "Trakt": ("", "trakt"),
    "Twitch": ("", "twitch"),
    "Women's Prize": ("", "womensprize"),
    "Writers Guild of America": ("WGA", "wga"),
}

# Shown names that lead a row, best known first.
LEAD = (
    "Oscars",
    "Cannes",
    "Emmys",
    "Grammys",
    "NYT",
    "Pulitzer",
    "The Game Awards",
    "Kono Manga ga Sugoi!",
    "Manga Taishō",
    "MyAnimeList",
    "Crunchyroll",
    "Eisner Awards",
    "Netflix",
    "Billboard",
    "Steam",
    "TIME",
    "Golden Globes",
    "BAFTA",
    "Booker",
    "Nobel Prize",
    "Letterboxd",
    "IMDb",
    "Criterion",
    "Sight and Sound",
    "Venice",
    "Berlinale",
    "Rolling Stone",
    "Pitchfork",
    "Apple Music",
    "Goodreads",
    "IGN",
)


def shown(source_name):
    """Return ``(name, icon file stem)`` for a definition's ``source_name``."""
    name, icon = SOURCES.get(source_name, ("", ""))
    return name or source_name, icon


def shelf_sources(definitions, request):
    """Return ``[{"name", "icon_url", "lists"}]`` for a shelf's lists, best known first.

    ``lists`` is how many of these lists the source made; lists without a source (and
    manually featured lists, which have no definition) are left out.
    """
    counts = Counter()
    icons = {}
    for definition in definitions:
        if definition is None or not definition.source_name:
            continue
        name, icon = shown(definition.source_name)
        counts[name] += 1
        icons[name] = icons.get(name) or icon
    lead = {name: index for index, name in enumerate(LEAD)}
    ordered = sorted(counts, key=lambda name: (lead.get(name, len(lead)), -counts[name], name))
    return [
        {
            "name": name,
            "icon_url": request.build_absolute_uri(static(f"{ICON_FOLDER}/{icons[name]}.png")) if icons[name] else None,
            "lists": counts[name],
        }
        for name in ordered
    ]


def problems(definitions):
    """Return every problem with ``SOURCES`` and ``LEAD`` against ``definitions``."""
    used = {definition.source_name for definition in definitions if definition.source_name}
    names = {shown(source_name)[0] for source_name in used}
    folder = Path(settings.STATICFILES_DIRS[0]) / ICON_FOLDER
    found = []
    for source_name, (_, icon) in SOURCES.items():
        if source_name not in used:
            found.append(f"SOURCES: no list comes from {source_name!r}")
        if icon and not (folder / f"{icon}.png").is_file():
            found.append(f"SOURCES[{source_name!r}]: no icon file {icon}.png")
    if len(set(LEAD)) != len(LEAD):
        found.append("LEAD: repeated names")
    found.extend(f"LEAD: no source shows as {name!r}" for name in LEAD if name not in names)
    return found
