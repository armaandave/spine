from unittest.mock import Mock

from django.db import connection, connections
from django.test import TransactionTestCase

from app.db_threads import DatabaseThreadPoolExecutor


class DatabaseThreadPoolTests(TransactionTestCase):
    """Provider workers must return connections even outside request teardown."""

    def query_in_worker(self, *, fail=False):
        worker_connection = connections["default"]
        worker_connection.close = Mock(wraps=worker_connection.close)
        self.worker_connection = worker_connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
        if fail:
            raise ValueError("provider failed after querying")
        return result

    def test_success_returns_worker_connection_without_closing_request_connection(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        request_connection = connection.connection
        with DatabaseThreadPoolExecutor(max_workers=1) as executor:
            for _ in range(12):
                self.assertEqual(executor.submit(self.query_in_worker).result(timeout=5), (1,))
                self.worker_connection.close.assert_called_once_with()
                if connection.vendor == "postgresql":
                    self.assertIsNone(self.worker_connection.connection)
        self.assertIs(connection.connection, request_connection)

    def test_failure_returns_worker_connection_and_preserves_error(self):
        with DatabaseThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.query_in_worker, fail=True)
            with self.assertRaisesMessage(ValueError, "provider failed after querying"):
                future.result(timeout=5)
        self.worker_connection.close.assert_called_once_with()
