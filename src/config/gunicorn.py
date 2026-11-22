import gc
import os

bind = "localhost:8001"
preload_app = True
worker_class = "gthread"
workers = int(os.environ.get("GUNICORN_WORKERS", os.environ.get("WEB_CONCURRENCY", "2")))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
if workers < 1 or threads < 1:
    raise ValueError("Gunicorn workers and threads must be positive integers")
timeout = 200
max_requests = 500
max_requests_jitter = 10

accesslog = "-"
errorlog = "-"


def pre_fork(server, worker):  # noqa: ARG001
    """Freeze the preloaded app's objects so each forked worker keeps sharing their memory.

    Without it, the garbage collector's first pass in a worker writes to every
    preloaded object and the OS copies those pages into the worker.
    """
    gc.freeze()
