"""The Activity list and the feed on the calculation's rows (Phase 4, plan 5.11).

A list always adds up to the number that opened it, for every viewer. Home, the profile
list and the post page read the same rows, so a day's likes and comments follow its row:
they hide while the day has none, and come back with it.
"""

from datetime import UTC, date, datetime
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from api.serializers.auth import token_response
from api.services import activity_rows, progress_posts
from api.tests.feed_fixtures import address_of, show_with_episodes, tick
from api.tests.test_activity_calc_books import BookCalcBase, d
from api.tests.test_stats_v2_activity import ALL_TIME, YEAR_2026
from app import book_tracking, game_tracking, tv_tracking
from app.models import DiaryEntry, Item, MediaTypes
from app.tests.tv_fixtures import make_user
from social.models import (
    Block,
    Comment,
    ContentLike,
    Follow,
    FollowStatus,
    ProgressPost,
)

LIST = "/api/v1/users/owner/activity/"
YEAR = {"start_date": "2026-01-01", "end_date": "2026-12-31"}
TOKYO = "Asia/Tokyo"


def at(day, hour=12):
    """Return a UTC moment in 2026, for the tick helper."""
    return datetime(2026, day.month, day.day, hour, tzinfo=UTC)


@override_settings(TIME_ZONE="America/New_York")
class WireBase(BookCalcBase):
    """An owner with books and a show, read by a follower, a stranger and no one."""

    def setUp(self):
        super().setUp()
        self.show = show_with_episodes("Severance", seasons={1: 8})

    def anonymous(self):
        return APIClient(HTTP_X_SPINE_TIMEZONE="America/New_York")

    def activity(self, viewer=None, params=None, path=LIST, *, expect=200):
        client = self.client_for(viewer) if viewer else self.anonymous()
        response = client.get(path, params or {})
        self.assertEqual(response.status_code, expect, response.content)
        return response.json() if expect == 200 else response

    def results(self, viewer=None, params=None, **kwargs):
        return self.activity(viewer, params, **kwargs)["results"]

    def pages_to_the_end(self, viewer, params, size=2):
        """Return every item of a list read a few at a time, and its pages."""
        seen, url, pages = [], LIST, 0
        query = {**params, "page_size": size}
        while url:
            page = self.client_for(viewer).get(url, query if pages == 0 else {})
            self.assertEqual(page.status_code, 200, page.content)
            seen += page.json()["results"]
            url = page.json()["next_cursor"]
            pages += 1
        return seen, pages

    def stats_value(self, viewer, media_type, params):
        """Return a type's number as Stats prints it for this viewer."""
        data = self.summary(params, viewer=viewer, tab=media_type)
        return next(row for row in data["time"]["rows"] if row["media_type"] == media_type)

    def books(self):
        """Three books read on known days in 2026 and one marked read with no date."""
        for name, saves in (
            ("a", [(d(2026, 2, 10), 120)]),
            ("b", [(d(2026, 3, 1), 50), (d(2026, 3, 5), 150)]),
            ("c", [(d(2026, 5, 2), 100)]),
        ):
            item = self.book(name)
            self.begin(item, saves[0][0])
            for day, pages in saves:
                self.save(item, pages, day)
        marked = self.book("marked", pages=350)
        self.do(book_tracking.mark_read, marked, d(2026, 6, 1))

    def tick_show(self, episodes, day, hour=12):
        tick(self.owner, self.show, episodes, at(day, hour))


