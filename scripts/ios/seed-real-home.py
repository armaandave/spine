#!/usr/bin/env python3
"""Fill the test backend with real titles and believable friend activity for Home.

    python3 scripts/ios/seed-real-home.py [BASE_URL]      (default http://127.0.0.1:8010)

Talks to the backend only over its HTTP API, as the app does, so it works against any
checkout the test backend serves. It refuses port 8000 (production) and any host that
is not this Mac. Standard library only.

It makes five friends (maya, jules, theo, noor, sam; a short suffix when a name is
taken), follows between them and the dev user `claude`, six days of their logs, daily
progress, starts, a paused book, a stopped game, a day of Planning adds, likes, comments
and replies, and fills `claude`'s own In progress and Trending. Titles are real ones
from the catalog (TMDB, MAL, IGDB, Hardcover, Apple Music), so posters are real.

Safe to run again: every step looks first and skips what is already there (a log on
the same title and day, a title already tracked, a comment with the same words). A step
that fails is reported at the end and does not stop the others.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_BASE = "http://127.0.0.1:8010"
PASSWORD = "spine-test-pass-1"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
# The app sends the phone's zone; the server dates ticks and saves in it.
TIMEZONE = os.environ.get("SPINE_SEED_TZ", "America/New_York")
MUSIC_PAUSE = 3.0  # MusicBrainz is 1 request/s per IP, shared with production

# Real titles, by the provider's own ids (looked up once through /media/search/).
T = {
    # movies (TMDB)
    "sinners": ("tmdb", "movie", "1233413", "Sinners"),
    "battle": ("tmdb", "movie", "1054867", "One Battle After Another"),
    "weapons": ("tmdb", "movie", "1078605", "Weapons"),
    "pastlives": ("tmdb", "movie", "666277", "Past Lives"),
    "dune2": ("tmdb", "movie", "693134", "Dune: Part Two"),
    "anora": ("tmdb", "movie", "1064213", "Anora"),
    "challengers": ("tmdb", "movie", "937287", "Challengers"),
    "brutalist": ("tmdb", "movie", "549509", "The Brutalist"),
    "frankenstein": ("tmdb", "movie", "1062722", "Frankenstein"),
    "marty": ("tmdb", "movie", "1317288", "Marty Supreme"),
    "bugonia": ("tmdb", "movie", "701387", "Bugonia"),
    "hamnet": ("tmdb", "movie", "858024", "Hamnet"),
    "perfectdays": ("tmdb", "movie", "976893", "Perfect Days"),
    # TV (TMDB)
    "severance": ("tmdb", "tv", "95396", "Severance"),
    "bear": ("tmdb", "tv", "136315", "The Bear"),
    "shogun": ("tmdb", "tv", "126308", "Shogun"),
    "pluribus": ("tmdb", "tv", "225171", "Pluribus"),
    "pitt": ("tmdb", "tv", "250307", "The Pitt"),
    "andor": ("tmdb", "tv", "83867", "Andor"),
    # anime and manga (MAL)
    "onepiece": ("mal", "anime", "21", "One Piece"),
    "frieren": ("mal", "anime", "52991", "Frieren: Beyond Journey's End"),
    "dandadan": ("mal", "anime", "57334", "Dan Da Dan"),
    "chainsawman": ("mal", "manga", "116778", "Chainsaw Man"),
    # books (Hardcover)
    "intermezzo": ("hardcover", "book", "1214974", "Intermezzo"),
    "hailmary": ("hardcover", "book", "427578", "Project Hail Mary"),
    "james": ("hardcover", "book", "1010023", "James"),
    "tomorrow": ("hardcover", "book", "479910", "Tomorrow, and Tomorrow, and Tomorrow"),
    # games (IGDB)
    "eldenring": ("igdb", "game", "119133", "Elden Ring"),
    "hades2": ("igdb", "game", "228525", "Hades II"),
    "clairobscur": ("igdb", "game", "305152", "Clair Obscur: Expedition 33"),
    "silksong": ("igdb", "game", "115289", "Hollow Knight: Silksong"),
    # albums (Apple Music)
    "sos": ("apple_music", "music", "1657869377", "SOS"),
    "gnx": ("apple_music", "music", "1781270319", "GNX"),
    "hmhas": ("apple_music", "music", "1739659134", "Hit Me Hard and Soft"),
}

FRIENDS = [
    ("maya", "Maya Chen"),
    ("jules", "Jules Moreau"),
    ("theo", "Theo Alvarez"),
    ("noor", "Noor Rahman"),
    ("sam", "Sam Okafor"),
]
# Who follows whom among the friends, besides everyone following `claude` and back.
FRIEND_FOLLOWS = [
    ("maya", "jules"), ("maya", "noor"), ("jules", "maya"), ("jules", "sam"),
    ("theo", "sam"), ("theo", "noor"), ("noor", "maya"), ("noor", "theo"),
    ("sam", "jules"), ("sam", "theo"), ("sam", "maya"),
]


def refuse_unsafe(base):
    parts = urllib.parse.urlparse(base)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        sys.exit("Give a URL like http://127.0.0.1:8010")
    if parts.hostname not in LOCAL_HOSTS and parts.hostname.strip("[]") not in LOCAL_HOSTS:
        sys.exit("Refusing %s: this seeds a local test backend only." % parts.hostname)
    if parts.port in (None, 80, 443, 8000):
        sys.exit("Refusing port %s: 8000 is production, and a test backend has its own port." % parts.port)


class Failures:
    def __init__(self):
        self.items = []
        self.created = {}

    def fail(self, who, what, detail):
        text = detail if isinstance(detail, str) else json.dumps(detail, default=str)
        self.items.append("%s: %s -> %s" % (who, what, text[:220]))
        print("  FAIL %s: %s -> %s" % (who, what, text[:160]))

    def made(self, what, count=1):
        self.created[what] = self.created.get(what, 0) + count


F = Failures()


def mid(*parts):
    """A stable mutation id, so a repeated write is the same change."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "spine-seed-real-home/" + "/".join(str(p) for p in parts)))


