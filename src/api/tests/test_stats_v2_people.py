"""Stats v2 people (STAT-219): who you logged most, the show-all endpoint and the drill.

Credits are written straight to ItemCredit here; storing them from provider metadata
is tested in test_item_credits.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from api.services import stats_v2_people as people
from api.services.stats_scope import StatsScope
from api.services.stats_v2 import build_stats_v2
from app.models import (
    TV,
    DiaryEntry,
    Item,
    ItemCredit,
    MediaTypes,
    Movie,
    Sources,
    Status,
)
from social.models import Follow, FollowStatus

NEW_YORK = "America/New_York"
MAR_2025 = datetime(2025, 3, 2, tzinfo=UTC)
JUN_2024 = datetime(2024, 6, 1, tzinfo=UTC)
NOW = datetime(2026, 10, 26, 16, 0, tzinfo=UTC)
YEAR_2025 = (date(2025, 1, 1), date(2025, 12, 31))
DIRECTOR = ItemCredit.Role.DIRECTOR.value
CAST = ItemCredit.Role.CAST.value
CREATOR = ItemCredit.Role.CREATOR.value
ARTIST = ItemCredit.Role.ARTIST.value
AUTHOR = ItemCredit.Role.AUTHOR.value
SOURCES = {
    MediaTypes.MUSIC.value: Sources.APPLE_MUSIC.value,
    MediaTypes.BOOK.value: Sources.HARDCOVER.value,
}


@override_settings(TIME_ZONE=NEW_YORK, MUSIC_ENABLED=True)
class PeopleBase(TestCase):
    """An owner, a follower and a stranger, with Item, credit and log helpers."""

    counter = 0

    def setUp(self):
        clock = patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        users = get_user_model().objects
        self.owner = users.create_user(username="owner", password="strong-password-123")
        self.follower = users.create_user(username="follower", password="strong-password-123")
        self.stranger = users.create_user(username="stranger", password="strong-password-123")
        Follow.objects.create(from_user=self.follower, to_user=self.owner, status=FollowStatus.ACCEPTED)

    def item(self, media_type=MediaTypes.MOVIE.value, *, media_id=None, season=None, episode=None, people=()):
        """Create an Item and its credits: (role, person_id, name), in billing order."""
        PeopleBase.counter += 1
        item = Item.objects.create(
            media_id=media_id or str(1000 + PeopleBase.counter),
            source=SOURCES.get(media_type, Sources.TMDB.value),
            media_type=media_type,
            season_number=season,
            episode_number=episode,
            title=f"{media_type} {PeopleBase.counter}",
            image="https://example.com/poster.jpg",
        )
        person_source = SOURCES.get(media_type, Sources.TMDB.value)
        ItemCredit.objects.bulk_create(
            [
                ItemCredit(
                    item=item,
                    role=role,
                    person_source=person_source,
                    person_id=str(person_id),
                    name=name,
                    order=order,
                    image_url=f"https://example.com/{person_id}.jpg" if person_id == 1 else "",
                )
                for order, (role, person_id, name) in enumerate(people)
            ],
        )
        return item

    def show(self, *, people=(), seasons=1):
        show = self.item(MediaTypes.TV.value, people=people)
        return show, [
            self.item(MediaTypes.SEASON.value, media_id=show.media_id, season=number)
            for number in range(1, seasons + 1)
        ]

    def log(self, item, consumed_at=MAR_2025, *, rating=None, visibility="public"):
        return DiaryEntry.objects.create(
            user=self.owner,
            item=item,
            consumed_at=consumed_at,
            rating=rating,
            visibility=visibility,
        )

    def scope(self, media_type=MediaTypes.MOVIE.value, *, dates=(None, None), viewer=None):
        return StatsScope(
            owner=self.owner,
            viewer=viewer or self.owner,
            start_date=dates[0],
            end_date=dates[1],
            media_type=media_type,
        )

    def group(self, section, role):
        return next(group for group in section["groups"] if group["role"] == role)

    def counts(self, group):
        return [(row["person"]["name"], row["titles"]) for row in group["rows"]]


class PeopleSectionTests(PeopleBase):
    """The section's groups, counts, order and ratings."""

    def test_only_the_people_tabs_have_the_section(self):
        tabs = {
            None: None,
            "movie": ["director", "cast"],
            "tv": ["cast", "creator"],
            "music": ["artist"],
            "book": ["author"],
            "anime": None,
            "manga": None,
            "game": None,
            "comic": None,
        }
        for tab, roles in tabs.items():
            section = build_stats_v2(self.scope(tab))["people"]
            if roles is None:
                self.assertIsNone(section, tab)
            else:
                self.assertEqual([group["role"] for group in section["groups"]], roles, tab)
                self.assertEqual([group["rows"] for group in section["groups"]], [[]] * len(roles), tab)
                self.assertEqual([group["rated_rows"] for group in section["groups"]], [[]] * len(roles), tab)
                self.assertEqual([group["total"] for group in section["groups"]], [0] * len(roles), tab)

    def test_labels_and_the_row_shape(self):
        film = self.item(people=[(DIRECTOR, 1, "Greta Gerwig"), (CAST, 2, "Saoirse Ronan")])
        self.log(film, rating=Decimal("9.0"))

        section = people.build_people(self.scope())

        self.assertEqual([group["label"] for group in section["groups"]], ["Directors", "Cast"])
        self.assertEqual(set(section["groups"][0]), {"role", "label", "rows", "rated_rows", "total"})
        self.assertEqual(
            self.group(section, DIRECTOR)["rows"],
            [
                {
                    "person": {
                        "source": "tmdb",
                        "id": "1",
                        "name": "Greta Gerwig",
                        "image_url": "https://example.com/1.jpg",
                    },
                    "titles": 1,
                    "rated": 1,
                    "average_stars": "4.5",
                },
            ],
        )
        self.assertIsNone(self.group(section, CAST)["rows"][0]["person"]["image_url"])

    def test_people_are_counted_by_distinct_titles_and_sorted(self):
        # Two logs of one film count it once; ties go to the higher average, then name.
        first = self.item(people=[(DIRECTOR, 1, "Ari Aster"), (CAST, 10, "Toni Collette")])
        second = self.item(people=[(DIRECTOR, 1, "Ari Aster"), (CAST, 11, "Florence Pugh")])
        third = self.item(people=[(DIRECTOR, 2, "Bong Joon-ho"), (CAST, 11, "Florence Pugh")])
        fourth = self.item(people=[(DIRECTOR, 3, "Celine Song")])
        fifth = self.item(people=[(DIRECTOR, 4, "Ava DuVernay")])
        self.log(first, rating=Decimal("6.0"))
        self.log(first, rating=Decimal("8.0"))
        self.log(second)
        self.log(third, rating=Decimal("7.0"))
        self.log(fourth, rating=Decimal("10.0"))
        self.log(fifth)

        section = people.build_people(self.scope())

        directors = self.group(section, DIRECTOR)
        self.assertEqual(
            self.counts(directors),
            [("Ari Aster", 2), ("Celine Song", 1), ("Bong Joon-ho", 1), ("Ava DuVernay", 1)],
        )
        self.assertEqual(directors["total"], 4)
        # Ari Aster's rated title is the first film, at its newest rated log's 8.0.
        self.assertEqual((directors["rows"][0]["rated"], directors["rows"][0]["average_stars"]), (1, "4.0"))
        self.assertEqual(directors["rows"][3]["average_stars"], None)
        cast = self.group(section, CAST)
        self.assertEqual(self.counts(cast), [("Florence Pugh", 2), ("Toni Collette", 1)])
        self.assertEqual((cast["rows"][0]["rated"], cast["rows"][0]["average_stars"]), (1, "3.5"))

    def test_ten_rows_are_sent_and_total_counts_everyone(self):
        for index in range(12):
            self.log(self.item(people=[(CAST, 100 + index, f"Actor {index:02d}")]))

        cast = self.group(people.build_people(self.scope()), CAST)

        self.assertEqual(len(cast["rows"]), people.PREVIEW_LIMIT)
        self.assertEqual(cast["total"], 12)
        self.assertEqual(cast["rows"][0]["person"]["name"], "Actor 00")

    def test_a_range_counts_only_its_titles_and_their_ratings(self):
        old = self.item(people=[(DIRECTOR, 1, "Agnes Varda")])
        new = self.item(people=[(DIRECTOR, 1, "Agnes Varda")])
        self.log(old, JUN_2024, rating=Decimal("10.0"))
        self.log(new, MAR_2025, rating=Decimal("6.0"))

        all_time = self.group(people.build_people(self.scope()), DIRECTOR)["rows"][0]
        year = self.group(people.build_people(self.scope(dates=YEAR_2025)), DIRECTOR)["rows"][0]

        self.assertEqual((all_time["titles"], all_time["rated"], all_time["average_stars"]), (2, 2, "4.0"))
        self.assertEqual((year["titles"], year["rated"], year["average_stars"]), (1, 1, "3.0"))

    def test_all_time_counts_undated_completions_with_their_current_rating(self):
        watched = self.item(people=[(DIRECTOR, 1, "Wong Kar-wai")])
        Movie.objects.bulk_create([
            Movie(user=self.owner, item=watched, status=Status.COMPLETED.value, score=Decimal("9.0"), direct_consumption=True),
        ])

        all_time = self.group(people.build_people(self.scope()), DIRECTOR)["rows"]
        year = self.group(people.build_people(self.scope(dates=YEAR_2025)), DIRECTOR)["rows"]

        self.assertEqual([(row["titles"], row["rated"], row["average_stars"]) for row in all_time], [(1, 1, "4.5")])
        self.assertEqual(year, [])

    def test_tv_counts_the_show_once_through_its_seasons(self):
        show, (first, second) = self.show(people=[(CAST, 1, "Rhea Seehorn"), (CREATOR, 2, "Vince Gilligan")], seasons=2)
        _other, (season,) = self.show(people=[(CAST, 1, "Rhea Seehorn")])
        self.log(first)
        self.log(second)
        self.log(season)
        self.log(show, rating=Decimal("10.0"))

        section = people.build_people(self.scope(MediaTypes.TV.value))

        cast = self.group(section, CAST)
        self.assertEqual(self.counts(cast), [("Rhea Seehorn", 2)])
        # The show log's rating is the show's title rating; season logs have their own.
        self.assertEqual((cast["rows"][0]["rated"], cast["rows"][0]["average_stars"]), (1, "5.0"))
        self.assertEqual(self.counts(self.group(section, CREATOR)), [("Vince Gilligan", 1)])

    def test_other_viewers_lose_only_titles_reached_through_hidden_logs(self):
        _hidden, (hidden_season,) = self.show(people=[(CAST, 1, "Bob Odenkirk")])
        _shown, (shown_season,) = self.show(people=[(CAST, 1, "Bob Odenkirk")])
        completed = self.item(MediaTypes.TV.value, people=[(CAST, 1, "Bob Odenkirk")])
        self.log(hidden_season, visibility="private")
        self.log(shown_season, visibility="public")
        TV.objects.bulk_create([TV(user=self.owner, item=completed, status=Status.COMPLETED.value)])

        owner = self.group(people.build_people(self.scope(MediaTypes.TV.value)), CAST)
        follower = self.group(people.build_people(self.scope(MediaTypes.TV.value, viewer=self.follower)), CAST)
        stranger = self.group(people.build_people(self.scope(MediaTypes.TV.value, viewer=self.stranger)), CAST)

        self.assertEqual(self.counts(owner), [("Bob Odenkirk", 3)])
        self.assertEqual(self.counts(follower), [("Bob Odenkirk", 2)])
        self.assertEqual(self.counts(stranger), [("Bob Odenkirk", 2)])

    def test_music_and_books_have_one_group(self):
        album = self.item(MediaTypes.MUSIC.value, people=[(ARTIST, 1, "SZA")])
        book = self.item(MediaTypes.BOOK.value, people=[(AUTHOR, 7, "Ursula K. Le Guin")])
        self.log(album)
        self.log(book)

        artists = people.build_people(self.scope(MediaTypes.MUSIC.value))["groups"]
        authors = people.build_people(self.scope(MediaTypes.BOOK.value))["groups"]

        self.assertEqual([(group["label"], self.counts(group)) for group in artists], [("Artists", [("SZA", 1)])])
        self.assertEqual(artists[0]["rows"][0]["person"]["source"], "apple_music")
        self.assertEqual(
            [(group["label"], self.counts(group)) for group in authors],
            [("Authors", [("Ursula K. Le Guin", 1)])],
        )

    def test_highest_rated_needs_three_rated_titles(self):
        many = [self.item(people=[(DIRECTOR, 1, "Steady")]) for _ in range(4)]
        few = [self.item(people=[(DIRECTOR, 2, "Brilliant")]) for _ in range(2)]
        three = [self.item(people=[(DIRECTOR, 3, "Great")]) for _ in range(3)]
        for film in many:
            self.log(film, rating=Decimal("6.0"))
        for film in few:
            self.log(film, rating=Decimal("10.0"))
        for film in three:
            self.log(film, rating=Decimal("9.0"))

        by_titles = people.people_page(self.scope(), DIRECTOR, "titles", 1, 25)
        by_rating = people.people_page(self.scope(), DIRECTOR, "rating", 1, 25)

        self.assertEqual(
            [row["person"]["name"] for row in by_titles["results"]],
            ["Steady", "Great", "Brilliant"],
        )
        self.assertEqual(by_titles["count"], 3)
        self.assertEqual(
            [(row["person"]["name"], row["average_stars"]) for row in by_rating["results"]],
            [("Great", "4.5"), ("Steady", "3.0")],
        )
        self.assertEqual(by_rating["count"], 2)
        # The summary's rated_rows are the same order's first rows.
        directors = self.group(people.build_people(self.scope()), DIRECTOR)
        self.assertEqual(directors["rated_rows"], by_rating["results"])
        self.assertEqual(directors["rows"], by_titles["results"])

    def test_rated_rows_reach_past_the_ten_most_logged(self):
        # Ten people with 5 titles each fill rows; the best rated have 3 titles.
        for index in range(10):
            for _ in range(5):
                self.log(self.item(people=[(DIRECTOR, 100 + index, f"Busy {index}")]), rating=Decimal("5.0"))
        for person_id, name, rating in ((1, "Gem", "10.0"), (2, "Almost", "9.0"), (3, "Tied gem", "10.0")):
            for _ in range(3):
                self.log(self.item(people=[(DIRECTOR, person_id, name)]), rating=Decimal(rating))
        for _ in range(2):
            self.log(self.item(people=[(DIRECTOR, 4, "Two only")]), rating=Decimal("10.0"))
        for index in range(3):
            self.log(self.item(people=[(DIRECTOR, 5, "Half rated")]), rating=Decimal("10.0") if index else None)

        directors = self.group(people.build_people(self.scope()), DIRECTOR)

        self.assertEqual([row["person"]["name"] for row in directors["rows"]], [f"Busy {index}" for index in range(10)])
        self.assertEqual(directors["total"], 15)
        rated = [(row["person"]["name"], row["titles"], row["rated"], row["average_stars"]) for row in directors["rated_rows"]]
        # Average stars, then titles, then name; fewer than 3 rated titles never rank.
        self.assertEqual(
            rated[:4],
            [("Gem", 3, 3, "5.0"), ("Tied gem", 3, 3, "5.0"), ("Almost", 3, 3, "4.5"), ("Busy 0", 5, 5, "2.5")],
        )
        self.assertEqual(len(rated), people.PREVIEW_LIMIT)
        self.assertNotIn("Two only", [row[0] for row in rated])
        self.assertNotIn("Half rated", [row[0] for row in rated])

    def test_query_count_does_not_grow_with_the_library(self):
        def seed(count):
            for _ in range(count):
                PeopleBase.counter += 1
                film = self.item(
                    people=[(DIRECTOR, PeopleBase.counter, "Director")]
                    + [(CAST, 10_000 + PeopleBase.counter * 20 + index, "Actor") for index in range(15)],
                )
                self.log(film, rating=Decimal("7.0"))
                _show, (season,) = self.show(people=[(CAST, 1, "Lead"), (CREATOR, 2, "Creator")])
                self.log(season)

        def count_queries(scope):
            with CaptureQueriesContext(connection) as queries:
                people.build_people(scope)
            return len(queries)

        seed(3)
        small = [count_queries(self.scope(tab, dates=dates)) for tab in ("movie", "tv") for dates in ((None, None), YEAR_2025)]
        seed(9)
        large = [count_queries(self.scope(tab, dates=dates)) for tab in ("movie", "tv") for dates in ((None, None), YEAR_2025)]

        self.assertEqual(small, large)
        # One query for every role of the tab.
        self.assertEqual(small, [1, 1, 1, 1])


