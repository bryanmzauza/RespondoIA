import os

# Must be set before any Qt import so tests never open real windows.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

TEST_KEY = bytes(range(32))


@pytest.fixture
def db(tmp_path):
    """A real encrypted database in a temporary folder."""
    from respondoia.database import Database

    database = Database.open(tmp_path / "test.db", TEST_KEY)
    yield database
    database.close()
