"""Thread pools for work that may use Django's thread-local database connections."""

from concurrent.futures import ThreadPoolExecutor
from functools import partial

from django.db import connections


def _run_and_close_connections(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    finally:
        # Request teardown only closes the request thread's connections. Provider
        # workers need their own teardown to return borrowed PostgreSQL pool slots,
        # including work finishing after the response deadline or an exception.
        connections.close_all()


class DatabaseThreadPoolExecutor(ThreadPoolExecutor):
    """Return worker connections after every task, leaving the caller's alone."""

    def submit(self, fn, /, *args, **kwargs):
        return super().submit(partial(_run_and_close_connections, fn, *args, **kwargs))