class Api:
    def __init__(self, base):
        self.base = base.rstrip("/") + "/api/v1"

    def raw(self, method, path, body=None, token=None):
        headers = {
            "Content-Type": "application/json",
            "X-Spine-Rating-Scale": "stars",
            "X-Spine-Timezone": TIMEZONE,
        }
        if token:
            headers["Authorization"] = "Bearer " + token
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(6):
            request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    text = response.read()
                    return response.status, (json.loads(text) if text else None)
            except urllib.error.HTTPError as error:
                text = error.read()
                try:
                    payload = json.loads(text) if text else None
                except ValueError:
                    payload = text[:200].decode("utf-8", "replace")
                if error.code == 429:
                    wait = int(error.headers.get("Retry-After") or 15)
                    print("  (throttled, waiting %ss)" % min(wait, 60))
                    time.sleep(min(wait, 60) + 1)
                    continue
                if error.code >= 502 and attempt < 3:
                    time.sleep(3)
                    continue
                return error.code, payload
            except (urllib.error.URLError, TimeoutError) as error:
                if attempt < 3:
                    time.sleep(3)
                    continue
                return 0, str(error)
        return 429, "still throttled"


class Actor:
    """One signed-in account."""

    def __init__(self, api, username, password=None, dev=False):
        self.api = api
        self.username = username
        self.password = password
        self.dev = dev
        self.token = None
        self.diary_keys = None
        self.sign_in()

    def sign_in(self):
        if self.dev:
            status, data = self.api.raw("POST", "/auth/dev-sign-in/", {})
        else:
            status, data = self.api.raw(
                "POST", "/auth/login/", {"username_or_email": self.username, "password": self.password}
            )
        if status != 200:
            raise RuntimeError("sign-in %s failed: %s %s" % (self.username, status, data))
        self.token = data["access"]
        self.user = data.get("user") or {}

    def call(self, method, path, body=None):
        status, data = self.api.raw(method, path, body, self.token)
        if status == 401:
            self.sign_in()
            status, data = self.api.raw(method, path, body, self.token)
        return status, data

    def step(self, what, method, path, body=None, ok=(200, 201, 204)):
        status, data = self.call(method, path, body)
        if status not in ok:
            F.fail(self.username, what, {"status": status, "body": data})
            return None
        return data if data is not None else {}


