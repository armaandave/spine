from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from app import series_tracking
from app.models import Comic, DiaryEntry, Item, MediaTypes, Sources, Status
from app.providers import services
from events.calendar.comic import process_comic
from events.calendar.helpers import date_parser
from events.models import Event
from events.tests.calendar.utils import CalendarFixturesMixin
from social.models import Activity, ProgressChange


class CalendarComicTests(CalendarFixturesMixin, TestCase):
    """Test comic calendar processing."""

    @patch("events.calendar.comic.services.get_media_metadata")
    @patch("events.calendar.comic.comicvine.issue")
    def test_process_comic_with_store_date(self, mock_issue, mock_get_media_metadata):
        """Test process_comic with store date available."""
        comic_item = Item.objects.create(
            media_id="4050-18166",
            source=Sources.COMICVINE.value,
            media_type=MediaTypes.COMIC.value,
            title="Batman",
            image="http://example.com/batman.jpg",
        )

        mock_get_media_metadata.return_value = {
            "max_issue_number": 10,
            "last_issue_id": "4000-123456",
            "last_issue": {"issue_number": "10"},
        }

        mock_issue.return_value = {
            "store_date": "2023-04-15",
            "cover_date": "2023-05-01",
        }

        events_bulk = []
        process_comic(comic_item, events_bulk)

        self.assertEqual(len(events_bulk), 1)
        self.assertEqual(events_bulk[0].item, comic_item)
        self.assertEqual(events_bulk[0].content_number, 10)
        self.assertEqual(events_bulk[0].datetime, date_parser("2023-04-15"))
        mock_issue.assert_called_once_with("4000-123456")

    @patch("events.calendar.comic.services.get_media_metadata")
    @patch("events.calendar.comic.comicvine.issue")
    def test_process_comic_with_cover_date_only(
        self,
        mock_issue,
        mock_get_media_metadata,
    ):
        """Test process_comic with only cover date available."""
        comic_item = Item.objects.create(
            media_id="4050-18167",
            source=Sources.COMICVINE.value,
            media_type=MediaTypes.COMIC.value,
            title="Superman",
            image="http://example.com/superman.jpg",
        )

        mock_get_media_metadata.return_value = {
            "max_issue_number": 5,
            "last_issue_id": "4000-123457",
            "last_issue": {"issue_number": "5"},
        }

        mock_issue.return_value = {
            "store_date": None,
            "cover_date": "2023-05-01",
        }

        events_bulk = []
        process_comic(comic_item, events_bulk)

        self.assertEqual(len(events_bulk), 1)
        self.assertEqual(events_bulk[0].item, comic_item)
        self.assertEqual(events_bulk[0].content_number, 5)
        self.assertEqual(events_bulk[0].datetime, date_parser("2023-05-01"))

    @patch("events.calendar.comic.services.get_media_metadata")
    @patch("events.calendar.comic.comicvine.issue")
    def test_process_comic_no_dates(self, mock_issue, mock_get_media_metadata):
        """Test process_comic with no dates available."""
        comic_item = Item.objects.create(
            media_id="4050-18168",
            source=Sources.COMICVINE.value,
            media_type=MediaTypes.COMIC.value,
            title="Wonder Woman",
            image="http://example.com/wonderwoman.jpg",
        )

        mock_get_media_metadata.return_value = {
            "max_issue_number": 3,
            "last_issue_id": "4000-123458",
            "last_issue": {"issue_number": "3"},
        }

        mock_issue.return_value = {
            "store_date": None,
            "cover_date": None,
        }

        events_bulk = []
        process_comic(comic_item, events_bulk)

        self.assertEqual(len(events_bulk), 0)

    @patch("events.calendar.comic.services.get_media_metadata")
    def test_process_comic_returns_when_issue_is_already_saved(
        self,
        mock_get_media_metadata,
    ):
        """No new event should be added if the latest issue is already stored."""
        Event.objects.create(
            item=self.comic_item,
            content_number=10,
            datetime=date_parser("2023-04-15"),
        )
        mock_get_media_metadata.return_value = {
            "max_issue_number": 10,
            "last_issue_id": "4000-123456",
            "last_issue": {"issue_number": "10"},
        }

        events_bulk = []
        process_comic(self.comic_item, events_bulk)

        self.assertEqual(events_bulk, [])

    @patch("events.calendar.comic.services.get_media_metadata")
    @patch("events.calendar.comic.comicvine.issue")
    def test_process_comic_handles_issue_provider_error(
        self,
        mock_issue,
        mock_get_media_metadata,
    ):
        """Issue lookup failures should stop comic processing quietly."""
        response = type("Response", (), {"status_code": 500, "text": "boom"})()
        mock_get_media_metadata.return_value = {
            "max_issue_number": 10,
            "last_issue_id": "4000-123456",
            "last_issue": {"issue_number": "10"},
        }
        mock_issue.side_effect = services.ProviderAPIError(
            provider=Sources.COMICVINE.value,
            error=type("Error", (), {"response": response})(),
            details="boom",
        )

        events_bulk = []
        process_comic(self.comic_item, events_bulk)

        self.assertEqual(events_bulk, [])