class ListEqualsNumberTests(WireBase):
    """Decision 18: the rows sum to the number, for every viewer who can see them."""

    def test_pages_for_the_owner_a_follower_and_a_stranger(self):
        self.books()
        for viewer in (self.owner, self.follower, self.stranger):
            with self.subTest(viewer=viewer.username):
                page = self.activity(viewer, {**YEAR, "media_type": "book"})
                rows = [row for row in page["results"] if row["type"] == "progress_updated"]
                pages = sum(row["object"]["amount"] for row in rows)
                self.assertEqual(pages, 370)
                self.assertEqual(page["top_line"], "370 pages")
                self.assertEqual(page["total"]["value"], 370)
                self.assertEqual(page["undated"], None)
                self.assertEqual(self.stats_value(viewer, "book", YEAR_2026)["value"], 370)

    def test_all_time_adds_the_undated_pile_and_says_so(self):
        self.books()
        page = self.activity(self.follower, {"media_type": "book"})
        rows = [row for row in page["results"] if row["type"] == "progress_updated"]
        self.assertEqual(sum(row["object"]["amount"] for row in rows), 370)
        self.assertEqual(page["top_line"], "720 pages")
        self.assertEqual(page["total"]["value"], 720)
        self.assertEqual(page["undated"]["amount"], 350)
        self.assertEqual(page["undated"]["titles"], 1)
        self.assertEqual(page["undated"]["label"], "Includes 1 book and 350 pages with no date")
        # Rows plus the line are the number Stats prints.
        self.assertEqual(self.stats_value(self.owner, "book", ALL_TIME)["value"], 720)

    def test_titles_gives_one_row_per_title_on_its_newest_day_across_a_page_break(self):
        self.books()
        seen, pages = self.pages_to_the_end(self.follower, {**YEAR, "media_type": "book", "counts": "titles"})
        self.assertGreater(pages, 1)
        titles = [(row["media"]["title"], row["consumed_at"]) for row in seen]
        self.assertEqual(titles, [("c", "2026-05-02"), ("b", "2026-03-05"), ("a", "2026-02-10")])
        top = self.activity(self.follower, {**YEAR, "media_type": "book", "counts": "titles"})["top_line"]
        self.assertEqual(top, "3 books")

    def test_all_time_titles_count_the_pile_titles_with_no_row(self):
        self.books()
        page = self.activity(self.follower, {"media_type": "book", "counts": "titles"})
        self.assertEqual(len(page["results"]), 3)
        self.assertEqual(page["top_line"], "4 books")
        self.assertEqual(page["undated"]["label"], "Includes 1 book with no date")

    def test_a_measure_lists_every_days_row(self):
        self.books()
        rows = self.results(self.follower, {**YEAR, "media_type": "book"})
        self.assertEqual(
            [(row["consumed_at"], row["object"]["amount"]) for row in rows],
            [("2026-05-02", 100), ("2026-03-05", 100), ("2026-03-01", 50), ("2026-02-10", 120)],
        )

    def test_global_titles_has_one_row_per_title_of_any_type(self):
        self.books()
        self.tick_show([1, 2], date(2026, 4, 1))
        page = self.activity(self.follower, YEAR | {"counts": "titles"})
        self.assertEqual(page["top_line"], "4 titles")
        self.assertEqual(len(page["results"]), 4)
        self.assertEqual(page["total"], None)

    def test_minutes_on_the_rows_sum_to_the_total(self):
        self.tick_show([1, 2], date(2026, 4, 1))
        self.tick_show([3], date(2026, 4, 2))
        page = self.activity(self.follower, {**YEAR, "media_type": "tv"})
        minutes = sum(row["object"]["amount"] for row in page["results"])
        self.assertEqual(minutes, 135)
        self.assertEqual(page["total"]["value"], 135)
        self.assertEqual(page["total"]["time_label"], "2h 15m")
        self.assertEqual(page["top_line"], "2 hours (2h 15m)")
        self.assertEqual(self.stats_value(self.owner, "tv", YEAR_2026)["value"], 135)

    def test_episodes_and_hours_and_active_days_are_their_own_numbers(self):
        self.tick_show([1, 2], date(2026, 4, 1))
        self.tick_show([3], date(2026, 4, 2))
        book = self.book("daily")
        self.begin(book, d(2026, 4, 2))
        self.save(book, 10, d(2026, 4, 2))
        for counts, top in (
            ("episodes", "3 episodes"),
            ("hours", "2 hours (2h 15m)"),
            ("days", "2 active days"),
            ("multi_title_days", "1 day with 2+ titles"),
        ):
            with self.subTest(counts=counts):
                page = self.activity(self.follower, {**YEAR, "counts": counts})
                self.assertEqual(page["top_line"], top)
        weeks = self.activity(self.follower, {**YEAR, "counts": "weeks"})
        self.assertEqual(weeks["top_line"], "1 week")

    def test_days_and_weeks_leave_out_status_only_rows_and_have_no_undated_line(self):
        self.books()
        stopped = self.book("stopped")
        self.begin(stopped, d(2026, 7, 1))
        self.do(book_tracking.pause, stopped, d(2026, 7, 2))
        every = self.results(self.follower, YEAR)
        self.assertIn("paused", [row["object"]["summary"] for row in every])
        for counts in ("days", "weeks", "multi_title_days"):
            with self.subTest(counts=counts):
                page = self.activity(self.follower, {"counts": counts})
                self.assertNotIn("paused", [row["object"]["summary"] for row in page["results"]])
                self.assertIsNone(page["undated"])
        days = self.activity(self.follower, {"counts": "days"})
        self.assertEqual(days["top_line"], "5 active days")

    def test_a_plain_list_has_no_numbers(self):
        self.books()
        page = self.activity(self.follower)
        for key in ("top_line", "total", "undated"):
            self.assertNotIn(key, page)

    def test_a_streak_names_its_range_for_every_viewer(self):
        for day in (1, 2, 3):
            self.tick_show([day], date(2026, 4, day))
        for viewer in (self.owner, self.follower, self.stranger):
            with self.subTest(viewer=viewer.username):
                header = self.summary(YEAR_2026, viewer=viewer, tab="tv")["header"]
                details = {row["key"]: row for row in header["details"]}
                self.assertEqual(details["longest_weekly_streak"]["range"], {"start": "2026-03-30", "end": "2026-04-05"})
                self.assertEqual(header["longest_daily_streak_range"], {"start": "2026-04-01", "end": "2026-04-03"})


