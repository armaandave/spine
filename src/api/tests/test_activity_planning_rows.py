"""AC-8: a person's day of Planning adds is one row, only for a request that asks.

`include=planning` on Home and on a profile's plain list; never on a list opened from a
number, and never without it, so installed builds get exactly the pages they did. A
Planning row has no likes or comments, adds nothing to any number, and is no active day.
"""

from datetime import UTC, date, datetime

from rest_framework.test import APIClient

from api.services import activity_rows
from api.tests.test_activity_planning_writes import (
    NEW_YORK,
    PlanningWriteBase,
)
from api.tests.test_activity_status_rows import rendered
from app import (
    book_tracking,
    game_tracking,
    series_tracking,
    single_weight,
    tv_tracking,
)
from app.models import Status
from app.tests.tv_fixtures import FakeTVProvider, make_show
from social.models import Block, Follow, FollowStatus, ProgressChange

ASK = {"include": "planning"}


class PlanningRowBase(PlanningWriteBase):
    """A reader with a friend who follows them, and the friend's own adds."""

    def home(self, params=None, *, user=None):
        return self.get("/api/v1/feed/", {**ASK, **(params or {})}, user=user or self.friend)

    def profile(self, params=None, *, viewer=None):
        return self.get(f"/api/v1/users/{self.user.username}/activity/", {**ASK, **(params or {})}, user=viewer or self.friend)

    def planning(self, payload):
        return [row for row in payload["results"] if row["type"] == "planning_added"]

    def add(self, count, media_type="movie", *, user=None, **fields):
        items = [self.item(media_type, **fields) for _ in range(count)]
        for item in items:
            self.put(item, user=user)
        return items


