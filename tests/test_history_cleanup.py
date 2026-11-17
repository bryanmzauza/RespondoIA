"""Third-party text must leave the disk when it is forgotten, not at some later point."""

from datetime import date

from test_app_history import make_worker
from test_history_loader import FakeChat

from respondoia.config import Settings
from respondoia.style.service import StyleService


class Spy:
    """Wraps the database and counts checkpoints."""

    def __init__(self, db):
        self._db = db
        self.checkpoints = 0

    def transaction(self):
        return self._db.transaction()

    def checkpoint(self):
        self.checkpoints += 1
        self._db.checkpoint()


def test_forgetting_a_private_contact_checkpoints(db, tmp_path):
    spy = Spy(db)
    style = StyleService(spy, lambda: None, tmp_path / "knowledge.md")
    style.forget("Marina Duarte")
    assert spy.checkpoints == 1


def test_a_load_applies_the_retention(qtbot, db, tmp_path):
    # Messages from September with a one-month retention: the load must not keep them.
    settings = Settings(history_enabled=True, retention_months=1, max_load_months=0)
    chat = FakeChat(n=60, title="Marina Duarte", per_day=10, first_day=date(2026, 9, 1))
    worker, _ = make_worker(db, tmp_path, chat, settings)
    with qtbot.waitSignal(worker.loaded):
        worker.load("full", 42, "Marina Duarte", None)
    with db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM history_message").fetchone()[0] == 0