class PrivacyTests(WireBase):
    """A viewer reads only the rows of logs they may see."""

    def logs(self):
        self.tick_show([1, 2], date(2026, 4, 1))
        season = Item.objects.get(media_id=self.show.media_id, media_type=MediaTypes.SEASON.value)
        with patch("django.utils.timezone.now", return_value=at(date(2026, 4, 3))):
            tv_tracking.create_log(self.owner, season, consumed_at=date(2026, 4, 3), visibility="followers")

    def test_a_followers_only_log_reaches_followers_in_the_list_and_the_number(self):
        self.logs()
        owner = self.activity(self.owner, {**YEAR, "media_type": "tv"})
        follower = self.activity(self.follower, {**YEAR, "media_type": "tv"})
        stranger = self.activity(self.stranger, {**YEAR, "media_type": "tv"})
        self.assertEqual(owner["total"]["value"], follower["total"]["value"])
        self.assertEqual([row["type"] for row in follower["results"]], ["diary_created", "progress_updated"])
        self.assertEqual([row["type"] for row in stranger["results"]], ["progress_updated"])
        self.assertEqual(stranger["total"]["value"], 90)
        self.assertEqual(follower["total"]["value"], 360)

    def test_a_logged_out_caller_gets_public_rows_and_no_numbers(self):
        self.logs()
        page = self.activity(None, {**YEAR, "media_type": "tv"})
        self.assertEqual([row["type"] for row in page["results"]], ["progress_updated"])
        for key in ("top_line", "total", "undated"):
            self.assertNotIn(key, page)

    def test_a_private_account_and_a_block_show_nothing(self):
        self.books()
        self.owner.profile_private = True
        self.owner.save(update_fields=["profile_private"])
        self.assertEqual(self.activity(self.stranger, YEAR), {"next_cursor": None, "previous_cursor": None, "results": []})
        self.assertEqual(self.activity(None, YEAR)["results"], [])
        self.assertTrue(self.activity(self.follower, YEAR)["results"])
        Block.objects.create(blocker=self.owner, blocked=self.follower)
        self.assertEqual(self.activity(self.follower, YEAR)["results"], [])

    def test_the_feed_shows_a_friends_rows_and_leaves_out_a_private_log(self):
        self.logs()
        home = self.client_for(self.follower).get("/api/v1/feed/").json()["results"]
        self.assertEqual([row["type"] for row in home], ["diary_created", "progress_updated"])
        stranger = Follow.objects.create(from_user=self.stranger, to_user=self.owner, status=FollowStatus.PENDING)
        self.assertEqual(self.client_for(self.stranger).get("/api/v1/feed/").json()["results"], [])
        stranger.delete()


