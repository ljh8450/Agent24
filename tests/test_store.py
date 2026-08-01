import unittest
from pathlib import Path
from unittest.mock import patch

from app.store import ProjectStore


class FakeConnection:
    def __init__(self):
        self.closed = False
        self.row_factory = None

    def execute(self, _query):
        return self

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        return False

    def close(self):
        self.closed = True


class ProjectStoreConnectionTests(unittest.TestCase):
    def test_connect_closes_sqlite_connection_after_context(self):
        store = ProjectStore.__new__(ProjectStore)
        store.db_path = Path("unused.sqlite")
        connection = FakeConnection()

        with patch("app.store.sqlite3.connect", return_value=connection):
            with store._connect() as opened:
                self.assertIs(opened, connection)
                self.assertFalse(connection.closed)

        self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
