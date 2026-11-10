"""What installed builds read stays what it was: keys and types, on rendered JSON.

Both activity endpoints, the tracking detail's latest change, and what old code does
with the rows this ship adds, because a rollback hands them to it.
ActivityWireCompatTests.swift decodes the same shapes as build 61 does.
"""

import json
from datetime import UTC, date, datetime
from functools import reduce
from operator import or_
from unittest import mock

from django.db.models import CharField, FloatField, Max, Min, Q, Sum, Value
from django.db.models.fields.json import KT
from django.db.models.functions import Cast, Concat, TruncDate
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.renderers import JSONRenderer
from rest_framework.test import APIClient

from api.services.activity import UNITS
from app import book_tracking
from app.models import Item, MediaTypes, Sources
from app.tests.tv_fixtures import make_user, patch_now
from social.models import Follow, FollowStatus, ProgressChange

NEW_YORK = "America/New_York"
NOON = datetime(2026, 10, 25, 16, 0, tzinfo=UTC)
PAGE_KEYS = {"next_cursor", "previous_cursor", "results"}
ROW_KEYS = {
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
}
LOG_ONLY_KEYS = {"review_title": str, "review": str, "contains_spoilers": bool}
NUMBER = (int, float)
NONE = type(None)
# Phase 4 adds fields beside the ones installed builds read; none of those changed type,
# except that a status-only day has no label (or the app would print "+Paused").
PROGRESS_OBJECT = {
    "type": str,
    "id": int,
    "summary": str,
    "label": (str, NONE),
    "amount": NUMBER,
    "unit": (str, NONE),
    "time_label": (str, NONE),
    "moves": list,
    "percent": (*NUMBER, NONE),
    "place": (dict, NONE),
}
LOG_OBJECT = {
    "type": str,
    "id": int,
    "rating": (str, NONE),
    "liked": bool,
    "is_rewatch": bool,
    "amount": NUMBER,
    "unit": (str, NONE),
    "time_label": (str, NONE),
    "label": (str, NONE),
}
VIEWER = {"can_view": bool, "has_liked": bool}
LATEST_KEYS = {"id": int, "previous": dict, "current": dict, "created_at": str}
SNAPSHOT_KEYS = {"kind", "value", "max", "unit", "journey_id"}


def rendered(response):
    """Return a response as the phone reads it."""
    return json.loads(JSONRenderer().render(response.data))


def old_progress_days(changes):
    """Group rows as the code a rollback lands on does: with no unit filter."""
    return (
        changes.annotate(
            day=TruncDate("created_at", tzinfo=timezone.get_current_timezone()),
            title_key=Concat(Value("item:"), Cast("item_id", CharField())),
            delta=Cast(KT("current_progress__value"), FloatField())
            - Cast(KT("previous_progress__value"), FloatField()),
        )
        .values("actor_id", "title_key", "day")
        .annotate(
            progress_id=Min("id"),
            last_at=Max("created_at"),
            any_item_id=Min("item_id"),
            **{f"amount_{unit}": Sum("delta", filter=Q(current_progress__unit=unit)) for unit in UNITS},
        )
        .filter(reduce(or_, (Q(**{f"amount_{unit}__gt": 0}) for unit in UNITS)))
    )


@override_settings(TIME_ZONE=NEW_YORK)
class WireBase(TestCase):
    """A book with a save and a status move, followed by a friend."""

    def setUp(self):
        for target in ("app.models.Item.fetch_releases",):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        clock = patch_now(NOON)
        clock.__enter__()
        self.addCleanup(clock.__exit__, None, None, None)
        self.clock = clock
        self.user = make_user(username="reader", calendar=NEW_YORK)
        self.friend = make_user(username="friend", calendar=NEW_YORK)
        Follow.objects.create(from_user=self.friend, to_user=self.user, status=FollowStatus.ACCEPTED)
        self.item = Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=MediaTypes.BOOK.value,
            media_id="1",
            title="Dune",
            image="https://example.com/poster.jpg",
            total_pages=400,
        )

    def get(self, path, viewer=None):
        """Return an endpoint's JSON."""
        client = APIClient(HTTP_X_SPINE_TIMEZONE=NEW_YORK)
        client.force_authenticate(viewer or self.user)
        response = client.get(path)
        self.assertEqual(response.status_code, 200, response.content)
        return rendered(response)

    def assert_keys_and_types(self, payload, shape):
        """Check a dict holds exactly these keys, each of its type."""
        self.assertEqual(set(payload), set(shape), payload)
        for key, kind in shape.items():
            self.assertIsInstance(payload[key], kind, key)