def ensure_friend(api, base_name, display):
    """Sign in to the friend if it exists with our password; register it if not."""
    for suffix in ("", "_s", "_s2", "_s3"):
        name = base_name + suffix
        try:
            actor = Actor(api, name, PASSWORD)
        except RuntimeError:
            actor = None
        if actor:
            created = False
        else:
            status, data = api.raw(
                "POST",
                "/auth/register/",
                {"username": name, "email": name + "@example.com", "password": PASSWORD, "password_confirm": PASSWORD},
            )
            if status != 201:
                print("  %s unavailable (%s), trying another name" % (name, status))
                continue
            actor = Actor(api, name, PASSWORD)
            created = True
            F.made("accounts")
        if (actor.user.get("display_name") or "") != display:
            actor.step("display name", "PATCH", "/me/", {"display_name": display})
        print("  %s %s (%s)" % ("created" if created else "found", name, display))
        return actor
    raise RuntimeError("no free username for " + base_name)


# ---------------------------------------------------------------------------------
# Dates. "Today" is the phone's day in TIMEZONE, as the server reads it.
# ---------------------------------------------------------------------------------

TODAY = datetime.now(ZoneInfo(TIMEZONE)).date()


def day(n):
    """The day n days before today, as YYYY-MM-DD."""
    return (TODAY - timedelta(days=n)).isoformat()


# ---------------------------------------------------------------------------------
# Building blocks. Each skips what is already there.
# ---------------------------------------------------------------------------------


def ref(key, season=None, episode=None):
    source, media_type, media_id, _ = T[key]
    out = {"source": source, "media_type": media_type, "media_id": media_id}
    if season is not None:
        out["season_number"] = season
    if episode is not None:
        out["episode_number"] = episode
    return out


def track_path(key, tail=""):
    source, media_type, media_id, _ = T[key]
    return "/tracking/%s/%s/%s/%s" % (source, media_type, media_id, tail)


def is_tracked(actor, key):
    status, data = actor.call("GET", track_path(key))
    return status == 200 and isinstance(data, dict) and bool(data.get("status") or data.get("tracking"))


def load_diary(actor):
    """Index the actor's diary by (type, id, season, episode, day), once."""
    if actor.diary_keys is not None:
        return
    keys = set()
    page = 1
    while True:
        status, data = actor.call("GET", "/diary/?page_size=100&page=%d" % page)
        if status != 200 or not data:
            break
        for entry in data["results"]:
            r = entry["media"]["ref"]
            keys.add((r["media_type"], str(r["media_id"]), r.get("season_number"), r.get("episode_number"), entry["consumed_at"][:10]))
        if not data.get("next"):
            break
        page += 1
    actor.diary_keys = keys


def log(actor, key, days_ago, rating=None, liked=False, rewatch=False, review="", spoilers=False,
        season=None, episode=None, media_type=None, favorite=None):
    """A diary log on a past day."""
    load_diary(actor)
    r = ref(key, season, episode)
    if media_type:
        r["media_type"] = media_type
    stamp = (r["media_type"], r["media_id"], season, episode, day(days_ago))
    if stamp in actor.diary_keys:
        return None
    body = {"ref": r, "consumed_at": day(days_ago), "liked": liked, "is_rewatch": rewatch,
            "contains_spoilers": spoilers, "review": review}
    if rating is not None:
        body["rating"] = rating
    song = favorite() if favorite else None
    if song:
        body["favorite"] = song
    data = actor.step("log %s" % T[key][3], "POST", "/diary/", body)
    if data is not None:
        actor.diary_keys.add(stamp)
        F.made("logs")
    return data


