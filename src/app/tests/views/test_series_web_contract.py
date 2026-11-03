"""The web reaches the same manga and comic state as the app (MC-000, BK-500)."""

from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from app import series_tracking
from app.models import Comic, DiaryEntry, Item, Manga, MediaTypes, Sources, Status
from social.models import ProgressChange

READING = Status.IN_PROGRESS.value


class SeriesWebContractTests(TestCase):
    """The web's form, progress buttons and delete go through app.series_tracking."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="reader", password="strong-password-123")
        self.client.force_login(self.user)
        self.item = Item.objects.create(
            source=Sources.MANUAL.value,
            media_type=MediaTypes.MANGA.value,
            media_id="berserk",
            title="Berserk",
            image="https://example.com/b.jpg",
            series_total=10,
        )

    def form(self, **fields):
        return {
            "source": self.item.source,
            "media_id": self.item.media_id,
            "media_type": self.item.media_type,
            "status": READING,
            "progress": 0,
            "notes": "",
            **fields,
        }

    def save(self, **fields):
        return self.client.post(reverse("media_save"), self.form(**fields))

    def row(self):
        return Manga.objects.get(user=self.user, item=self.item)

    # MC-000 (BK-104), MC-001: the form starts an attempt and saves its number.
    def test_the_form_starts_an_attempt_and_saves_its_number(self):
        self.save(progress=4)
        row = self.row()
        self.assertEqual(row.status, READING)
        self.assertEqual(row.progress, 4)
        self.assertEqual(row.attempts.get().place, 4)
        self.assertTrue(ProgressChange.objects.filter(actor=self.user, item=self.item, current_progress__value=4).exists())

    # MC-500: saving notes changes nothing else.
    def test_saving_notes_changes_nothing_else(self):
        self.save(progress=4)
        row = self.row()
        self.save(instance_id=row.pk, progress=4, notes="Good")
        row = self.row()
        self.assertEqual(row.notes, "Good")
        self.assertEqual(row.attempts.count(), 1)
        self.assertEqual(ProgressChange.objects.filter(actor=self.user, item=self.item, current_progress__value=4).count(), 1)

    # MC-004: a number past the total is refused.
    def test_the_form_refuses_a_number_past_the_total(self):
        self.save(progress=11)
        self.assertFalse(Manga.objects.filter(user=self.user, item=self.item).exists())

    # MC-100: reaching the total never finishes it.
    def test_the_progress_buttons_never_finish_a_series(self):
        series_tracking.start(self.user, self.item)
        series_tracking.update_progress(self.user, self.item, value=9)
        response = self.client.post(
            reverse("progress_edit", args=[MediaTypes.MANGA.value, self.row().pk]),
            {"operation": "increase"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "app/components/progress_changer.html")
        row = self.row()
        self.assertEqual(row.progress, 10)
        self.assertEqual(row.status, READING)
        self.assertEqual(row.attempts.get().place, 10)
        self.assertEqual(
            self.client.post(
                reverse("progress_edit", args=[MediaTypes.MANGA.value, row.pk]),
                {"operation": "increase"},
            ).status_code,
            400,
            "Past the total",
        )
        self.client.post(reverse("progress_edit", args=[MediaTypes.MANGA.value, row.pk]), {"operation": "decrease"})
        self.assertEqual(self.row().progress, 9)
        self.assertEqual(self.row().attempts.get().place, 9)

    # MC-000 (BK-300): the buttons need an attempt under way.
    def test_the_progress_buttons_need_an_attempt(self):
        series_tracking.assign_status(self.user, self.item, Status.PLANNING.value)
        response = self.client.post(
            reverse("progress_edit", args=[MediaTypes.MANGA.value, self.row().pk]),
            {"operation": "increase"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.row().progress, 0)

    # MC-000 (BK-002): delete goes through the same guard.
    def test_delete_keeps_real_history(self):
        series_tracking.start(self.user, self.item)
        self.client.post(reverse("media_delete"), {"instance_id": self.row().pk, "media_type": "manga"})
        self.assertTrue(Manga.objects.filter(user=self.user, item=self.item).exists())

        comic = Item.objects.create(source=Sources.MANUAL.value, media_type="comic", media_id="saga", title="Saga", image="https://example.com/s.jpg")
        series_tracking.assign_status(self.user, comic, Status.PLANNING.value)
        row = Comic.objects.get(user=self.user, item=comic)
        self.client.post(reverse("media_delete"), {"instance_id": row.pk, "media_type": "comic"})
        self.assertFalse(Comic.objects.filter(user=self.user, item=comic).exists())

    # MC-103: marking on the model is the eye, never a log.
    def test_the_models_mark_consumed_is_the_eye(self):
        series_tracking.assign_status(self.user, self.item, Status.PLANNING.value)
        self.row().mark_consumed()
        self.assertEqual(self.row().status, Status.COMPLETED.value)
        self.assertTrue(self.row().completed_manually)
        self.assertFalse(DiaryEntry.objects.filter(user=self.user, item=self.item).exists())

    def action(self, name):
        with patch("app.views.services.get_media_metadata", return_value=self.metadata()):
            return self.client.post(reverse(name, args=[self.item.source, self.item.media_type, self.item.media_id]))

    def metadata(self):
        return {"title": "Berserk", "image": "https://example.com/b.jpg", "max_progress": 10, "details": {}, "related": {}}

    # BK-300, BK-303: Pause keeps the attempt with the row, and Resume continues it.
    def test_pause_and_resume_move_the_attempt_with_the_row(self):
        self.save(progress=4)
        self.assertEqual(self.action("pause_media").status_code, 200)
        row = self.row()
        self.assertEqual((row.status, row.current_attempt.status), (Status.PAUSED.value, Status.PAUSED.value))
        self.assertEqual(self.action("resume_media").status_code, 200)
        row = self.row()
        self.assertEqual((row.status, row.current_attempt.status), (READING, READING))
        self.assertEqual(row.attempts.count(), 1)
        self.assertEqual(row.progress, 4)

    # BK-300, BK-500: Drop closes the attempt, keeping its place.
    def test_drop_closes_the_attempt(self):
        self.save(progress=4)
        self.assertEqual(self.action("drop_media").status_code, 200)
        row = self.row()
        self.assertEqual((row.status, row.current_attempt.status), (Status.DROPPED.value, Status.DROPPED.value))
        self.assertIsNotNone(row.current_attempt.end_date)
        self.assertEqual(row.progress, 4)

    # BK-300: Resume on a direct Paused begins an attempt, never a bare status.
    def test_resuming_a_direct_pause_starts_an_attempt(self):
        series_tracking.assign_status(self.user, self.item, Status.PAUSED.value)
        self.assertEqual(self.action("resume_media").status_code, 200)
        row = self.row()
        self.assertEqual(row.status, READING)
        self.assertEqual(row.current_attempt.status, READING)

    # MC-004: lowering a number still past a total that shrank is refused, not a server error.
    def test_decreasing_past_the_total_is_refused(self):
        self.save(progress=4)
        row = self.row()
        Item.objects.filter(pk=self.item.pk).update(series_total=2)
        response = self.client.post(
            reverse("progress_edit", args=[MediaTypes.MANGA.value, row.pk]),
            {"operation": "decrease"},
        )
        self.assertEqual(response.status_code, 400)

    # BK-306: the form's dates reach the attempt.
    def test_the_forms_start_date_reaches_the_attempt(self):
        self.save(progress=4)
        row = self.row()
        self.save(instance_id=row.pk, progress=4, start_date="2026-10-01T10:00")
        self.assertEqual(self.row().current_attempt.start_date, date(2026, 10, 1))

    def test_the_forms_end_date_reaches_a_did_not_finish_attempt(self):
        self.save()
        row = self.row()
        series_tracking.update_journey(self.user, self.item, row.current_attempt_id, start_date=date(2026, 9, 1))
        series_tracking.drop(self.user, self.item)
        self.save(instance_id=row.pk, status=Status.DROPPED.value, end_date="2026-10-01T10:00")
        self.assertEqual(self.row().current_attempt.end_date, date(2026, 10, 1))
