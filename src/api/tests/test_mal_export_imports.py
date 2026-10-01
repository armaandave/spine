import gzip
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django_celery_results.models import TaskResult
from rest_framework import status
from rest_framework.test import APIClient

from api.services.imports import TASKS_BY_SOURCE
from integrations import tasks

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "integrations"
    / "tests"
    / "mock_data"
    / "import_mal_export_anime.xml"
)


class MALExportImportApiTests(TestCase):
    """Cover the mal_export upload contract used by the iOS client."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="malapi", password="password")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _post(self, source, data, task):
        task_map = {source: task}
        with (
            patch.dict("api.views.imports.TASKS_BY_SOURCE", task_map, clear=True),
            patch.dict("api.services.imports.TASKS_BY_SOURCE", task_map, clear=True),
        ):
            return self.client.post(f"/api/v1/imports/{source}/", data, format="multipart")

    def test_source_is_registered_with_named_task(self):
        self.assertIs(TASKS_BY_SOURCE["mal_export"], tasks.import_mal_export)
        self.assertEqual(tasks.import_mal_export.name, "Import from MyAnimeList export")
        self.assertIs(TASKS_BY_SOURCE["mal"], tasks.import_mal)

    def test_upload_queues_task_with_temp_file(self):
        task = Mock()
        task.delay.return_value = SimpleNamespace(id="mal-export-task")
        payload = gzip.compress(FIXTURE.read_bytes())
        upload = SimpleUploadedFile("animelist.xml.gz", payload, content_type="application/gzip")

        response = self._post("mal_export", {"mode": "overwrite", "file": upload}, task)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data, {"task_id": "mal-export-task", "status": "queued"})
        kwargs = task.delay.call_args.kwargs
        self.assertEqual(kwargs["user_id"], self.user.id)
        self.assertEqual(kwargs["mode"], "overwrite")
        temp_path = Path(kwargs["file_path"])
        self.addCleanup(temp_path.unlink, missing_ok=True)
        self.assertEqual(temp_path.read_bytes(), payload)

        TaskResult.objects.create(
            task_id="mal-export-task",
            task_name="Import from MyAnimeList export",
            task_kwargs=f"{{'file_path': '{temp_path}', 'user_id': {self.user.id}, 'mode': 'overwrite'}}",
            status="SUCCESS",
            result='"Imported 9 Anime."',
        )
        poll = self.client.get("/api/v1/imports/tasks/mal-export-task/")
        self.assertEqual(poll.status_code, status.HTTP_200_OK)
        self.assertEqual(poll.data["status"], "SUCCESS")

    def test_missing_file_is_rejected(self):
        task = Mock()

        response = self._post("mal_export", {"mode": "new"}, task)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.data["error"]["fields"]["file"],
            ["A MyAnimeList XML export file is required."],
        )
        task.delay.assert_not_called()

    def test_username_mal_import_is_unchanged(self):
        task = Mock()
        task.delay.return_value = SimpleNamespace(id="mal-task")

        response = self._post("mal", {"mode": "new", "username": "someone"}, task)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        task.delay.assert_called_once_with(username="someone", user_id=self.user.id, mode="new")


class ImportTaskStatusMessageTests(TestCase):
    """The status endpoint returns a message clients can display verbatim."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="pollapi", password="password")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _poll(self, status_value, result):
        TaskResult.objects.create(
            task_id="poll-task",
            task_name="Import from MyAnimeList export",
            task_kwargs=f"{{'file_path': '/tmp/x.xml', 'user_id': {self.user.id}, 'mode': 'new'}}",
            status=status_value,
            result=result,
        )
        response = self.client.get("/api/v1/imports/tasks/poll-task/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data["result"]

    def test_success_summary_is_unquoted(self):
        result = self._poll("SUCCESS", '"Imported 45 anime.\\n\\nwarning line"')

        self.assertEqual(result, "Imported 45 anime.\n\nwarning line")

    def test_import_error_message_is_surfaced(self):
        payload = (
            '{"exc_type": "MediaImportError", '
            '"exc_message": ["This doesn\'t look like a MyAnimeList export."], '
            '"exc_module": "integrations.imports.helpers"}'
        )

        self.assertEqual(self._poll("FAILURE", payload), "This doesn't look like a MyAnimeList export.")

    def test_unexpected_error_is_not_leaked(self):
        payload = '{"exc_type": "KeyError", "exc_message": ["secret_internal_key"], "exc_module": "builtins"}'

        self.assertEqual(
            self._poll("FAILURE", payload),
            "Import failed due to an unexpected error. Please try again later.",
        )

    def test_pending_without_result_is_null(self):
        self.assertIsNone(self._poll("PENDING", None))

    def test_non_json_result_is_passed_through(self):
        self.assertEqual(self._poll("SUCCESS", "plain text"), "plain text")