def favorite_song(actor, key, index):
    """The album's n-th song, in the shape a log's `favorite` takes."""
    time.sleep(MUSIC_PAUSE)
    source, media_type, media_id, _ = T[key]
    status, data = actor.call("GET", "/media/%s/%s/%s/" % (source, media_type, media_id))
    time.sleep(MUSIC_PAUSE)
    try:
        tracks = data["music"]["representative_release"]["media"][0]["tracks"]
        track = tracks[min(index, len(tracks) - 1)]
        return {"kind": "song", "title": track["title"], "id": str(track["recording"]["recording_mbid"]),
                "number": str(track["number"])}
    except (TypeError, KeyError, IndexError):
        F.fail(actor.username, "favorite song for " + T[key][3], {"status": status})
        return None


def tv_ticks(actor, key, season, episodes, mark_up_to=None):
    """Watch episodes today. `mark_up_to` marks everything before first, undated."""
    if is_tracked(actor, key):
        return
    source, _, media_id, _ = T[key]
    base = "/tracking/%s/tv/%s/actions/" % (source, media_id)
    if mark_up_to:
        actor.step("mark up to", "POST", base + "mark_up_to/",
                   {"season_number": season, "episode_number": mark_up_to, "mutation_id": mid(actor.username, key, "mark")})
    for episode in episodes:
        actor.step("tick %s S%dE%d" % (T[key][3], season, episode), "POST", base + "tick/",
                   {"season_number": season, "episode_number": episode, "mutation_id": mid(actor.username, key, season, episode)})
    F.made("tv ticks", len(episodes))


def anime_ticks(actor, key, episodes, mark_up_to=None):
    if is_tracked(actor, key):
        return
    source, _, media_id, _ = T[key]
    base = "/tracking/%s/anime/%s/actions/" % (source, media_id)
    if mark_up_to:
        actor.step("mark up to", "POST", base + "mark_up_to/",
                   {"episode_number": mark_up_to, "mutation_id": mid(actor.username, key, "mark")})
    for episode in episodes:
        actor.step("tick %s #%d" % (T[key][3], episode), "POST", base + "tick/",
                   {"episode_number": episode, "mutation_id": mid(actor.username, key, episode)})
    F.made("anime ticks", len(episodes))


def start_on(actor, key, days_ago):
    """Start a title on a past day. For a game the tracking write dates the start row;
    for a book the row is always today (the journey's start date is the past day), so
    a book's story also saves pages today, and that day's progress replaces the start."""
    actor.step("start " + T[key][3], "PUT", track_path(key),
               {"status": "In progress", "start_date": day(days_ago) + "T00:00:00Z"})


def book(actor, key, start, saves, finish=None, pause=None):
    """Start a book on a past day, save pages on past days, then finish or pause it."""
    if is_tracked(actor, key):
        return
    source, _, media_id, name = T[key]
    base = "/tracking/%s/book/%s/" % (source, media_id)
    start_on(actor, key, start)
    for days_ago, pages in saves:
        actor.step("pages " + name, "POST", base + "progress/", {"progress_type": "pages", "value": pages, "progressed_on": day(days_ago)})
        F.made("progress days")
    if finish:
        body = {"completion_date": day(finish["day"]), "mutation_id": mid(actor.username, key, "complete"),
                "rating": finish.get("rating"), "review": finish.get("review", ""), "liked": finish.get("liked", False)}
        actor.step("finish " + name, "POST", base + "complete/", body)
        F.made("logs")
    if pause is not None:
        actor.step("pause " + name, "POST", base + "actions/pause/", {})