class DayRowTests(PlanningRowBase):
    """One row per person per day, with the day's earliest add on its face."""

    def test_three_movies_and_two_others_make_one_row_saying_five(self):
        movies = self.add(3)
        book = self.item("book")
        game = self.item("game")
        self.later()
        self.put(book)
        self.put(game)
        rows = self.planning(self.home())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["type"], "planning_added")
        self.assertEqual(row["object"]["summary"], "added 5 titles to Planning")
        self.assertEqual((row["object"]["count"], row["object"]["media_type"]), (5, None))
        self.assertEqual(row["consumed_at"], "2026-10-25")
        self.assertEqual(row["media"]["title"], movies[0].title)
        # The row reads as its count, each title's name under it, oldest first.
        self.assertEqual(row["object"]["titles"], [*(movie.title for movie in movies), book.title, game.title])
        self.assertEqual((row["like_count"], row["comment_count"], row["viewer"]), (0, 0, {"can_view": True, "has_liked": False}))
        self.assertEqual(row["actor"]["username"], "reader")
        self.assertLess(row["id"], 0)
        self.assertEqual(-row["id"], row["object"]["id"])

    def test_the_wire_shape(self):
        self.add(2)
        row = self.planning(self.home())[0]
        self.assertEqual(
            {key: type(value) for key, value in row.items()},
            {
                "id": int,
                "type": str,
                "created_at": str,
                "consumed_at": str,
                "actor": dict,
                "media": dict,
                "person": type(None),
                "object": dict,
                "viewer": dict,
                "like_count": int,
                "comment_count": int,
            },
        )
        self.assertEqual(
            set(row["object"]),
            {"type", "id", "summary", "label", "count", "media_type", "titles"},
        )
        self.assertEqual(row["object"]["type"], "planning")

    def test_one_type_says_its_noun(self):
        self.add(3, "movie")
        self.assertEqual(self.planning(self.home())[0]["object"]["summary"], "added 3 movies to Planning")
        self.assertEqual(self.planning(self.home())[0]["object"]["media_type"], "movie")

    def test_the_nouns(self):
        for media_type, plural, single in (
            ("music", "albums", "album"),
            ("comic", "comics", "comic"),
            ("manga", "manga", "manga"),
            ("game", "games", "game"),
        ):
            with self.subTest(media_type):
                ProgressChange.objects.all().delete()
                self.add(2, media_type)
                self.assertEqual(self.planning(self.home())[0]["object"]["summary"], f"added 2 {plural} to Planning")
                ProgressChange.objects.all().delete()
                self.add(1, media_type)
                self.assertEqual(self.planning(self.home())[0]["object"]["summary"], f"added 1 {single} to Planning")

    def test_removing_a_title_says_four(self):
        items = [*self.add(3), self.item("book"), self.item("game")]
        self.put(items[3])
        self.put(items[4])
        self.assertEqual(self.planning(self.home())[0]["object"]["count"], 5)
        single_weight.unwatch(self.user, items[0])
        # The receiver forgets a removed title's rows on commit.
        with self.captureOnCommitCallbacks(execute=True):
            from app.models import Book

            Book.objects.filter(user=self.user, item=items[3]).delete()
        row = self.planning(self.home())[0]
        self.assertEqual(row["object"]["count"], 3)

    def test_a_removed_title_drops_out_even_if_its_row_stayed(self):
        items = self.add(3)
        from app.models import Movie

        # The receiver forgets the title's rows on commit, which a test never reaches:
        # the read doesn't depend on it.
        Movie.objects.filter(user=self.user, item=items[1]).delete()
        self.assertTrue(ProgressChange.objects.filter(item=items[1]).exists())
        self.assertEqual(self.planning(self.home())[0]["object"]["count"], 2)

    def test_the_row_keeps_its_face_as_adds_pile_up(self):
        first = self.item("movie")
        self.put(first)
        before = self.planning(self.home())[0]["media"]["title"]
        self.later()
        self.add(2)
        self.assertEqual(self.planning(self.home())[0]["media"]["title"], before)
        self.assertEqual(before, first.title)

    def test_a_title_added_twice_in_a_day_counts_once(self):
        item = self.item("movie")
        self.put(item)
        self.put(item, Status.DROPPED.value)
        self.put(item)
        self.assertEqual(self.planning(self.home())[0]["object"]["count"], 1)

    def test_adds_at_2330_and_0030_new_york_are_two_rows(self):
        self.clock.set(datetime(2026, 10, 26, 3, 30, tzinfo=UTC))
        self.put(self.item("movie"))
        self.clock.set(datetime(2026, 10, 26, 4, 30, tzinfo=UTC))
        self.put(self.item("movie"))
        rows = self.planning(self.home())
        self.assertEqual([row["consumed_at"] for row in rows], ["2026-10-26", "2026-10-25"])

    def test_logging_one_of_the_movies_keeps_the_row_and_adds_the_log(self):
        movies = self.add(2, runtime_minutes=120)
        single_weight.create_log(self.user, movies[0], consumed_at=date(2026, 10, 25))
        types = [row["type"] for row in self.home()["results"]]
        self.assertEqual(sorted(types), ["diary_created", "planning_added"])
        self.assertEqual(self.planning(self.home())[0]["object"]["count"], 2)

    def test_a_row_sorts_by_its_day_among_the_rest(self):
        book = self.item("book", total_pages=300)
        book_tracking.start(self.user, book)
        book_tracking.update_progress(self.user, book, progress_type="pages", value=20)
        self.later(24 * 60)
        self.add(2)
        kinds = [row["type"] for row in self.home()["results"]]
        self.assertEqual(kinds, ["planning_added", "progress_updated"])