class PeopleEndpointTests(PeopleBase):
    """GET stats/me/people/ and users/{username}/stats/people/ (STAT-310)."""

    def get(self, viewer, params, path="/api/v1/stats/me/people/"):
        client = APIClient()
        client.force_authenticate(viewer)
        return client.get(path, params)

    def test_pages_one_role(self):
        for index in range(3):
            film = self.item(people=[(CAST, 1, "Everywhere"), (CAST, 10 + index, f"Once {index}")])
            self.log(film)

        response = self.get(
            self.owner,
            {"media_type": "movie", "role": "cast", "start_date": "all", "end_date": "all", "page_size": 2},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 4)
        self.assertIsNone(response.data["previous"])
        self.assertIn("page=2", response.data["next"])
        self.assertEqual(
            [(row["person"]["name"], row["titles"]) for row in response.data["results"]],
            [("Everywhere", 3), ("Once 0", 1)],
        )
        last = self.get(
            self.owner,
            {"media_type": "movie", "role": "cast", "start_date": "all", "end_date": "all", "page_size": 2, "page": 2},
        )
        self.assertEqual([row["person"]["name"] for row in last.data["results"]], ["Once 1", "Once 2"])
        self.assertIsNone(last.data["next"])
        past = self.get(
            self.owner,
            {"media_type": "movie", "role": "cast", "start_date": "all", "end_date": "all", "page_size": 2, "page": 3},
        )
        self.assertEqual(past.status_code, 404)

    def test_bad_parameters_are_400s(self):
        cases = [
            ({"role": "cast"}, "media_type"),
            ({"media_type": "anime", "role": "cast"}, "media_type"),
            ({"media_type": "movie"}, "role"),
            ({"media_type": "movie", "role": "creator"}, "role"),
            ({"media_type": "movie", "role": "cast", "sort": "name"}, "sort"),
            ({"media_type": "movie", "role": "cast", "start_date": "soon"}, "start_date"),
        ]
        for params, key in cases:
            response = self.get(self.owner, params)
            self.assertEqual(response.status_code, 400, params)
            self.assertIn(key, response.data["error"]["fields"], params)

    def test_other_users_go_through_the_profile_gate(self):
        self.log(self.item(people=[(DIRECTOR, 1, "Kelly Reichardt")]))
        params = {"media_type": "movie", "role": "director", "start_date": "all", "end_date": "all"}
        path = f"/api/v1/users/{self.owner.username}/stats/people/"

        shown = self.get(self.follower, params, path=path)
        self.assertEqual(shown.status_code, 200)
        self.assertEqual([row["person"]["name"] for row in shown.data["results"]], ["Kelly Reichardt"])

        self.owner.profile_private = True
        self.owner.save(update_fields=["profile_private"])
        self.assertEqual(self.get(self.stranger, params, path=path).status_code, 404)
        self.assertEqual(self.get(self.stranger, params, path="/api/v1/users/nobody/stats/people/").status_code, 404)

    def test_the_summary_sends_the_section(self):
        self.log(self.item(people=[(DIRECTOR, 1, "Jordan Peele")]))
        client = APIClient()
        client.force_authenticate(self.owner)

        movie = client.get("/api/v1/stats/me/summary/", {"schema": "2", "start_date": "all", "end_date": "all", "media_type": "movie"})
        everything = client.get("/api/v1/stats/me/summary/", {"schema": "2", "start_date": "all", "end_date": "all"})

        self.assertEqual(self.counts(self.group(movie.data["people"], DIRECTOR)), [("Jordan Peele", 1)])
        self.assertIsNone(everything.data["people"])


