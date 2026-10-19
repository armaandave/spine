"""Celery tasks for streaming availability."""

from celery import shared_task


@shared_task(name="Refresh streaming availability")
def refresh_streaming_titles(titles):
    """Refresh snapshots for up to 100 titles in every active region.

    ``titles`` is a list of ``[title_type, tmdb_id]`` pairs, e.g.
    ``[["movie", "603"], ["tv", "1396"]]``.
    """
    raise NotImplementedError