class OptInTests(PlanningRowBase):
    """Installed builds never get a Planning row."""

    def test_a_default_request_returns_none(self):
        self.add(3)
        self.assertEqual(self.get("/api/v1/feed/", user=self.friend)["results"], [])
        self.assertEqual(self.get("/api/v1/users/reader/activity/", user=self.friend)["results"], [])
        self.assertEqual(self.get("/api/v1/users/reader/activity/", {"include": "other"}, user=self.friend)["results"], [])

    def test_the_default_pages_are_byte_identical_with_and_without_adds(self):
        book = self.item("book", total_pages=300)
        book_tracking.start(self.user, book)
        book_tracking.update_progress(self.user, book, progress_type="pages", value=20)
        before = (self.get("/api/v1/feed/", user=self.friend), self.get("/api/v1/users/reader/activity/", user=self.friend))
        self.later()
        self.add(4)
        after = (self.get("/api/v1/feed/", user=self.friend), self.get("/api/v1/users/reader/activity/", user=self.friend))
        self.assertEqual(before, after)

    def test_include_works_on_a_comma_list_and_the_profile(self):
        self.add(2)
        both = self.get("/api/v1/users/reader/activity/", {"include": "other,planning"}, user=self.friend)
        self.assertEqual(len(self.planning(both)), 1)
        self.assertEqual(len(self.planning(self.profile())), 1)

    def test_a_list_opened_from_a_number_never_has_one(self):
        book = self.item("book", total_pages=300)
        book_tracking.start(self.user, book)
        book_tracking.update_progress(self.user, book, progress_type="pages", value=20)
        self.add(2, "book")
        for params in ({"media_type": "book"}, {"start_date": "2026-01-01", "end_date": "2026-12-31"}, {"counts": "titles"}):
            with self.subTest(params):
                page = self.profile(params, viewer=self.user)
                self.assertEqual(self.planning(page), [])
        # A plain list asked to read as a list still gets them.
        self.assertEqual(len(self.planning(self.profile({"view": "list"}))), 1)

    def test_home_filtered_to_a_type_counts_only_that_type(self):
        self.add(2, "movie")
        self.later()
        self.add(1, "book")
        rows = self.planning(self.home({"media_type": "book"}))
        self.assertEqual([row["object"]["summary"] for row in rows], ["added 1 book to Planning"])
        rows = self.planning(self.home({"media_type": "movie"}))
        self.assertEqual([row["object"]["summary"] for row in rows], ["added 2 movies to Planning"])

    def test_a_row_has_no_post_page(self):
        self.add(2)
        row = self.planning(self.home())[0]
        client = APIClient()
        client.force_authenticate(self.friend)
        base = f"/api/v1/progress/{row['object']['id']}/"
        self.assertEqual(client.get(base).status_code, 404)
        self.assertEqual(client.get(f"{base}comments/").status_code, 404)
        self.assertEqual(client.post(f"{base}like/").status_code, 404)


class VisibilityTests(PlanningRowBase):
    """Anyone who can see the profile sees the row (AC-5)."""

    def test_a_follower_a_stranger_and_the_owner_on_a_public_profile(self):
        self.add(2)
        stranger = self.stranger()
        for viewer in (self.user, self.friend, stranger):
            self.assertEqual(len(self.planning(self.profile(viewer=viewer))), 1, viewer.username)

    def stranger(self):
        from app.tests.tv_fixtures import make_user

        return make_user(username="stranger", calendar=NEW_YORK)

    def test_a_private_profile_shows_only_to_followers(self):
        self.add(2)
        self.user.profile_private = True
        self.user.save()
        stranger = self.stranger()
        self.assertEqual(self.planning(self.profile(viewer=stranger)), [])
        self.assertEqual(len(self.planning(self.profile(viewer=self.friend))), 1)

    def test_a_block_either_way_shows_nothing(self):
        self.add(2)
        Block.objects.create(blocker=self.user, blocked=self.friend)
        self.assertEqual(self.planning(self.profile(viewer=self.friend)), [])
        Block.objects.all().delete()
        Block.objects.create(blocker=self.friend, blocked=self.user)
        self.assertEqual(self.planning(self.profile(viewer=self.friend)), [])

    def test_home_shows_only_people_you_follow(self):
        stranger = self.stranger()
        self.add(2, user=stranger)
        self.assertEqual(self.planning(self.home()), [])
        Follow.objects.create(from_user=self.friend, to_user=stranger, status=FollowStatus.ACCEPTED)
        rows = self.planning(self.home())
        self.assertEqual([row["actor"]["username"] for row in rows], ["stranger"])

    def test_a_pending_follow_is_not_a_follow(self):
        stranger = self.stranger()
        self.add(2, user=stranger)
        Follow.objects.create(from_user=self.friend, to_user=stranger, status=FollowStatus.PENDING)
        self.assertEqual(self.planning(self.home()), [])