class WhatAListRowSaysTests(WireBase):
    """Decisions 21, 22 and 24: moves show, rows carry no review and no edit control."""

    def test_a_log_row_shows_no_review_on_the_list_but_the_feed_keeps_it(self):
        book = self.book("reviewed")
        _, entry = self.finish(book, d(2026, 3, 1), review="Wrecked me.", review_title="Quiet", contains_spoilers=True)
        row = self.results(self.follower, YEAR)[0]
        self.assertEqual((row["review"], row["review_title"]), ("", ""))
        self.assertEqual(row["id"], entry.pk)
        self.assertEqual(row["object"]["label"], "400 pages")
        feed = self.client_for(self.follower).get("/api/v1/feed/").json()["results"][0]
        self.assertEqual((feed["review"], feed["review_title"]), ("Wrecked me.", "Quiet"))

    def test_a_list_asked_to_be_one_reads_as_one(self):
        book = self.book("reviewed")
        self.finish(book, d(2026, 3, 1), review="Wrecked me.")
        row = self.results(self.follower, {"view": "list"})[0]
        self.assertEqual(row["review"], "")
        self.assertIn("show_title", row["media"])

    def test_stop_and_pick_up_read_as_rows_with_no_label(self):
        book = self.book("moves")
        self.begin(book, d(2026, 3, 1))
        self.save(book, 40, d(2026, 3, 2))
        self.do(book_tracking.pause, book, d(2026, 3, 2))
        self.do(book_tracking.drop, book, d(2026, 3, 4))
        self.do(book_tracking.start, book, d(2026, 3, 6))
        rows = self.results(self.follower)
        self.assertEqual(
            [(row["consumed_at"], row["object"]["summary"], row["object"]["label"], row["object"]["moves"]) for row in rows],
            [
                ("2026-03-06", "picked back up", None, ["picked_back_up"]),
                ("2026-03-04", "stopped", None, ["stopped"]),
                ("2026-03-02", "read 40 pages, paused", "40 pages", ["paused"]),
                ("2026-03-01", "started reading", "Started", []),
            ],
        )

    def test_a_game_dropped_then_played_again_says_started_playing(self):
        game = self.item(MediaTypes.GAME.value, "stopped-game")
        self.do(game_tracking.start, game, d(2026, 3, 1))
        self.do(game_tracking.drop, game, d(2026, 3, 2))
        self.do(game_tracking.start, game, d(2026, 3, 3))
        self.assertEqual(
            [row["object"]["summary"] for row in self.results(self.follower)],
            ["started playing", "stopped", "started playing"],
        )

    def test_nothing_marked_makes_a_row(self):
        marked = self.book("marked", pages=350)
        self.do(book_tracking.mark_read, marked, d(2026, 6, 1))
        film = Item.objects.create(source="manual", media_type="movie", media_id="film", title="Film", runtime_minutes=120)
        from app import single_weight

        with self.on(d(2026, 6, 1)):
            single_weight.mark_consumed(self.owner, film)
        self.assertEqual(self.results(self.follower), [])
        self.assertEqual(self.activity(self.follower, {"media_type": "book"})["undated"]["amount"], 350)


class OneDayEverywhereTests(WireBase):
    """A New York viewer reads a Tokyo friend's late log on the day the friend sees."""

    def test_the_same_day_on_home_and_on_the_friends_list(self):
        tokyo = make_user("tokyo", calendar=TOKYO)
        Follow.objects.create(from_user=self.owner, to_user=tokyo, status=FollowStatus.ACCEPTED)
        game = self.item(MediaTypes.GAME.value, "late")
        # 16:00 UTC on Mar 1 is 01:00 on Mar 2 in Tokyo and 11:00 on Mar 1 in New York.
        DiaryEntry.objects.create(user=tokyo, item=game, consumed_at=datetime(2026, 3, 1, 16, tzinfo=UTC))
        home = self.client_for(self.owner).get("/api/v1/feed/").json()["results"]
        listed = self.client_for(self.owner).get("/api/v1/users/tokyo/activity/").json()["results"]
        self.assertEqual([row["consumed_at"] for row in home], ["2026-03-02"])
        self.assertEqual([row["consumed_at"] for row in listed], ["2026-03-02"])


