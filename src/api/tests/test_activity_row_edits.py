"""AC-9: fixing an older row and taking back the newest (decisions 52 and 58).

Book P: 400 pages, started Nov 1 2025, 100 on Nov 2, 120 on Dec 20, 200 on Dec 27. The
same on a manga, a comic (counts) and a game (minutes). The dates are last year's on
purpose, so these work on any day.
"""

from datetime import date

from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from rest_framework.test import APIClient

from api.services import activity_rows, stats_cache
from api.tests.test_activity_status_rows import NEW_YORK, StatusRowBase
from app import book_tracking, game_tracking, series_tracking
from app.models import Book, Comic, GameSession, Manga, MediaTypes, Status
from app.tests.tv_fixtures import make_user
from social.models import (
    Comment,
    ContentLike,
    ProgressChange,
    ProgressPost,
    SocialAuditLog,
)

NOV_1 = date(2025, 11, 1)
NOV_2 = date(2025, 11, 2)
DEC_20 = date(2025, 12, 20)
DEC_27 = date(2025, 12, 27)
JAN_5 = date(2026, 1, 5)
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "row-edits"}}


class Family:
    """How one type is saved and read back."""

    def __init__(self, media_type, fields, body, place):
        """Say a type's title fields, a save's body and where its place is read."""
        self.media_type = media_type
        self.fields = fields
        self.body = body
        self.place = place


def _place_book(item):
    return Book.objects.get(item=item).current_session.pages_read


def _place_series(model):
    return lambda item: model.objects.get(item=item).current_attempt.place


FAMILIES = {
    "book": Family(
        MediaTypes.BOOK.value,
        {"total_pages": 400},
        lambda value, day: {"progress_type": "pages", "value": value, "progressed_on": str(day)},
        _place_book,
    ),
    "manga": Family(
        MediaTypes.MANGA.value,
        {"series_total": 1000},
        lambda value, day: {"value": value, "progressed_on": str(day)},
        _place_series(Manga),
    ),
    "comic": Family(
        MediaTypes.COMIC.value,
        {"series_total": 1000},
        lambda value, day: {"value": value, "progressed_on": str(day)},
        _place_series(Comic),
    ),
    "game": Family(
        MediaTypes.GAME.value,
        {},
        lambda value, day: {"total_minutes": value, "progressed_on": str(day)},
        lambda item: GameSession.objects.get(related_game__item=item, status=Status.IN_PROGRESS.value).total_minutes,
    ),
}


class RowEditBase(StatusRowBase):
    """A reader with a friend following, a stranger and an admin."""

    def setUp(self):
        super().setUp()
        self.stranger = make_user(username="stranger", calendar=NEW_YORK)
        self.admin = make_user(username="admin", calendar=NEW_YORK, is_staff=True)

    def api(self, user):
        client = APIClient(HTTP_X_SPINE_TIMEZONE=NEW_YORK)
        if user is not None:
            client.force_authenticate(user)
        return client

    def title(self, family):
        return self.item(family.media_type, **family.fields)

    def begin(self, item, day=NOV_1):
        if item.media_type == MediaTypes.BOOK.value:
            book_tracking.start(self.user, item, start_date=day)
        elif item.media_type == MediaTypes.GAME.value:
            game_tracking.start(self.user, item, start_date=day)
        else:
            series_tracking.start(self.user, item, start_date=day)

    def save(self, family, item, value, day, user=None):
        path = f"/api/v1/tracking/{item.source}/{item.media_type}/{item.media_id}/progress/"
        response = self.api(user or self.user).post(path, family.body(value, day), format="json")
        self.assertEqual(response.status_code, 200, response.content)

    def sheet(self, family, saves=((100, NOV_2), (120, DEC_20), (200, DEC_27))):
        """Return a title started Nov 1 2025 with these saves made in order."""
        item = self.title(family)
        self.begin(item)
        for value, day in saves:
            self.save(family, item, value, day)
        return item

    def rows(self, item, viewer=None):
        """Return the profile list's progress rows of one title, by day."""
        page = self.get("/api/v1/users/reader/activity/", user=viewer or self.user)
        return {
            row["consumed_at"]: row
            for row in page["results"]
            if row["type"] == "progress_updated" and row["media"]["ref"]["media_id"] == item.media_id
        }

    def told(self, item):
        """Return what each day's row says it was told: {day: number}."""
        rows = activity_rows.rows_for([self.user], self.user, titles=[item.pk], piles=False).rows
        return {str(row.day): int(row.told) for row in rows if not row.log_id and row.told}

    def page(self, row, viewer=None, method="get", **body):
        client = self.api(viewer or self.user)
        return getattr(client, method)(f"/api/v1/progress/{row['object']['id']}/", body or None, format="json")

    def year(self, item, year, user=None):
        client = self.api(user or self.user)
        response = client.get(
            "/api/v1/stats/me/summary/",
            {"schema": "2", "start_date": f"{year}-01-01", "end_date": f"{year}-12-31", "media_type": item.media_type},
        )
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.json()["time"]["rows"]
        return next((row["value"] for row in rows if row["media_type"] == item.media_type), 0)

    def like(self, row, viewer=None):
        response = self.api(viewer or self.friend).post(f"/api/v1/progress/{row['object']['id']}/like/")
        self.assertEqual(response.status_code, 200, response.content)


