"""Rows tied on everything but the person still page in a total order."""

from datetime import date
from types import SimpleNamespace

from django.test import SimpleTestCase

from api.services import activity, activity_list
from api.services.activity_rows import Row


def _row(actor_id):
    return Row("status", actor_id, date(2026, 1, 1), "tv:1", 7, 7, "tv")


class RowOrderActorTests(SimpleTestCase):
    """Rows that differ only by person keep a stable order."""

    def test_actor_breaks_a_tie_on_the_same_title_and_day(self):
        self.assertNotEqual(activity_list.row_order(_row(1)), activity_list.row_order(_row(2)))

    def test_cursor_keeps_the_actor_and_old_cursors_default_to_zero(self):
        key = activity_list.row_order(_row(5))
        request = SimpleNamespace(query_params={"cursor": activity._encode_cursor(key)})
        self.assertEqual(activity._decode_cursor(request), key)
        old = {"cursor": activity._encode_cursor((*key[:-1], 0))}
        old_key = activity._decode_cursor(SimpleNamespace(query_params=old))
        self.assertEqual(old_key[-1], 0)


class RowAddressLimitTests(SimpleTestCase):
    """Row addresses refuse ids past their packing limits."""

    def test_ids_past_the_limits_never_pack_into_another_titles_address(self):
        row = _row(1)
        row.title_id = activity.ITEM_LIMIT + 1
        with self.assertRaises(OverflowError):
            activity.row_address(row)
        row.progress_id = 99
        self.assertEqual(activity.row_address(row), 99)
        big_person = _row(activity.USER_LIMIT + 1)
        with self.assertRaises(OverflowError):
            activity.row_address(big_person)

    def test_ids_at_the_limits_still_pack(self):
        row = _row(activity.USER_LIMIT)
        row.title_id = activity.ITEM_LIMIT
        self.assertGreaterEqual(activity.row_address(row), activity.ADDRESS_BASE)
        self.assertLess(activity.row_address(row), 1 << 63)
