import logging
from functools import partial

from celery import states
from celery.signals import before_task_publish
from django.db import transaction
from django.db.backends.signals import connection_created
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils import timezone
from django_celery_results.models import TaskResult

from app.external_ratings import eligible_rating_sources
from app.mixins import collect_external_rating_item_id
from app.models import DiaryEntry, Item
from app.tasks import enrich_external_ratings, update_daily_statistics

logger = logging.getLogger(__name__)
# Frequent bookkeeping tasks with ignore_result=True: no PENDING TaskResult rows.
RESULTLESS_TASKS = frozenset({"Dispatch featured lists", "Fill item metadata"})


@receiver(post_save, sender=Item)
def handle_item_created(sender, instance, created, **kwargs):  # noqa: ARG001
    """Queue eligible external ratings after a new Item is committed."""
    if not created or not eligible_rating_sources(instance):
        return
    if collect_external_rating_item_id(instance.pk):
        return
    transaction.on_commit(
        partial(enrich_external_ratings.delay, instance.pk),
        robust=True,
    )


@receiver(connection_created)
def setup_sqlite_pragmas(sender, connection, **kwargs):  # noqa: ARG001
    """Set up SQLite pragmas for WAL mode and busy timeout on connection creation."""
    if connection.vendor == "sqlite":
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=wal;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.close()


@before_task_publish.connect
def create_task_result_on_publish(sender=None, headers=None, body=None, **kwargs):  # noqa: ARG001
    """Create a TaskResult object with PENDING status on task publish.

    https://github.com/celery/django-celery-results/issues/286#issuecomment-1279161047
    """
    if "task" not in headers:
        return
    if headers["task"] in RESULTLESS_TASKS:
        return

    TaskResult.objects.store_result(
        content_type="application/json",
        content_encoding="utf-8",
        task_id=headers["id"],
        result=None,
        status=states.PENDING,
        task_name=headers["task"],
        task_args=headers.get("argsrepr", ""),
        task_kwargs=headers.get("kwargsrepr", ""),
    )


@receiver(post_save, sender=DiaryEntry)
def handle_diary_entry_save(sender, instance, created, **kwargs):
    """Queue statistics update when a diary entry is saved."""
    transaction.on_commit(
        lambda: update_daily_statistics.delay(
            user_id=instance.user_id,
            date_str=instance.consumed_at.isoformat(),
        ),
    )