class ParametersTests(WireBase):
    """What a list is asked for."""

    def test_bad_values_are_400s(self):
        for params in ({"counts": "pages"}, {"start_date": "yesterday"}, {"end_date": "2026-13-01"}):
            with self.subTest(params=params):
                response = self.activity(self.follower, params, expect=400)
                self.assertEqual(response.status_code, 400)

    def test_a_type_the_server_doesnt_expose_is_not_found(self):
        with override_settings(MUSIC_ENABLED=False):
            self.activity(self.follower, {"media_type": "music"}, expect=404)

    def test_season_and_episode_filter_to_the_show_tab(self):
        self.tick_show([1], date(2026, 4, 1))
        self.books()
        for media_type in ("tv", "season", "episode"):
            with self.subTest(media_type=media_type):
                rows = self.results(self.follower, {"media_type": media_type})
                self.assertEqual({row["media"]["ref"]["media_type"] for row in rows}, {"tv"})

    def test_a_cursor_from_before_still_works(self):
        self.books()
        first = self.activity(self.follower, {"page_size": 1})
        import base64
        import json

        cursor = first["next_cursor"].split("cursor=")[1].split("&")[0]
        from urllib.parse import unquote

        data = json.loads(base64.urlsafe_b64decode(unquote(cursor)))
        del data["title"]
        old = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
        page = self.activity(self.follower, {"page_size": 1, "cursor": old})
        self.assertEqual(len(page["results"]), 1)