@patch("events.calendar.comic.comicvine.issue", return_value={"store_date": "2026-10-20", "cover_date": None})
class NewIssueMovesFinishedComicsTests(TestCase):
    """MC-202: the daily job continues a finished comic when it finds a newer last issue."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="follower", password="strong-password-123")
        self.item = Item.objects.create(
            media_id="46568",
            source=Sources.COMICVINE.value,
            media_type=MediaTypes.COMIC.value,
            title="Saga",
            image="http://example.com/saga.jpg",
            series_total=54,
        )
        self.today = timezone.localdate()
        Event.objects.create(item=self.item, content_number=54, datetime=timezone.now() - timedelta(days=30))

    def run_job(self, *, count=55, last_issue=55, item=None):
        metadata = {
            "max_issue_number": last_issue,
            "last_issue_id": "4000-1",
            "details": {"issues_count": count},
        }
        with patch("events.calendar.comic.services.get_media_metadata", return_value=metadata):
            events = []
            process_comic(item or self.item, events)
        return events

    def comic(self):
        return Comic.objects.get(user=self.user, item=self.item)

    # MC-202: a Read comic carries on at its place, adding no activity, keeping its logs.
    def test_a_read_comic_carries_on_by_itself(self, _issue):
        series_tracking.complete(self.user, self.item, completion_date=self.today)
        changes_before = list(ProgressChange.objects.values_list("id", flat=True))
        activity_before = Activity.objects.count()

        events = self.run_job()

        self.assertEqual(len(events), 1)
        row = self.comic()
        self.assertEqual(row.status, Status.IN_PROGRESS.value, "Its eye empties")
        self.assertEqual(row.progress, 54)
        self.assertEqual(row.item.series_total, 55)
        attempt = row.current_attempt
        self.assertEqual(attempt.place, 54)
        self.assertTrue(attempt.continues)
        self.assertEqual(list(ProgressChange.objects.values_list("id", flat=True)), changes_before)
        self.assertEqual(Activity.objects.count(), activity_before)
        self.assertEqual(DiaryEntry.objects.filter(user=self.user, item=self.item).count(), 1, "Its logs stay")
        state = series_tracking.state_payload(row)
        self.assertFalse(state["is_rereading"])
        self.assertEqual(state["current_journey"]["progress"]["max"], 55)

    # MC-202: logging the new issue is another entry, not a reread.
    def test_the_next_log_is_not_a_reread(self, _issue):
        series_tracking.complete(self.user, self.item, completion_date=self.today)
        self.run_job()
        _, entry = series_tracking.complete(self.user, self.item, completion_date=self.today)
        self.assertFalse(entry.is_rewatch)
        self.assertEqual(self.comic().progress, 55)

    # MC-202: a comic marked Read with the eye moves too, with no number to carry.
    def test_an_eye_marked_comic_moves_at_zero(self, _issue):
        series_tracking.mark_read(self.user, self.item)
        self.run_job()
        row = self.comic()
        self.assertEqual(row.status, Status.IN_PROGRESS.value)
        self.assertEqual(row.progress, 0)

    # MC-202: Paused, Did Not Finish and To Read comics don't move.
    def test_other_shelves_do_not_move(self, _issue):
        for status in (Status.PAUSED.value, Status.DROPPED.value, Status.PLANNING.value):
            with self.subTest(status=status):
                series_tracking.assign_status(self.user, self.item, status)
                Event.objects.filter(item=self.item).update(content_number=54)
                self.run_job()
                self.assertEqual(self.comic().status, status)
                self.assertFalse(self.comic().attempts.exists())

    # MC-202: an attempt under way stays as it is.
    def test_an_attempt_under_way_stays(self, _issue):
        series_tracking.start(self.user, self.item)
        attempt = self.comic().current_attempt
        self.run_job()
        self.assertEqual(self.comic().current_attempt, attempt)

    # MC-202: only a newer last issue.
    def test_the_same_last_issue_moves_nothing(self, _issue):
        series_tracking.complete(self.user, self.item, completion_date=self.today)
        self.assertEqual(self.run_job(count=54, last_issue=54), [])
        self.assertEqual(self.comic().status, Status.COMPLETED.value)

    # MC-202: the first pass over a comic knows of no newer issue.
    def test_a_comic_with_no_known_issue_moves_nothing(self, _issue):
        Event.objects.filter(item=self.item).delete()
        series_tracking.complete(self.user, self.item, completion_date=self.today)
        self.run_job()
        self.assertEqual(self.comic().status, Status.COMPLETED.value)

    # MC-202: every follower moves.
    def test_every_reader_of_it_moves(self, _issue):
        other = get_user_model().objects.create_user(username="other", password="strong-password-123")
        series_tracking.complete(self.user, self.item, completion_date=self.today)
        series_tracking.complete(other, self.item, completion_date=self.today)
        self.run_job()
        self.assertEqual(
            set(Comic.objects.filter(item=self.item).values_list("status", flat=True)),
            {Status.IN_PROGRESS.value},
        )

    # MC-200, MC-202: a run back after a longer gap moves when the reader chooses.
    def test_a_run_outside_the_jobs_year_moves_only_by_choice(self, _issue):
        series_tracking.complete(self.user, self.item, completion_date=self.today)
        Item.objects.filter(pk=self.item.pk).update(series_total=60)
        row = self.comic()
        self.assertEqual(row.status, Status.COMPLETED.value)
        self.assertTrue(series_tracking.state_payload(row)["offers_keep_reading"])
        series_tracking.keep_reading(self.user, self.item)
        self.assertEqual(self.comic().progress, 54)
