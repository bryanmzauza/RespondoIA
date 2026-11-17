import sqlite3
import threading

import pytest

from respondoia.database import MIGRATIONS, Database, DatabaseError, load_or_create_key

KEY = bytes(range(32))
OTHER_KEY = bytes(range(1, 33))
CANARY = "ZZ-CANARY-0303"


def test_key_is_created_once_and_stored_protected(tmp_path):
    path = tmp_path / "k"
    key = load_or_create_key(path)
    assert len(key) == 32 and load_or_create_key(path) == key
    assert key not in path.read_bytes()


def test_unreadable_key_file_is_a_key_error(tmp_path):
    (tmp_path / "k").write_bytes(b"not a dpapi blob")
    with pytest.raises(DatabaseError) as err:
        load_or_create_key(tmp_path / "k")
    assert err.value.code == "key"


def test_new_database_reaches_the_last_version(tmp_path):
    db = Database.open(tmp_path / "db", KEY)
    assert db.version == len(MIGRATIONS)
    db.close()


def test_reopening_migrates_nothing_twice(tmp_path):
    db = Database.open(tmp_path / "db", KEY)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO contact (name, name_key, first_seen, last_seen) VALUES ('a', 'a', '', '')"
        )
    db.close()
    db = Database.open(tmp_path / "db", KEY)
    with db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM contact").fetchone()[0] == 1
    assert db.version == len(MIGRATIONS)
    db.close()


def test_file_holds_no_plaintext(tmp_path):
    db = Database.open(tmp_path / "db", KEY)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO contact (name, name_key, first_seen, last_seen) VALUES (?, ?, '', '')",
            (CANARY, CANARY.lower()),
        )
        conn.execute(
            "INSERT INTO style_sample (language, source, chat_key, text, text_hash, run_id,"
            " added_at) VALUES ('pt', 'screen', 'c', ?, 'h', 0, '')",
            (CANARY,),
        )
    db.close()
    data = b"".join(p.read_bytes() for p in tmp_path.glob("db*"))
    assert CANARY.encode() not in data and not data.startswith(b"SQLite format 3")


def test_wrong_key_is_an_open_error_and_keeps_the_file(tmp_path):
    Database.open(tmp_path / "db", KEY).close()
    before = (tmp_path / "db").read_bytes()
    with pytest.raises(DatabaseError) as err:
        Database.open(tmp_path / "db", OTHER_KEY)
    assert err.value.code == "open" and (tmp_path / "db").read_bytes() == before


def test_fts5_is_available(tmp_path):
    db = Database.open(tmp_path / "db", KEY)
    with pytest.raises(RuntimeError), db.transaction() as conn:
        conn.execute("CREATE VIRTUAL TABLE probe USING fts5(x)")
        conn.execute("INSERT INTO probe VALUES ('bom dia')")
        assert conn.execute("SELECT count(*) FROM probe WHERE probe MATCH 'dia'").fetchone()[0]
        raise RuntimeError("roll back")
    db.close()


def test_transaction_rolls_back_on_error(tmp_path):
    db = Database.open(tmp_path / "db", KEY)
    with pytest.raises(ValueError), db.transaction() as conn:
        conn.execute(
            "INSERT INTO contact (name, name_key, first_seen, last_seen)"
            " VALUES ('a', 'a', '', '')"
        )
        raise ValueError
    with db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM contact").fetchone()[0] == 0
    db.close()


def test_two_threads_share_one_database(tmp_path):
    db = Database.open(tmp_path / "db", KEY)

    def write(tag):
        for i in range(100):
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO pasted (text_hash, at) VALUES (?, '')", (f"{tag}{i}",)
                )

    threads = [threading.Thread(target=write, args=(t,)) for t in "ab"]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    with db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM pasted").fetchone()[0] == 200
    db.close()


def test_deleting_a_contact_deletes_its_samples(tmp_path):
    db = Database.open(tmp_path / "db", KEY)
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO contact (id, name, name_key, first_seen, last_seen)"
            " VALUES (1, 'a', 'a', '', '')"
        )
        conn.execute(
            "INSERT INTO style_sample (language, contact_id, source, chat_key, text, text_hash,"
            " run_id, added_at) VALUES ('pt', 1, 'screen', 'c', 't', 'h', 0, '')"
        )
        conn.execute("DELETE FROM contact WHERE id = 1")
        assert conn.execute("SELECT count(*) FROM style_sample").fetchone()[0] == 0
    db.close()


class FailingCommit:
    """Wraps the connection and fails the first COMMIT, as a full disk would."""

    def __init__(self, conn):
        self._conn = conn
        self.failed = False

    def execute(self, sql, *args):
        if sql == "COMMIT" and not self.failed:
            self.failed = True
            raise sqlite3.OperationalError("disk I/O error")
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_a_failed_commit_does_not_block_later_transactions(tmp_path):
    db = Database.open(tmp_path / "db", KEY)
    real = db._conn
    db._conn = FailingCommit(real)
    with pytest.raises(sqlite3.OperationalError), db.transaction() as conn:
        conn.execute("INSERT INTO pasted (text_hash, at) VALUES ('a', '')")
    with db.transaction() as conn:
        conn.execute("INSERT INTO pasted (text_hash, at) VALUES ('b', '')")
        assert [r[0] for r in conn.execute("SELECT text_hash FROM pasted")] == ["b"]
    db._conn = real
    db.close()


def test_close_does_not_hang_while_another_thread_holds_the_database(tmp_path):
    import time

    db = Database.open(tmp_path / "db", KEY)
    holding, release = threading.Event(), threading.Event()

    def hold():
        with db.transaction():
            holding.set()
            release.wait(10)

    thread = threading.Thread(target=hold)
    thread.start()
    holding.wait(5)
    started = time.perf_counter()
    db.close()
    assert time.perf_counter() - started < 4
    release.set()
    thread.join()


def test_migration_two_from_version_one(tmp_path, monkeypatch):
    from respondoia import database

    monkeypatch.setattr(database, "MIGRATIONS", MIGRATIONS[:1])
    old = Database.open(tmp_path / "db", KEY)
    with old.transaction() as conn:
        conn.execute(
            "INSERT INTO contact (name, name_key, label, first_seen, last_seen)"
            " VALUES ('Ana', 'ana', 'client', 'x', 'x')"
        )
    old.close()
    monkeypatch.setattr(database, "MIGRATIONS", MIGRATIONS)
    db = Database.open(tmp_path / "db", KEY)
    try:
        assert db.version == len(MIGRATIONS) >= 2
        with db.transaction() as conn:
            row = conn.execute("SELECT label, used_at, avatar, history FROM contact").fetchone()
        assert tuple(row) == ("client", None, "", None)
    finally:
        db.close()


def test_checkpoint_runs_outside_a_transaction(tmp_path):
    db = Database.open(tmp_path / "db", KEY)
    try:
        db.checkpoint()
    finally:
        db.close()