class CursorTests(PlanningRowBase):
    """A walk by cursor visits every row once, Planning rows among the rest."""

    def test_a_walk_across_pages(self):
        for day in range(4):
            book = self.item("book", total_pages=300)
            book_tracking.start(self.user, book)
            book_tracking.update_progress(self.user, book, progress_type="pages", value=20 + day)
            self.add(2)
            self.later(24 * 60)
        seen = []
        params = {**ASK, "page_size": 3}
        client = APIClient(HTTP_X_SPINE_TIMEZONE=NEW_YORK)
        client.force_authenticate(self.friend)
        url = "/api/v1/feed/"
        while url:
            response = client.get(url, params if "?" not in url else None)
            self.assertEqual(response.status_code, 200)
            data = rendered(response)
            seen.extend((row["type"], row["consumed_at"]) for row in data["results"])
            url = data["next_cursor"]
        self.assertEqual(len(seen), 8)
        self.assertEqual(len(set(seen)), 8)
        self.assertEqual(seen, sorted(seen, key=lambda row: row[1], reverse=True))


class OpeningTests(PlanningRowBase):
    """GET /planning/<id>/ lists the titles added that day."""

    def test_it_lists_the_days_titles_in_the_order_added(self):
        movies = self.add(3)
        self.later()
        book = self.item("book")
        self.put(book)
        row = self.planning(self.home())[0]
        client = APIClient()
        client.force_authenticate(self.friend)
        opened = rendered(client.get(f"/api/v1/planning/{row['object']['id']}/"))
        self.assertEqual(opened["count"], 4)
        self.assertEqual(opened["summary"], "added 4 titles to Planning")
        self.assertEqual(opened["consumed_at"], "2026-10-25")
        self.assertEqual([title["title"] for title in opened["results"]], [*[movie.title for movie in movies], book.title])
        self.assertEqual(opened["results"][0]["ref"]["media_id"], movies[0].media_id)
        narrowed = rendered(client.get(f"/api/v1/planning/{row['object']['id']}/", {"media_type": "movie"}))
        self.assertEqual(narrowed["count"], 3)
        self.assertEqual(narrowed["summary"], "added 3 movies to Planning")

    def test_the_same_access_as_the_profile(self):
        self.add(2)
        row = self.planning(self.home())[0]
        self.user.profile_private = True
        self.user.save()
        stranger = self.stranger_client()
        self.assertEqual(stranger.get(f"/api/v1/planning/{row['object']['id']}/").status_code, 404)
        client = APIClient()
        client.force_authenticate(self.friend)
        self.assertEqual(client.get(f"/api/v1/planning/{row['object']['id']}/").status_code, 200)

    def stranger_client(self):
        from app.tests.tv_fixtures import make_user

        client = APIClient()
        client.force_authenticate(make_user(username="stranger", calendar=NEW_YORK))
        return client

    def test_a_day_with_nothing_or_a_bad_id_is_404(self):
        client = APIClient()
        client.force_authenticate(self.friend)
        from api.services import activity_planning

        empty = activity_planning.address(self.user.pk, date(2026, 10, 25))
        self.assertEqual(client.get(f"/api/v1/planning/{empty}/").status_code, 404)
        self.assertEqual(client.get("/api/v1/planning/5/").status_code, 404)
        self.assertEqual(client.get(f"/api/v1/planning/{(self.user.pk << 44) | (7 << 20) | 5}/").status_code, 404)
        self.assertEqual(client.get(f"/api/v1/planning/{(self.user.pk << 44) | 5000000}/").status_code, 404)
        self.assertEqual(client.get(f"/api/v1/planning/{(999999 << 44) | date(2026, 10, 25).toordinal()}/").status_code, 404)

    def test_it_needs_a_signed_in_viewer(self):
        self.add(2)
        row = self.planning(self.home())[0]
        self.assertIn(APIClient().get(f"/api/v1/planning/{row['object']['id']}/").status_code, (401, 403))