class DeleteTests(RowEditBase):
    """The newest row goes, with its likes, and the place goes back."""

    def test_the_owner_takes_back_the_newest_row_on_each_type(self):
        for name, family in FAMILIES.items():
            with self.subTest(name):
                item = self.sheet(family, ((100, NOV_2), (120, DEC_20)))
                row = self.rows(item)["2025-12-20"]
                self.like(row)
                self.assertEqual(self.rows(item)["2025-12-20"]["like_count"], 1)
                response = self.page(row, method="delete")
                self.assertEqual(response.status_code, 204, response.content)
                self.assertEqual(self.told(item), {"2025-11-02": 100})
                self.assertEqual(family.place(item), 100)
                self.assertEqual(self.year(item, 2025), 100)
                self.assertNotIn("2025-12-20", self.rows(item))
                # The row's page, its like and its post are gone.
                self.assertEqual(self.page(row).status_code, 404)
                self.assertFalse(ProgressPost.objects.filter(actor=self.user, day=DEC_20).exists())
                self.assertFalse(ContentLike.objects.filter(target_type=ContentLike.PROGRESS_POST).exists())
                # Saving it again is a new row, with no likes: deleted on purpose.
                self.save(family, item, 120, DEC_20)
                again = self.rows(item)["2025-12-20"]
                self.assertEqual((again["like_count"], again["comment_count"]), (0, 0))
                self.assertEqual(self.told(item), {"2025-11-02": 100, "2025-12-20": 20})
                ProgressChange.objects.all().delete()

    def test_a_delete_with_comments_removes_them_too(self):
        family = FAMILIES["book"]
        item = self.sheet(family, ((100, NOV_2), (120, DEC_20)))
        row = self.rows(item)["2025-12-20"]
        self.api(self.friend).post(f"/api/v1/progress/{row['object']['id']}/comments/", {"body": "nice"}, format="json")
        self.assertEqual(Comment.objects.count(), 1)
        self.assertEqual(self.page(row, method="delete").status_code, 204)
        self.assertEqual(Comment.objects.count(), 0)

    def test_the_first_save_goes_back_to_unknown(self):
        for name, family in FAMILIES.items():
            with self.subTest(name):
                item = self.sheet(family, ((100, NOV_2),))
                self.assertEqual(self.page(self.rows(item)["2025-11-02"], method="delete").status_code, 204)
                self.assertEqual(self.told(item), {})
                self.assertIn(family.place(item), (None, 0))
                # The read is still going, and reads on from nothing.
                self.save(family, item, 30, NOV_2)
                self.assertEqual(self.told(item), {"2025-11-02": 30})
                ProgressChange.objects.all().delete()

    def test_it_keeps_the_days_start_and_the_row_stays_as_started(self):
        family = FAMILIES["book"]
        item = self.title(family)
        self.begin(item, date(2026, 10, 25))
        self.save(family, item, 20, date(2026, 10, 25))
        row = self.rows(item)["2026-10-25"]
        self.assertEqual(row["object"]["summary"], "read 20 pages")
        self.assertEqual(self.page(row, method="delete").status_code, 204)
        left = self.rows(item)["2026-10-25"]
        self.assertEqual(left["object"]["summary"], "started reading")
        self.assertTrue(ProgressChange.objects.filter(item=item, current_progress__kind="start").exists())
        self.assertEqual(_place_book(item), None)

    def test_a_game_gives_back_both_numbers(self):
        item = self.item(MediaTypes.GAME.value)
        game_tracking.start(self.user, item, start_date=NOV_1)
        path = f"/api/v1/tracking/{item.source}/game/{item.media_id}/progress/"
        client = self.api(self.user)
        client.post(path, {"total_minutes": 60, "percentage": 10, "progressed_on": str(NOV_2)}, format="json")
        client.post(path, {"total_minutes": 90, "percentage": 25, "progressed_on": str(DEC_20)}, format="json")
        self.assertEqual(self.page(self.rows(item)["2025-12-20"], method="delete").status_code, 204)
        session = GameSession.objects.get(related_game__item=item)
        self.assertEqual((session.total_minutes, session.completion), (60, 10))
        self.assertEqual((session.minutes_updated_on, session.percentage_updated_on), (NOV_2, NOV_2))
        self.assertEqual(session.first_progress_on, NOV_2)

    def test_only_the_newest_row_can_be_deleted(self):
        for name, family in FAMILIES.items():
            with self.subTest(name):
                item = self.sheet(family)
                rows = self.rows(item)
                for day in ("2025-11-02", "2025-12-20"):
                    response = self.page(rows[day], method="delete")
                    self.assertEqual(response.status_code, 409, (name, day))
                self.assertEqual(self.told(item), {"2025-11-02": 100, "2025-12-20": 20, "2025-12-27": 80})
                self.assertEqual(family.place(item), 200)
                ProgressChange.objects.all().delete()

    def test_a_save_from_another_device_since_the_page_loaded_is_a_409(self):
        family = FAMILIES["book"]
        item = self.sheet(family, ((100, NOV_2), (120, DEC_20)))
        row = self.rows(item)["2025-12-20"]
        self.save(family, item, 150, DEC_27)
        self.assertEqual(self.page(row, method="delete").status_code, 409)
        self.assertEqual(_place_book(item), 150)

    def test_a_save_dated_earlier_but_made_last_is_the_newest(self):
        # Only games still take an earlier-dated save (decision 66 stops the rest).
        family = FAMILIES["game"]
        item = self.sheet(family, ((200, DEC_27), (250, DEC_20)))
        rows = self.rows(item)
        self.assertEqual(self.page(rows["2025-12-27"], method="delete").status_code, 409)
        self.assertEqual(self.page(rows["2025-12-20"], method="delete").status_code, 204)
        self.assertEqual(family.place(item), 200)
        self.assertEqual(self.told(item), {"2025-12-27": 200})

    def test_a_same_number_resave_on_a_later_day_does_not_stop_the_delete(self):
        family = FAMILIES["game"]
        item = self.sheet(family, ((30, DEC_20), (60, DEC_20), (60, DEC_27)))
        row = self.rows(item)["2025-12-20"]
        self.assertTrue(self.page(row).json()["viewer"]["can_delete"])
        self.assertEqual(self.page(row, method="delete").status_code, 204)
        self.assertIsNone(family.place(item))
        self.assertFalse(ProgressChange.objects.filter(item=item, current_progress__kind="minutes").exists())

    def test_who_may(self):
        family = FAMILIES["book"]
        item = self.sheet(family, ((100, NOV_2), (120, DEC_20)))
        row = self.rows(item)["2025-12-20"]
        self.assertEqual(self.page(row, viewer=self.friend, method="delete").status_code, 403)
        self.assertEqual(self.page(row, viewer=self.stranger, method="delete").status_code, 403)
        self.assertEqual(self.api(None).delete(f"/api/v1/progress/{row['object']['id']}/").status_code, 401)
        self.assertEqual(self.told(item), {"2025-11-02": 100, "2025-12-20": 20})
        response = self.page(row, viewer=self.admin, method="delete")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(self.told(item), {"2025-11-02": 100})
        audit = SocialAuditLog.objects.get(action="progress_delete")
        self.assertEqual((audit.actor, audit.target_user), (self.admin, self.user))
        self.assertEqual(audit.metadata["day"], "2025-12-20")
        self.assertEqual(audit.metadata["item_id"], item.pk)

    def test_the_owner_leaves_no_privileged_audit_row(self):
        item = self.sheet(FAMILIES["book"], ((100, NOV_2), (120, DEC_20)))
        self.page(self.rows(item)["2025-12-20"], method="delete")
        self.assertFalse(SocialAuditLog.objects.filter(action="progress_delete").exists())

    def test_someone_who_cannot_see_the_row_gets_404_not_403(self):
        item = self.sheet(FAMILIES["book"], ((100, NOV_2), (120, DEC_20)))
        row = self.rows(item)["2025-12-20"]
        self.user.profile_private = True
        self.user.save()
        self.assertEqual(self.page(row, viewer=self.stranger, method="delete").status_code, 404)
        self.assertEqual(self.page(row, viewer=self.stranger, method="patch", value=110).status_code, 404)
        # An admin can still take it back.
        self.assertEqual(self.page(row, viewer=self.admin, method="delete").status_code, 204)

    def test_a_tv_and_an_anime_row_have_nothing_to_delete(self):
        from app import tv_tracking
        from app.tests.tv_fixtures import FakeTVProvider, make_show

        with FakeTVProvider() as fake:
            show = make_show(fake, title="Dark", seasons={1: 3})
            tv_tracking.tick(self.user, show, 1, 1)
        row = next(row for row in self.rows(show).values())
        self.assertEqual(self.page(row, method="delete").status_code, 400)
        self.assertEqual(self.page(row, method="patch", value=1).status_code, 400)
        self.assertEqual(self.page(row).json()["viewer"], {"can_view": True, "has_liked": False, "can_edit": False, "can_delete": False, "edit": None})

    def test_a_finished_or_stopped_read_has_no_controls(self):
        family = FAMILIES["book"]
        finished = self.sheet(family, ((100, NOV_2), (120, DEC_20)))
        stopped = self.sheet(family, ((100, NOV_2), (120, DEC_20)))
        book_tracking.complete(self.user, finished, completion_date=date(2025, 12, 28))
        book_tracking.drop(self.user, stopped)
        for item in (finished, stopped):
            for row in self.rows(item).values():
                viewer = self.page(row).json()["viewer"]
                self.assertEqual((viewer["can_edit"], viewer["can_delete"]), (False, False))
                self.assertEqual(self.page(row, method="delete").status_code, 400)
                self.assertEqual(self.page(row, method="patch", value=105).status_code, 400)

    def test_a_paused_read_can_be_fixed(self):
        family = FAMILIES["book"]
        item = self.sheet(family, ((100, NOV_2), (120, DEC_20)))
        book_tracking.pause(self.user, item)
        row = self.rows(item)["2025-12-20"]
        self.assertEqual(self.page(row, method="delete").status_code, 204)
        self.assertEqual(_place_book(item), 100)


