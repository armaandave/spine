import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path

from django_celery_results.models import TaskResult

from integrations import tasks

TASKS_BY_SOURCE = {
    "trakt": tasks.import_trakt,
    "mal": tasks.import_mal,
    "anilist": tasks.import_anilist,
    "kitsu": tasks.import_kitsu,
    "steam": tasks.import_steam,
    "yamtrack": tasks.import_yamtrack,
    "hltb": tasks.import_hltb,
    "imdb": tasks.import_imdb,
    "goodreads": tasks.import_goodreads,
    "letterboxd": tasks.import_letterboxd,
    "storygraph": tasks.import_storygraph,
    "mal_export": tasks.import_mal_export,
}
# Uploads for these sources are spooled to a temp file the task deletes.
FILE_SUFFIXES = {
    "letterboxd": ".zip",
    "storygraph": ".csv",
    "goodreads": ".csv",
    "mal_export": ".xml",
}


def queue_import(source, user, data, files=None):
    """Queue a supported once-only import."""
    task = TASKS_BY_SOURCE[source]
    mode = data["mode"]
    username = data.get("username")
    if source in FILE_SUFFIXES:
        uploaded_file = data.get("file") or (files.get("file") if files else None)
        fd, path = tempfile.mkstemp(suffix=FILE_SUFFIXES[source])
        try:
            with os.fdopen(fd, "wb") as tmp:
                if hasattr(uploaded_file, "chunks"):
                    for chunk in uploaded_file.chunks():
                        tmp.write(chunk)
                else:
                    tmp.write(uploaded_file.read())
            result = task.delay(file_path=path, user_id=user.id, mode=mode)
        except Exception:
            with suppress(OSError):
                Path(path).unlink()
            raise
    elif source in {"yamtrack", "hltb", "imdb"}:
        uploaded_file = data.get("file") or (files.get("file") if files else None)
        result = task.delay(file=uploaded_file, user_id=user.id, mode=mode)
    elif source == "trakt" or source == "anilist":
        result = task.delay(user_id=user.id, mode=mode, username=username)
    else:
        result = task.delay(username=username, user_id=user.id, mode=mode)
    return {"task_id": result.id, "status": "queued"}


def task_status(task_id, user):
    """Return task status if it belongs to the current user."""
    task = TaskResult.objects.filter(task_id=task_id).first()
    if task is None:
        return None

    kwargs_text = task.task_kwargs or ""
    user_markers = (f"'user_id': {user.id}", f'"user_id": {user.id}')
    if not any(marker in kwargs_text for marker in user_markers):
        return None

    return {
        "task_id": task.task_id,
        "task_name": task.task_name,
        "status": task.status,
        "date_created": task.date_created,
        "date_done": task.date_done,
        "result": _result_message(task),
    }


UNEXPECTED_FAILURE_MESSAGE = "Import failed due to an unexpected error. Please try again later."


def _result_message(task):
    """Return the stored task result as a message clients can show as-is.

    Celery stores results JSON-encoded: a success summary arrives quoted and a
    failure arrives as an exception payload. Import errors carry a user-facing
    message; anything else is reported generically so internals never leak.
    """
    if task.result is None:
        return None
    try:
        decoded = json.loads(task.result)
    except (TypeError, ValueError):
        return task.result

    if task.status == "FAILURE":
        if isinstance(decoded, dict) and decoded.get("exc_type") == "MediaImportError":
            message = decoded.get("exc_message")
            if isinstance(message, list):
                message = message[0] if message else None
            if isinstance(message, str) and message.strip():
                return message
        return UNEXPECTED_FAILURE_MESSAGE
    if isinstance(decoded, str):
        return decoded
    return task.result