class CreditDrillTests(PeopleBase):
    """GET diary/?credit=<source>:<person_id> opens the logs of a person's titles."""

    def diary(self, params):
        client = APIClient()
        client.force_authenticate(self.owner)
        response = client.get("/api/v1/diary/", {"skip_backfill": "true", **params})
        self.assertEqual(response.status_code, 200)
        return sorted(row["id"] for row in response.data["results"])

    def test_a_person_opens_the_logs_of_their_titles(self):
        film = self.item(people=[(DIRECTOR, 1, "Greta Gerwig"), (CAST, 1, "Greta Gerwig")])
        acted = self.item(people=[(CAST, 1, "Greta Gerwig")])
        other = self.item(people=[(DIRECTOR, 2, "Noah Baumbach")])
        logs = [self.log(film), self.log(film), self.log(acted), self.log(other)]

        self.assertEqual(self.diary({"credit": "tmdb:1"}), sorted(entry.id for entry in logs[:3]))
        self.assertEqual(self.diary({"credit": "tmdb:1", "role": "director"}), sorted(entry.id for entry in logs[:2]))
        self.assertEqual(
            self.diary({"credit": ["tmdb:1", "tmdb:2"], "role": "director"}),
            sorted([logs[0].id, logs[1].id, logs[3].id]),
        )
        self.assertEqual(self.diary({"credit": "apple_music:1"}), [])
        self.assertEqual(self.diary({"credit": "nonsense"}), [])

    def test_tv_rollup_matches_season_and_episode_logs_through_the_show(self):
        show, (season,) = self.show(people=[(CAST, 1, "Rhea Seehorn")])
        episode = self.item(MediaTypes.EPISODE.value, media_id=show.media_id, season=1, episode=1)
        logs = [self.log(show), self.log(season), self.log(episode)]

        rolled = self.diary({
            "credit": "tmdb:1",
            "role": "cast",
            "tv_rollup": "true",
            "media_type": ["tv", "season", "episode"],
        })
        exact = self.diary({"credit": "tmdb:1", "media_type": ["tv", "season", "episode"]})

        self.assertEqual(rolled, sorted(entry.id for entry in logs))
        self.assertEqual(exact, [logs[0].id])