class FixTests(RowEditBase):
    """An older row is rewritten in place and the place stays (decision 58)."""

    def test_the_second_save_fixed_to_110_reads_100_10_90_and_the_place_holds(self):
        for name, family in FAMILIES.items():
            with self.subTest(name):
                item = self.sheet(family)
                row = self.rows(item)["2025-12-20"]
                response = self.page(row, method="patch", value=110)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(self.told(item), {"2025-11-02": 100, "2025-12-20": 10, "2025-12-27": 90})
                self.assertEqual(family.place(item), 200)
                self.assertRegex(response.json()["object"]["summary"], r"\b10")
                ProgressChange.objects.all().delete()

    def test_the_next_saves_from_moves_with_it_and_nothing_else(self):
        family = FAMILIES["book"]
        item = self.sheet(family)
        self.page(self.rows(item)["2025-12-20"], method="patch", value=110)
        changes = list(ProgressChange.objects.filter(item=item, current_progress__kind="pages").order_by("id"))
        self.assertEqual([c.previous_progress["value"] for c in changes], [0, 100, 110])
        self.assertEqual([c.current_progress["value"] for c in changes], [100, 110, 200])
        book = Book.objects.get(item=item)
        self.assertEqual(book.current_session.progressed_on, DEC_27)

    def test_a_row_is_not_fixable_when_the_next_save_made_belongs_to_an_earlier_day(self):
        family = FAMILIES["game"]
        item = self.sheet(family, ((100, DEC_20), (200, DEC_27), (210, DEC_20)))
        before = self.told(item)
        row = self.rows(item)["2025-12-27"]
        self.assertFalse(self.page(row).json()["viewer"]["can_edit"])
        self.assertEqual(self.page(row, method="patch", value=130).status_code, 400)
        self.assertEqual(self.told(item), before)

    def test_the_number_stays_between_the_saves_either_side(self):
        for name, family in FAMILIES.items():
            with self.subTest(name):
                item = self.sheet(family)
                row = self.rows(item)["2025-12-20"]
                for value in (99, 201, 999999):
                    self.assertEqual(self.page(row, method="patch", value=value).status_code, 400, (name, value))
                self.assertEqual(self.told(item), {"2025-11-02": 100, "2025-12-20": 20, "2025-12-27": 80})
                # The edges are fine.
                self.assertEqual(self.page(row, method="patch", value=200).status_code, 200)
                self.assertEqual(self.told(item), {"2025-11-02": 100, "2025-12-20": 100})
                ProgressChange.objects.all().delete()

    def test_fixing_to_the_number_before_empties_the_day_and_drops_its_post(self):
        family = FAMILIES["book"]
        item = self.sheet(family)
        row = self.rows(item)["2025-12-20"]
        self.like(row)
        response = self.page(row, method="patch", value=100)
        self.assertEqual(response.status_code, 204)
        self.assertNotIn("2025-12-20", self.rows(item))
        self.assertFalse(ProgressPost.objects.filter(day=DEC_20).exists())
        self.assertEqual(self.told(item), {"2025-11-02": 100, "2025-12-27": 100})

    def test_bad_numbers_are_400(self):
        family = FAMILIES["book"]
        item = self.sheet(family)
        row = self.rows(item)["2025-12-20"]
        for value in ("abc", -5, 110.5, None):
            self.assertEqual(self.page(row, method="patch", value=value).status_code, 400, value)
        self.assertEqual(self.page(row, method="patch").status_code, 400)

    def test_the_newest_row_is_fixed_by_saving_again(self):
        family = FAMILIES["book"]
        item = self.sheet(family, ((100, NOV_2), (120, DEC_20)))
        row = self.rows(item)["2025-12-20"]
        response = self.page(row, method="patch", value=110)
        self.assertEqual(response.status_code, 400)
        # What the app does: save again with the row's date. A drop comes off newest.
        self.save(family, item, 110, DEC_20)
        self.assertEqual(self.told(item), {"2025-11-02": 100, "2025-12-20": 10})
        self.assertEqual(_place_book(item), 110)

    def test_who_may(self):
        family = FAMILIES["book"]
        item = self.sheet(family)
        row = self.rows(item)["2025-12-20"]
        for viewer in (self.friend, self.stranger, self.admin):
            self.assertEqual(self.page(row, viewer=viewer, method="patch", value=110).status_code, 403, viewer.username)
        self.assertEqual(self.api(None).patch(f"/api/v1/progress/{row['object']['id']}/", {"value": 110}, format="json").status_code, 401)
        self.assertEqual(self.told(item), {"2025-11-02": 100, "2025-12-20": 20, "2025-12-27": 80})

    def test_a_game_edits_the_number_the_row_tells(self):
        item = self.item(MediaTypes.GAME.value)
        game_tracking.start(self.user, item, start_date=NOV_1)
        path = f"/api/v1/tracking/{item.source}/game/{item.media_id}/progress/"
        client = self.api(self.user)
        for minutes, percent, day in ((60, 10, NOV_2), (90, 25, DEC_20), (150, 60, DEC_27)):
            client.post(path, {"total_minutes": minutes, "percentage": percent, "progressed_on": str(day)}, format="json")
        row = self.rows(item)["2025-12-20"]
        page = self.page(row).json()["viewer"]
        self.assertEqual((page["edit"]["unit"], page["edit"]["min"], page["edit"]["max"]), ("minute", 60, 150))
        self.assertEqual(self.page(row, method="patch", value=75).status_code, 200)
        # The percent is a number of its own, with its own saves either side.
        self.assertEqual(self.page(row, method="patch", value=75, unit="percent").status_code, 400)
        self.assertEqual(self.page(row, method="patch", value=40, unit="percent").status_code, 200)
        minutes = ProgressChange.objects.filter(item=item, current_progress__kind="minutes").order_by("id")
        percent = ProgressChange.objects.filter(item=item, current_progress__kind="percentage").order_by("id")
        self.assertEqual([c.current_progress["value"] for c in minutes], [60, 75, 150])
        self.assertEqual([c.current_progress["value"] for c in percent], [10, 40, 60])
        self.assertEqual([c.previous_progress["value"] for c in percent], [0, 10, 40])
        self.assertEqual(self.page(row, method="patch", value=40, unit="nope").status_code, 400)

    def test_a_percent_may_have_two_decimals_and_a_page_may_not_be_fractional(self):
        item = self.item(MediaTypes.BOOK.value)
        book_tracking.start(self.user, item, start_date=NOV_1)
        client = self.api(self.user)
        path = f"/api/v1/tracking/{item.source}/book/{item.media_id}/progress/"
        for value, day in ((10, NOV_2), (20, DEC_20), (30, DEC_27)):
            client.post(path, {"progress_type": "percentage", "value": value, "progressed_on": str(day)}, format="json")
        row = self.rows(item)["2025-12-20"]
        self.assertEqual(self.page(row, method="patch", value=15.25).status_code, 200)
        self.assertEqual(self.page(row, method="patch", value=15.255).status_code, 400)