class NoNumberMovesTests(PlanningRowBase):
    """Planning adds change no number, and no family's calculation reads them."""

    def stats(self):
        return self.everything()

    def test_stats_are_byte_identical_around_adds(self):
        book = self.item("book", total_pages=300)
        book_tracking.start(self.user, book)
        book_tracking.update_progress(self.user, book, progress_type="pages", value=20)
        before = self.stats()
        self.add(2, "movie")
        self.add(1, "book")
        self.add(1, "game")
        self.add(1, "manga")
        self.add(1, "comic")
        self.add(1, "music")
        self.maxDiff = None
        self.assertEqual(before, self.stats())

    def test_a_removed_planning_add_changes_nothing_either(self):
        self.add(1, "movie")
        before = self.stats()
        ProgressChange.objects.filter(current_progress__kind="planning").delete()
        self.assertEqual(before, self.stats())

    def test_every_family_ignores_a_planning_row_on_a_title_in_progress(self):
        book = self.item("book", total_pages=300)
        book_tracking.start(self.user, book)
        book_tracking.update_progress(self.user, book, progress_type="pages", value=20)
        game = self.item("game")
        game_tracking.start(self.user, game)
        game_tracking.update_progress(self.user, game, total_minutes=90)
        manga = self.item("manga", series_total=100)
        series_tracking.start(self.user, manga)
        series_tracking.update_progress(self.user, manga, value=4)
        comic = self.item("comic", series_total=100)
        series_tracking.start(self.user, comic)
        series_tracking.update_progress(self.user, comic, value=2)
        tv = FakeTVProvider().__enter__()
        self.addCleanup(tv.__exit__, None, None, None)
        show = make_show(tv, title="Dark", seasons={1: 3})
        tv_tracking.tick(self.user, show, 1, 1)
        movie = self.item("movie", runtime_minutes=120)
        single_weight.apply_tracking_state(self.user, movie, status=Status.COMPLETED.value)
        titles = [book, game, manga, comic, show, movie]

        def calculation():
            result = activity_rows.rows_for([self.user], self.user)
            return result.rows, result.piles

        before = calculation()
        self.assertTrue(before[0])
        for title in titles:
            ProgressChange.record_planning(self.user, title)
        self.assertEqual(before, calculation())


class QueryCostTests(PlanningRowBase):
    """Planning rows are a handful of queries however many titles a day holds."""

    def count(self, params):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        client = APIClient(HTTP_X_SPINE_TIMEZONE=NEW_YORK)
        client.force_authenticate(self.friend)
        with CaptureQueriesContext(connection) as queries:
            response = client.get("/api/v1/feed/", params)
        self.assertEqual(response.status_code, 200)
        return len(queries)

    def test_the_extra_queries_do_not_grow_with_the_titles(self):
        self.add(2, "movie")
        small = self.count(ASK) - self.count({})
        self.add(8, "movie")
        self.add(5, "book")
        self.add(5, "game")
        large = self.count(ASK) - self.count({})
        # One for the day's rows, one per type to check they're still tracked, and the
        # row's own people and artwork: not one per title.
        self.assertLessEqual(small, 6)
        self.assertLessEqual(large - small, 3)