def game(actor, key, start, saves, drop=None):
    """Start a game, save playtime (`minutes`) or percent on past days, maybe stop it."""
    if is_tracked(actor, key):
        return
    source, _, media_id, name = T[key]
    base = "/tracking/%s/game/%s/" % (source, media_id)
    start_on(actor, key, start)
    for days_ago, kind, value in saves:
        body = {"progressed_on": day(days_ago)}
        body["total_minutes" if kind == "minutes" else "percentage"] = value
        actor.step("play " + name, "POST", base + "progress/", body)
        F.made("progress days")
    if drop is not None:
        actor.step("did not finish " + name, "POST", base + "actions/drop/", {"end_date": day(drop)})


def manga(actor, key, start, saves):
    if is_tracked(actor, key):
        return
    source, media_type, media_id, name = T[key]
    base = "/tracking/%s/%s/%s/" % (source, media_type, media_id)
    start_on(actor, key, start)
    for days_ago, chapter in saves:
        actor.step("read " + name, "POST", base + "progress/",
                   {"value": chapter, "progressed_on": day(days_ago), "mutation_id": mid(actor.username, key, "ch", chapter)})
        F.made("progress days")


def plan(actor, keys):
    """Add titles to Planning (one row for the day)."""
    for key in keys:
        if is_tracked(actor, key):
            continue
        source, media_type, media_id, name = T[key]
        if media_type == "tv":
            actor.step("plan " + name, "POST", "/tracking/%s/tv/%s/actions/planning/" % (source, media_id),
                       {"mutation_id": mid(actor.username, key, "plan")})
        else:
            actor.step("plan " + name, "PUT", track_path(key), {"status": "Planning"})
        F.made("planning adds")


# ---------------------------------------------------------------------------------
# What each friend did.
# ---------------------------------------------------------------------------------

SHORT = "Everything about the music scene is perfect."


def maya(a):
    log(a, "sinners", 1, 5, True, review="The juke joint sequence is the best thing I've seen in a theater in years. Go in cold.")
    log(a, "battle", 0, 4.5, True, review="Chaotic, funny and weirdly moving. Sean Penn is unhinged in the best way, and the last chase is pure tension. Not a dull minute in nearly three hours.")
    log(a, "pastlives", 3, 5, True, rewatch=True,
        review="Second time and it hurt more. The scene on the bench, where neither of them says the thing, is the whole film: two people who know exactly what they could have had and are kind enough to leave it alone. "
               "I noticed this time how much the camera lets the silences breathe, and how Greta Lee plays two different women depending on which language she is speaking. "
               "I keep thinking about 'in-yun', the idea that every meeting is a result of lives before this one. It made me call an old friend afterwards.")
    log(a, "sos", 2, 4.5, False, review="Still on repeat. Kill Bill and Snooze live in my head rent free.",
        favorite=lambda: favorite_song(a, "sos", 5))
    tv_ticks(a, "severance", 2, [1, 2, 3])
    book(a, "tomorrow", 0, [])


def jules(a):
    log(a, "weapons", 0, 4, False, spoilers=True,
        review="The whole second half is a different movie and I loved that. Amy Madigan steals every scene. The ending with the kids is horrifying and darkly funny.")
    log(a, "anora", 2, 3.5)
    log(a, "challengers", 4, 4, True, rewatch=True, review="Rewatched it for the soundtrack. Still the sexiest tennis match ever filmed.")
    log(a, "marty", 5, None, False, review="Need to sit with this one.")
    plan(a, ["bugonia", "hamnet", "brutalist", "perfectdays"])
    game(a, "eldenring", 6, [(5, "minutes", 300), (3, "minutes", 510), (0, "minutes", 630)])
    game(a, "silksong", 5, [(4, "minutes", 720)], drop=1)


def theo(a):
    tv_ticks(a, "shogun", 1, [1, 2, 3, 4])
    log(a, "pitt", 2, 5, True, season=1, media_type="season",
        review="Fifteen episodes, one shift, and I never once looked at my phone.")
    anime_ticks(a, "onepiece", list(range(1090, 1100)), mark_up_to=1089)
    anime_ticks(a, "dandadan", [1, 2, 3])
    log(a, "dune2", 3, 5, True, review="Saw it again on a big screen. The sandworm ride gets me every time, and Zimmer's score is on another level.")
    book(a, "hailmary", 6, [(5, 96), (4, 250), (0, 304)], finish={"day": 0, "rating": 4.5, "liked": True,
                                                         "review": "Rocky is the best character in any book I read this year. I won't say more."})