class ViewerFieldsTests(RowEditBase):
    """The post page tells the owner what can be done, and an admin the delete."""

    def test_the_newest_row_says_to_save_again_and_an_older_to_send_a_number(self):
        family = FAMILIES["book"]
        item = self.sheet(family)
        rows = self.rows(item)
        newest = self.page(rows["2025-12-27"]).json()["viewer"]
        self.assertEqual(
            newest,
            {
                "can_view": True,
                "has_liked": False,
                "can_edit": True,
                "can_delete": True,
                "edit": {
                    "mode": "save",
                    "value": 200,
                    "unit": "page",
                    "kind": "pages",
                    "date": "2025-12-27",
                    "min": None,
                    "max": 400,
                },
            },
        )
        older = self.page(rows["2025-12-20"]).json()["viewer"]
        self.assertEqual((older["can_edit"], older["can_delete"]), (True, False))
        self.assertEqual(
            older["edit"],
            {"mode": "number", "value": 120, "unit": "page", "kind": "pages", "date": "2025-12-20", "min": 100, "max": 200},
        )

    def test_the_feed_and_lists_carry_none_of_it(self):
        item = self.sheet(FAMILIES["book"])
        row = self.rows(item)["2025-12-27"]
        self.assertEqual(set(row["viewer"]), {"can_view", "has_liked"})
        feed = self.get("/api/v1/feed/", user=self.friend)["results"]
        self.assertTrue(feed)
        self.assertTrue(all(set(entry["viewer"]) == {"can_view", "has_liked"} for entry in feed))

    def test_a_follower_sees_the_plain_page_and_an_admin_only_the_delete(self):
        item = self.sheet(FAMILIES["book"])
        row = self.rows(item)["2025-12-27"]
        self.assertEqual(set(self.page(row, viewer=self.friend).json()["viewer"]), {"can_view", "has_liked"})
        admin = self.page(row, viewer=self.admin).json()["viewer"]
        self.assertEqual((admin["can_edit"], admin["can_delete"], admin["edit"]), (False, True, None))

    def test_a_percent_save_and_a_manga_say_their_units(self):
        manga = self.sheet(FAMILIES["manga"])
        rows = self.rows(manga)
        edit = self.page(rows["2025-12-20"]).json()["viewer"]["edit"]
        self.assertEqual((edit["unit"], edit["kind"], edit["min"], edit["max"]), ("chapter", "count", 100, 200))
        newest = self.page(rows["2025-12-27"]).json()["viewer"]["edit"]
        self.assertEqual((newest["mode"], newest["max"]), ("save", 1000))
        comic = self.sheet(FAMILIES["comic"])
        self.assertEqual(self.page(self.rows(comic)["2025-12-27"]).json()["viewer"]["edit"]["unit"], "issue")

    def test_a_row_with_only_a_start_has_no_controls(self):
        item = self.title(FAMILIES["book"])
        self.begin(item, date(2026, 10, 25))
        row = self.rows(item)["2026-10-25"]
        viewer = self.page(row).json()["viewer"]
        self.assertEqual((viewer["can_edit"], viewer["can_delete"], viewer["edit"]), (False, False, None))
        self.assertEqual(self.page(row, method="delete").status_code, 400)


@override_settings(TIME_ZONE=NEW_YORK, CACHES=LOCMEM, STATS_CACHE_ENABLED=True)
class StatsMoveTests(RowEditBase):
    """Stats read the new number at once, with no pull."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        setattr(connection, stats_cache.PENDING_ATTR, (None, None))

    def test_a_delete_and_a_fix_move_the_years(self):
        family = FAMILIES["book"]
        with self.captureOnCommitCallbacks(execute=True):
            item = self.sheet(family, ((100, NOV_2), (120, DEC_20), (200, JAN_5)))
        row = self.rows(item)["2025-12-20"]
        self.assertEqual((self.year(item, 2025), self.year(item, 2026)), (120, 80))
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.page(row, method="patch", value=110).status_code, 200)
        self.assertEqual((self.year(item, 2025), self.year(item, 2026)), (110, 90))
        newest = self.rows(item)["2026-01-05"]
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.page(newest, method="delete").status_code, 204)
        self.assertEqual((self.year(item, 2025), self.year(item, 2026)), (110, 0))