class ActivityEndpointTests(WireBase):
    """The two lists every installed build decodes."""

    def assert_rows(self, page):
        self.assertEqual(set(page), PAGE_KEYS)
        kinds = {row["type"] for row in page["results"]}
        self.assertEqual(kinds, {"progress_updated", "diary_created"})
        for row in page["results"]:
            if row["type"] == "progress_updated":
                self.assert_keys_and_types(row, ROW_KEYS)
                self.assert_keys_and_types(row["object"], PROGRESS_OBJECT)
                self.assertEqual(row["id"], -row["object"]["id"])
            else:
                self.assert_keys_and_types(row, {**ROW_KEYS, **LOG_ONLY_KEYS})
                self.assert_keys_and_types(row["object"], LOG_OBJECT)
            self.assert_keys_and_types(row["viewer"], VIEWER)
            date.fromisoformat(row["consumed_at"])

    def seed(self):
        """Save, pause and resume one book, and log another."""
        book_tracking.start(self.user, self.item)
        book_tracking.update_progress(self.user, self.item, progress_type="pages", value=40)
        book_tracking.pause(self.user, self.item)
        book_tracking.resume(self.user, self.item)
        other = Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=MediaTypes.BOOK.value,
            media_id="2",
            title="Emma",
            image="https://example.com/poster.jpg",
            total_pages=400,
        )
        book_tracking.complete(self.user, other, completion_date=date(2026, 10, 20))

    def test_the_profile_list(self):
        self.seed()
        self.assert_rows(self.get("/api/v1/users/reader/activity/"))

    def test_the_feed(self):
        self.seed()
        self.assert_rows(self.get("/api/v1/feed/", viewer=self.friend))

    def test_the_post_page(self):
        self.seed()
        row = next(
            row
            for row in self.get("/api/v1/users/reader/activity/")["results"]
            if row["type"] == "progress_updated"
        )
        page = self.get(f"/api/v1/progress/{row['object']['id']}/")
        self.assert_keys_and_types(page, ROW_KEYS)
        self.assertEqual(page["object"]["summary"], "read 40 pages")
        self.assertEqual(page["object"]["label"], "40 pages")

    def test_a_save_says_what_it_said(self):
        self.seed()
        row = next(
            row
            for row in self.get("/api/v1/users/reader/activity/")["results"]
            if row["type"] == "progress_updated"
        )
        self.assertEqual(row["object"]["summary"], "read 40 pages")
        self.assertEqual(row["created_at"], "2026-10-25T16:00:00Z")


class LatestChangeTests(WireBase):
    """The tracking detail's latest change: the same keys and types as before."""

    def latest(self):
        """Return the tracking detail's latest change."""
        return self.get(f"/api/v1/tracking/manual/book/{self.item.media_id}/")["latest_progress_change"]

    def test_its_shape(self):
        book_tracking.start(self.user, self.item)
        book_tracking.update_progress(self.user, self.item, progress_type="pages", value=40)
        latest = self.latest()
        self.assert_keys_and_types(latest, LATEST_KEYS)
        self.assertEqual(set(latest["current"]), SNAPSHOT_KEYS)
        self.assertEqual((latest["previous"]["value"], latest["current"]["value"]), (0, 40))
        self.assertNotIn("saved_on", latest)

    def test_a_status_move_leaves_it_alone(self):
        book_tracking.start(self.user, self.item)
        book_tracking.update_progress(self.user, self.item, progress_type="pages", value=40)
        before = self.latest()
        book_tracking.pause(self.user, self.item)
        self.assertEqual(self.latest(), before)


class OldCodeReadsNewRowsTests(WireBase):
    """A rollback's code meets the rows this ship wrote, and they stay harmless."""

    def seed(self):
        """Save on a day, then a status move on the same day and on the next."""
        book_tracking.start(self.user, self.item)
        book_tracking.update_progress(self.user, self.item, progress_type="pages", value=40)
        book_tracking.pause(self.user, self.item)
        self.clock.advance(days=1)
        book_tracking.resume(self.user, self.item)

    def test_old_grouping_adds_nothing_and_no_day(self):
        self.seed()
        with_rows = list(old_progress_days(ProgressChange.objects.filter(actor=self.user)))
        ProgressChange.objects.filter(current_progress__kind=ProgressChange.STATUS).delete()
        without = list(old_progress_days(ProgressChange.objects.filter(actor=self.user)))
        self.assertEqual(len(with_rows), 1)
        self.assertEqual(with_rows, without)

    def test_old_latest_change_hands_over_a_row_a_build_can_decode(self):
        self.seed()
        row = ProgressChange.objects.filter(actor=self.user).exclude(current_progress__kind=ProgressChange.START).latest("id")
        for snapshot in (row.previous_progress, row.current_progress):
            self.assertIsInstance(snapshot["kind"], str)
            self.assertIsInstance(snapshot["unit"], str)
            self.assertEqual(snapshot["value"], 0)
            self.assertIsNone(snapshot["max"])
        self.assertEqual(row.previous_progress["value"], row.current_progress["value"])

    def test_a_row_old_code_writes_reads_through_the_fallback(self):
        book_tracking.start(self.user, self.item)
        book_tracking.update_progress(self.user, self.item, progress_type="pages", value=40)
        ProgressChange.objects.update(saved_on=None)
        page = self.get("/api/v1/users/reader/activity/")
        self.assertEqual(page["results"][0]["consumed_at"], "2026-10-25")