def noor(a):
    anime_ticks(a, "frieren", [12, 13, 14, 15, 16], mark_up_to=11)
    book(a, "james", 6, [(5, 74), (3, 160), (0, 188)], pause=True)
    log(a, "gnx", 0, 4, True, review="Squabble Up into TV Off is a perfect two-song run.", favorite=lambda: favorite_song(a, "gnx", 0))
    tv_ticks(a, "bear", 4, [1, 2, 3])
    plan(a, ["andor"])
    log(a, "hamnet", 2, 4.5, True, review="I cried in the first twenty minutes and then I cried at the end. Jessie Buckley deserves everything.")
    log(a, "frankenstein", 4, None, False, review="Gorgeous to look at. Jacob Elordi is a better creature than I expected. A little long, but I'd see it again for the sets.")
    book(a, "intermezzo", 5, [(4, 40), (2, 110), (0, 150)])


def sam(a):
    game(a, "clairobscur", 5, [(4, "percent", 15), (2, "percent", 41), (0, "percent", 63)])
    game(a, "hades2", 6, [(3, "minutes", 260), (1, "minutes", 380)])
    tv_ticks(a, "andor", 1, [1, 2, 3])
    tv_ticks(a, "pluribus", 1, [1, 2])
    log(a, "perfectdays", 2, None, True)
    log(a, "bugonia", 1, 3, False, review="Wanted to love it more. Great performances, but the third act lost me.")
    log(a, "sinners", 0, 4.5, True, rewatch=True, review="Rewatched with friends and it holds up. The music hits harder the second time.")
    log(a, "hmhas", 3, 3.5, False, review="Birds of a Feather is the one that matters.")


FRIEND_STORIES = {"maya": maya, "jules": jules, "theo": theo, "noor": noor, "sam": sam}


def claude_story(a):
    """The phone's own account: full In progress, and a few logs for Trending."""
    # Severance season 2 at episode 7 of 10: three episodes marked, three logged on
    # earlier days, the seventh today.
    if not is_tracked(a, "severance"):
        base = "/tracking/tmdb/tv/95396/actions/"
        a.step("mark up to", "POST", base + "mark_up_to/", {"season_number": 2, "episode_number": 3, "mutation_id": mid("claude", "sev", "mark")})
        for days_ago, episode in ((3, 4), (2, 5), (1, 6)):
            log(a, "severance", days_ago, None, season=2, episode=episode, media_type="episode")
        a.step("tick S2E7", "POST", base + "tick/", {"season_number": 2, "episode_number": 7, "mutation_id": mid("claude", "sev", 7)})
    anime_ticks(a, "onepiece", [1087, 1088], mark_up_to=1086)
    book(a, "intermezzo", 6, [(5, 58), (3, 120), (1, 180), (0, 212)])
    game(a, "clairobscur", 6, [(5, "percent", 12), (2, "percent", 30), (0, "percent", 38)])
    manga(a, "chainsawman", 5, [(4, 98), (2, 120), (1, 139), (0, 145)])
    log(a, "sinners", 2, 4.5, True, review="Better than the hype.")
    log(a, "anora", 1, 4)
    log(a, "dune2", 3, 5, True)
    log(a, "sos", 4, 4)


# ---------------------------------------------------------------------------------
# Likes and comments. Targets are found on the author's activity, by title.
# ---------------------------------------------------------------------------------

