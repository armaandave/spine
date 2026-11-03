"""AC-3: a progress change's fixed day (migration social.0005 and fill_progress_days).

Rows from before the column take their save moment's day on the owner's calendar. New
York is the server, as in production; a blank or bad zone falls back to it. A rollback's
code writes rows naming only the old columns, so those stay empty until the command runs.
"""

import json
from datetime import UTC, date, datetime

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase, override_settings

from app.models import Item, MediaTypes, Sources
from social.models import ProgressChange

BEFORE = ("social", "0004_post_comments")
AFTER = ("social", "0005_progresschange_saved_on")
LATE_UTC = datetime(2026, 3, 1, 6, 30, tzinfo=UTC)  # Feb 28 in Los Angeles, Mar 1 elsewhere
EARLY_TOKYO = datetime(2026, 3, 1, 15, 30, tzinfo=UTC)  # Mar 2 in Tokyo, Mar 1 elsewhere


def migrate(target):
    executor = MigrationExecutor(connection)
    executor.migrate([target])


def insert_old_row(user, item, created_at):
    """Write a row the way code from before the column does: naming only the old columns."""
    snapshot = json.dumps({"kind": "pages", "value": 10, "max": 100, "unit": "page"})
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO social_progresschange (actor_id, item_id, previous_progress, current_progress, created_at)"
            " VALUES (%s, %s, %s, %s, %s)",
            [user.pk, item.pk, snapshot, snapshot, created_at],
        )


@override_settings(TIME_ZONE="America/New_York")
class SavedOnMigrationTests(TransactionTestCase):
    def setUp(self):
        self.addCleanup(migrate, AFTER)
        users = get_user_model().objects
        self.tokyo = users.create_user(username="fill-tokyo", calendar_timezone="Asia/Tokyo")
        self.los_angeles = users.create_user(username="fill-la", calendar_timezone="America/Los_Angeles")
        self.blank = users.create_user(username="fill-blank", calendar_timezone="")
        self.bad = users.create_user(username="fill-bad")
        get_user_model().objects.filter(pk=self.bad.pk).update(calendar_timezone="Not/AZone")
        self.item = Item.objects.create(
            media_id="fill",
            source=Sources.MANUAL.value,
            media_type=MediaTypes.BOOK.value,
            title="Fill",
            image="",
        )

    def days(self, user):
        rows = ProgressChange.objects.filter(actor=user).order_by("created_at")
        return list(rows.values_list("saved_on", flat=True))

    def test_each_owner_takes_their_own_calendar(self):
        migrate(BEFORE)
        for user in (self.tokyo, self.los_angeles, self.blank, self.bad):
            insert_old_row(user, self.item, LATE_UTC)
            insert_old_row(user, self.item, EARLY_TOKYO)
        migrate(AFTER)
        self.assertEqual(self.days(self.tokyo), [date(2026, 3, 1), date(2026, 3, 2)])
        self.assertEqual(self.days(self.los_angeles), [date(2026, 2, 28), date(2026, 3, 1)])
        self.assertEqual(self.days(self.blank), [date(2026, 3, 1), date(2026, 3, 1)])
        self.assertEqual(self.days(self.bad), [date(2026, 3, 1), date(2026, 3, 1)])

    def test_going_back_and_forward_keeps_every_row(self):
        insert_old_row(self.tokyo, self.item, EARLY_TOKYO)
        migrate(BEFORE)
        migrate(AFTER)
        self.assertEqual(self.days(self.tokyo), [date(2026, 3, 2)])

    def test_a_rollbacks_rows_stay_empty_until_the_command_fills_them(self):
        insert_old_row(self.tokyo, self.item, EARLY_TOKYO)
        insert_old_row(self.los_angeles, self.item, LATE_UTC)
        self.assertEqual(self.days(self.tokyo), [None])
        call_command("fill_progress_days", verbosity=0)
        self.assertEqual(self.days(self.tokyo), [date(2026, 3, 2)])
        self.assertEqual(self.days(self.los_angeles), [date(2026, 2, 28)])

    def test_the_command_leaves_a_stored_day_alone(self):
        insert_old_row(self.tokyo, self.item, EARLY_TOKYO)
        call_command("fill_progress_days", verbosity=0)
        ProgressChange.objects.filter(actor=self.tokyo).update(saved_on=date(2026, 1, 15))
        call_command("fill_progress_days", verbosity=0)
        self.assertEqual(self.days(self.tokyo), [date(2026, 1, 15)])
