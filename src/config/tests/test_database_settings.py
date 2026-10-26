import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

from django.db import transaction
from django.db.utils import ConnectionHandler
from django.test import SimpleTestCase

from config.database_settings import sqlite_database

ALIAS = "sqlite_lock_test"


class SQLiteDatabaseSettingsTests(SimpleTestCase):
    """Local and test backends share one SQLite file between the server and Celery."""

    def test_a_transaction_that_reads_first_waits_for_another_writer(self):
        """An atomic block that reads, then writes, while another connection is writing, waits and saves.

        With SQLite's default deferred transactions the write fails at once with "database is locked",
        which is how a TV log save and a Celery ratings save collided on the local backend.
        """
        with tempfile.TemporaryDirectory() as directory:
            # Its own alias, outside the test settings: Django allows a test this connection.
            handler = ConnectionHandler({"default": {}, ALIAS: sqlite_database(Path(directory) / "db.sqlite3")})
            wrote = threading.Event()
            errors = []

            def other_writer():
                try:
                    with transaction.atomic(using=ALIAS):
                        handler[ALIAS].cursor().execute("INSERT INTO entry (id) VALUES (1)")
                        wrote.set()
                        time.sleep(0.5)
                except Exception as error:  # noqa: BLE001 - reported to the test thread
                    errors.append(error)
                finally:
                    wrote.set()
                    handler.close_all()

            try:
                with mock.patch("django.db.transaction.connections", handler):
                    handler[ALIAS].cursor().execute("CREATE TABLE entry (id integer PRIMARY KEY)")
                    thread = threading.Thread(target=other_writer)
                    thread.start()
                    self.assertTrue(wrote.wait(5))

                    with transaction.atomic(using=ALIAS):
                        cursor = handler[ALIAS].cursor()
                        cursor.execute("SELECT COUNT(*) FROM entry")
                        cursor.execute("INSERT INTO entry (id) VALUES (2)")

                    thread.join(10)
                    cursor = handler[ALIAS].cursor()
                    cursor.execute("SELECT id FROM entry ORDER BY id")
                    rows = [row[0] for row in cursor.fetchall()]
            finally:
                handler.close_all()

        self.assertEqual(errors, [])
        self.assertEqual(rows, [1, 2])

    def test_sqlite_waits_for_a_busy_file_and_begins_writes_at_once(self):
        database = sqlite_database("db.sqlite3")

        self.assertEqual(database["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(database["NAME"], "db.sqlite3")
        self.assertEqual(database["OPTIONS"], {"transaction_mode": "IMMEDIATE", "timeout": 20})