# (actor, author, title key, "log" | "progress", comment or None, reply-to-comment or None)
REACTIONS = [
    ("jules", "maya", "pastlives", "log", None, None),
    ("theo", "maya", "pastlives", "log", None, None),
    ("sam", "maya", "pastlives", "log", None, None),
    ("claude", "maya", "pastlives", "log", None, None),
    ("noor", "maya", "pastlives", "log", "The bench scene still gets me every time.", None),
    ("maya", "maya", "pastlives", "log", "Same. Watching it again made it hit harder.", "The bench scene still gets me every time."),
    ("jules", "maya", "pastlives", "log", "Adding it to my rewatch pile this weekend.", "The bench scene still gets me every time."),
    ("claude", "maya", "sinners", "log", None, None),
    ("theo", "maya", "sinners", "log", "Told you to go in cold!", None),
    ("theo", "jules", "weapons", "log", None, None),
    ("maya", "jules", "weapons", "log", "Not reading this until I've seen it, but 4 stars is promising.", None),
    ("maya", "sam", "hades2", "progress", None, None),
    ("noor", "sam", "hades2", "progress", None, None),
    ("jules", "sam", "hades2", "progress", "What boon build are you running?", None),
    ("sam", "theo", "onepiece", "progress", None, None),
    ("jules", "theo", "onepiece", "progress", "Ten episodes in a day?? Respect.", None),
    ("theo", "theo", "onepiece", "progress", "Fillers are a crime, so I skipped to the good arc.", "Ten episodes in a day?? Respect."),
    ("maya", "noor", "hamnet", "log", None, None),
    ("sam", "noor", "hamnet", "log", "Tell me it's worth the tears.", None),
    ("noor", "noor", "hamnet", "log", "Bring tissues. I mean it.", "Tell me it's worth the tears."),
    ("jules", "noor", "gnx", "log", None, None),
    ("noor", "sam", "clairobscur", "progress", None, None),
    ("noor", "sam", "clairobscur", "progress", "Act 2 is unreal. Stay off the wiki!", None),
    ("maya", "theo", "shogun", "progress", "Wait until episode five.", None),
    ("claude", "theo", "dune2", "log", "Sandworm ride on a big screen was the right call.", None),
    ("sam", "jules", "eldenring", "progress", None, None),
    ("maya", "theo", "hailmary", "log", None, None),
    ("claude", "noor", "hamnet", "log", None, None),
]


def activity_index(api_actor, author, cache):
    """The author's activity as the actor sees it: {(media_id, kind): row}."""
    if (api_actor.username, author) in cache:
        return cache[(api_actor.username, author)]
    index = {}
    cursor = None
    for _ in range(10):
        path = "/users/%s/activity/?page_size=100" % author
        if cursor:
            path += "&cursor=" + urllib.parse.quote(cursor)
        status, data = api_actor.call("GET", path)
        if status != 200 or not data:
            break
        for row in data["results"]:
            kind = "log" if row["type"] == "diary_created" else "progress" if row["type"] == "progress_updated" else None
            if kind:
                index.setdefault((str(row["media"]["ref"]["media_id"]), kind), row)
        nxt = data.get("next_cursor")
        if not nxt:
            break
        cursor = urllib.parse.parse_qs(urllib.parse.urlparse(nxt).query).get("cursor", [None])[0]
        if not cursor:
            break
    cache[(api_actor.username, author)] = index
    return index


def post_path(row):
    if row["type"] == "diary_created":
        return "/diary/%d/" % row["id"]
    return "/progress/%d/" % row["object"]["id"]


def comment_thread(actor, row):
    """All comments on a post, as the actor sees them."""
    out = []
    page = 1
    while True:
        status, data = actor.call("GET", post_path(row) + "comments/?page_size=100&page=%d" % page)
        if status != 200 or not data:
            break
        out.extend(data["results"])
        if not data.get("next"):
            break
        page += 1
    return out