class PostPagesTests(WireBase):
    """Likes and comments follow the day's row (AC-7, plan 5.8)."""

    def like(self, viewer, address):
        return self.client_for(viewer).post(f"/api/v1/progress/{address}/like/")

    def comment(self, viewer, address):
        return self.client_for(viewer).post(f"/api/v1/progress/{address}/comments/", {"body": "Nice"}, format="json")

    def test_a_shows_day_is_reached_by_its_address_and_by_an_old_change_id(self):
        self.tick_show([1, 2], date(2026, 4, 1))
        row = self.results(self.follower)[0]
        address = row["object"]["id"]
        self.assertEqual(address, address_of(self.owner, self.show, date(2026, 4, 1)))
        self.assertEqual(self.like(self.follower, address).status_code, 200)
        post = ProgressPost.objects.get()
        self.assertEqual((post.actor_id, post.title_key, post.day), (self.owner.pk, f"tv:manual:{self.show.media_id}", date(2026, 4, 1)))
        # A change row from before is an address too: it names the same show and day.
        from social.models import ProgressChange

        change = ProgressChange.objects.filter(actor=self.owner, item__media_type="season").order_by("id").first()
        self.assertEqual(self.client_for(self.follower).get(f"/api/v1/progress/{change.pk}/").json()["like_count"], 1)
        self.assertEqual(self.comment(self.follower, change.pk).status_code, 201)
        self.assertEqual(ProgressPost.objects.count(), 1)

    def test_a_late_tick_dated_that_day_keeps_the_likes_and_comments(self):
        self.tick_show([1], date(2026, 4, 1))
        address = self.results(self.follower)[0]["object"]["id"]
        self.like(self.follower, address)
        self.comment(self.follower, address)
        # Another tick the same day: still one row, still its like and comment.
        self.tick_show([2], date(2026, 4, 1), hour=20)
        row = self.results(self.follower)[0]
        self.assertEqual(row["object"]["summary"], "watched 2 episodes")
        self.assertEqual((row["like_count"], row["comment_count"]), (1, 1))

    def test_a_row_a_fix_emptied_hides_its_likes_until_the_day_has_a_row_again(self):
        item = self.book("emptied")
        self.begin(item, d(2025, 12, 1))
        self.save(item, 100, d(2025, 12, 15))
        self.save(item, 300, d(2026, 1, 15))
        january = next(row for row in self.results(self.follower) if row["consumed_at"] == "2026-01-15")
        address = january["object"]["id"]
        self.like(self.follower, address)
        self.comment(self.follower, address)
        self.save(item, 30, d(2026, 2, 15))
        client = self.client_for(self.follower)
        for response in (
            client.get(f"/api/v1/progress/{address}/"),
            client.post(f"/api/v1/progress/{address}/like/"),
            client.get(f"/api/v1/progress/{address}/comments/"),
            client.post(f"/api/v1/progress/{address}/comments/", {"body": "Hi"}, format="json"),
        ):
            self.assertEqual(response.status_code, 404)
        self.assertEqual((ContentLike.objects.count(), Comment.objects.count(), ProgressPost.objects.count()), (1, 1, 1))
        self.save(item, 330, d(2026, 1, 15))
        self.assertEqual(client.get(f"/api/v1/progress/{address}/").json()["like_count"], 1)

    def test_a_post_a_backdated_log_hid_returns_when_the_log_goes(self):
        item = self.book("backdated")
        self.begin(item, d(2026, 3, 1))
        self.save(item, 100, d(2026, 3, 10))
        address = next(row for row in self.results(self.follower) if row["consumed_at"] == "2026-03-10")["object"]["id"]
        self.like(self.follower, address)
        _, entry = self.finish(item, d(2025, 12, 20), saved_on=d(2026, 3, 20))
        client = self.client_for(self.follower)
        self.assertEqual(client.get(f"/api/v1/progress/{address}/").status_code, 404)
        with self.on(d(2026, 3, 21)):
            book_tracking.delete_completion(self.owner, entry)
        self.assertEqual(client.get(f"/api/v1/progress/{address}/").json()["like_count"], 1)

    def test_a_row_a_private_log_moved_for_the_owner_keeps_its_likes(self):
        """The standing view keeps a tick a backdated log took from the owner."""
        self.tick_show([1], date(2026, 4, 1))
        address = self.results(self.stranger)[0]["object"]["id"]
        self.like(self.stranger, address)
        season = Item.objects.get(media_id=self.show.media_id, media_type=MediaTypes.SEASON.value)
        with patch("django.utils.timezone.now", return_value=at(date(2026, 4, 2))):
            tv_tracking.create_log(self.owner, season, consumed_at=date(2026, 3, 1), visibility="private")
        self.assertEqual(self.client_for(self.owner).get(f"/api/v1/progress/{address}/").status_code, 404)
        self.assertEqual(self.client_for(self.stranger).get(f"/api/v1/progress/{address}/").status_code, 200)
        progress_posts.drop_empty_posts(self.owner.pk, f"tv:manual:{self.show.media_id}", {date(2026, 4, 1)})
        self.assertEqual(ContentLike.objects.count(), 1)

    def test_deleting_a_season_drops_the_post_on_a_day_only_its_ticks_held(self):
        self.tick_show([1], date(2026, 4, 1))
        address = self.results(self.follower)[0]["object"]["id"]
        self.like(self.follower, address)
        # A dated play an import made has no change row: only the viewing holds the day.
        from social.models import ProgressChange

        ProgressChange.objects.filter(actor=self.owner).delete()
        with patch("django.utils.timezone.now", return_value=at(date(2026, 4, 2))):
            tv_tracking.delete_season_tracking(self.owner, self.show, 1)
        self.assertEqual((ProgressPost.objects.count(), ContentLike.objects.count()), (0, 0))

    def test_a_status_only_day_opens_and_takes_a_like(self):
        item = self.book("stopped")
        self.begin(item, d(2026, 3, 1))
        self.do(book_tracking.drop, item, d(2026, 3, 2))
        rows = self.results(self.follower)
        self.assertEqual(rows[0]["object"]["summary"], "stopped")
        address = rows[0]["object"]["id"]
        page = self.client_for(self.follower).get(f"/api/v1/progress/{address}/")
        self.assertEqual(page.status_code, 200)
        self.assertEqual(self.like(self.follower, address).status_code, 200)

    def test_paging_through_status_only_rows_never_raises(self):
        for number in range(4):
            item = self.book(f"stop-{number}")
            self.begin(item, d(2026, 3, 1))
            self.do(book_tracking.drop, item, d(2026, 3, 2 + number))
        seen, pages = self.pages_to_the_end(self.follower, {}, size=1)
        self.assertEqual(len(seen), 8)
        self.assertEqual(len({row["id"] for row in seen}), 8)
        self.assertEqual(pages, 8)

    def test_a_63_bit_address_survives_json_and_decodes(self):
        big = get_user_model().objects.create_user(username="big", id=(1 << 19) - 1, password="strong-password-123")
        Follow.objects.create(from_user=self.follower, to_user=big, status=FollowStatus.ACCEPTED)
        # A show at the largest Item id an address can name.
        media_id = Item.generate_manual_id()
        show = Item.objects.create(id=(1 << 24) - 1, media_id=media_id, source="manual", media_type="tv", title="Large")
        Item.objects.create(media_id=media_id, source="manual", media_type="season", season_number=1, title="Large")
        Item.objects.create(
            media_id=media_id,
            source="manual",
            media_type="episode",
            season_number=1,
            episode_number=1,
            title="One",
        )
        tick(big, show, [1], at(date(2026, 4, 1)))
        row = self.client_for(self.follower).get("/api/v1/users/big/activity/").json()["results"][0]
        address = row["object"]["id"]
        self.assertEqual(row["id"], -address)
        self.assertGreater(address, 1 << 62)
        self.assertLess(address, 1 << 63)
        page = self.client_for(self.follower).get(f"/api/v1/progress/{address}/")
        self.assertEqual(page.status_code, 200, page.content)
        self.assertEqual(page.json()["object"]["id"], address)