def react(actors, cache):
    for who, author, key, kind, text, reply_to in REACTIONS:
        actor = actors.get(who)
        if actor is None or author not in actors:
            continue
        row = activity_index(actor, actors[author].username, cache).get((T[key][2], kind))
        if row is None:
            F.fail(who, "react to %s's %s %s" % (author, T[key][3], kind), "no such row on their activity")
            continue
        if text is None:
            if not (row.get("viewer") or {}).get("has_liked"):
                if actor.step("like", "POST", post_path(row) + "like/") is not None:
                    F.made("likes")
            continue
        thread = comment_thread(actor, row)
        if any(c["user"]["username"] == actor.username and c["body"] == text and not c.get("is_deleted") for c in thread):
            continue
        body = {"body": text}
        if reply_to:
            parent = next((c for c in thread if c["body"] == reply_to and not c.get("parent_id")), None)
            if parent is None:
                F.fail(who, "reply on %s" % T[key][3], "the comment it answers is missing")
                continue
            body["parent_id"] = parent["id"]
        if actor.step("comment", "POST", post_path(row) + "comments/", body) is not None:
            F.made("replies" if reply_to else "comments")


# ---------------------------------------------------------------------------------


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE
    refuse_unsafe(base)
    api = Api(base)
    status, _ = api.raw("GET", "/health/")
    if status != 200:
        sys.exit("No backend answering at %s (health %s)" % (base, status))
    print("Seeding %s (today = %s %s)" % (base, TODAY, TIMEZONE))

    print("Accounts")
    claude = Actor(api, "claude", dev=True)
    actors = {"claude": claude}
    for name, display in FRIENDS:
        try:
            actors[name] = ensure_friend(api, name, display)
        except RuntimeError as error:
            F.fail(name, "account", str(error))
    friends = [actors[n] for n, _ in FRIENDS if n in actors]
    by_base = {n: actors[n].username for n, _ in FRIENDS if n in actors}

    print("Follows")
    edges = [(claude, f.username) for f in friends] + [(f, "claude") for f in friends]
    edges += [(actors[a], by_base[b]) for a, b in FRIEND_FOLLOWS if a in actors and b in by_base]
    for follower, target in edges:
        status, data = follower.call("POST", "/users/%s/follow/" % target)
        if status not in (200, 201):
            F.fail(follower.username, "follow " + target, {"status": status, "body": data})

    print("Friends' activity")
    for name, _ in FRIENDS:
        if name in actors:
            print(" ", actors[name].username)
            try:
                FRIEND_STORIES[name](actors[name])
            except Exception as error:  # noqa: BLE001 - report it and go on
                F.fail(name, "story", repr(error))

    print("claude's own")
    try:
        claude_story(claude)
    except Exception as error:  # noqa: BLE001
        F.fail("claude", "story", repr(error))

    print("Likes and comments")
    try:
        react(actors, {})
    except Exception as error:  # noqa: BLE001
        F.fail("reactions", "run", repr(error))

    print("\nCreated this run:", F.created or "nothing new")
    print("Failures: %d" % len(F.items))
    for item in F.items:
        print("  -", item)
    print("\nAccounts (password %s):" % PASSWORD)
    for name, display in FRIENDS:
        if name in actors:
            print("  %s (%s)" % (actors[name].username, display))

    print("\nFeed as claude:")
    show_feed(claude)


def show_feed(actor):
    status, data = actor.call("GET", "/feed/?page_size=50&include=planning")
    if status != 200:
        print("  feed answered", status, data)
        return
    for row in data["results"]:
        media = row.get("media") or {}
        poster = media.get("poster_url") or media.get("image_url") or ""
        obj = row.get("object") or {}
        what = obj.get("summary") or ("%s%s" % (obj.get("rating") or "", " liked" if obj.get("liked") else ""))
        print("  %s %-6s %-17s %-34s %s | %s" % (
            row["consumed_at"], row["actor"]["username"], row["type"], (media.get("title") or "")[:34],
            what[:30], "poster" if poster.startswith("http") else "NO POSTER"))
    print("  %d rows%s" % (len(data["results"]), "" if not data.get("next_cursor") else " (more pages)"))


if __name__ == "__main__":
    main()