class DatesAndBudgetsTests(WireBase):
    """Old dates sit in the past, and Home's work doesn't grow with who is followed."""

    def test_an_old_dated_play_sits_in_the_past_not_on_top(self):
        self.tick_show([1], date(2026, 4, 10))
        season = Item.objects.get(media_id=self.show.media_id, media_type=MediaTypes.SEASON.value)
        episode = Item.objects.get(media_id=self.show.media_id, media_type=MediaTypes.EPISODE.value, episode_number=5)
        from app.models import TV, Episode, Season

        row = Season.objects.get(user=self.owner, item=season)
        self.assertIsInstance(row.related_tv, TV)
        # Written now, dated last week: the day sorts it, not when it was written.
        Episode.objects.bulk_create([Episode(related_season=row, item=episode, watched_on=date(2026, 4, 3), kind="tick")])
        days = [row["consumed_at"] for row in self.results(self.follower)]
        self.assertEqual(days, ["2026-04-10", "2026-04-03"])

    def test_queries_stay_flat_as_titles_and_followed_people_grow(self):
        def home(count_of_books, people):
            for person in range(people):
                friend = make_user(f"friend-{count_of_books}-{person}", calendar="America/New_York")
                Follow.objects.create(from_user=self.stranger, to_user=friend, status=FollowStatus.ACCEPTED)
                for number in range(count_of_books):
                    item = self.book(f"{friend.pk}-{number}")
                    with self.on(d(2026, 3, 1)):
                        book_tracking.start(friend, item)
                        book_tracking.update_progress(friend, item, progress_type="pages", value=10)
            with CaptureQueriesContext(connection) as queries:
                response = self.client_for(self.stranger).get("/api/v1/feed/")
            self.assertEqual(response.status_code, 200)
            return len(queries)

        few = home(1, 1)
        many = home(3, 3)
        self.assertEqual(few, many)


class StandingRowsAgreeWithTheOwnersTests(WireBase):
    """With no log in the way, the standing view is the owner's own rows."""

    def test_they_match_on_a_plain_library(self):
        self.books()
        self.tick_show([1, 2], date(2026, 4, 1))
        owners = {(row.title_key, row.day) for row in activity_rows.rows_for([self.owner], self.owner).rows if not row.log_id}
        standing = {(row.title_key, row.day) for row in activity_rows.standing_rows([self.owner.pk])}
        self.assertEqual(owners, standing)


class ProfileActivityCountTests(WireBase):
    """The profile's Activity row says how many rows its list holds, for each viewer."""

    logs = PrivacyTests.logs

    def count(self, viewer, path="/api/v1/users/owner/"):
        response = self.client_for(viewer).get(path)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["counts"]["activity"]

    def test_the_count_is_the_rows_each_viewer_sees_on_the_list(self):
        self.books()
        self.logs()
        for viewer in (self.owner, self.follower, self.stranger):
            with self.subTest(viewer=viewer.username):
                seen, _pages = self.pages_to_the_end(viewer, {})
                self.assertEqual(self.count(viewer), len(seen))
        self.assertEqual(self.count(self.owner, "/api/v1/me/"), len(self.pages_to_the_end(self.owner, {})[0]))
        # The followers-only log is one row the stranger doesn't get.
        self.assertEqual(self.count(self.follower), self.count(self.stranger) + 1)

    def test_a_mark_adds_nothing_and_a_private_account_counts_zero_for_strangers(self):
        before = self.count(self.owner, "/api/v1/me/")
        self.do(book_tracking.mark_read, self.book("marked only", pages=200), d(2026, 7, 1))
        self.assertEqual(self.count(self.owner, "/api/v1/me/"), before)
        self.books()
        self.owner.profile_private = True
        self.owner.save(update_fields=["profile_private"])
        self.assertGreater(self.count(self.follower), 0)
        self.assertEqual(self.client_for(self.stranger).get("/api/v1/users/owner/").json()["counts"].get("activity", 0), 0)

    def test_sign_in_leaves_the_count_out(self):
        self.books()
        self.assertNotIn("activity", token_response(self.owner)["user"]["counts"])
