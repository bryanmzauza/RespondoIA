"""Encrypted local database. The key is protected by DPAPI, so only this Windows account
can open the file. Nothing here knows about style or contacts: the tables come from the
migrations, the queries from the modules that own them.
"""

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pywintypes
import sqlcipher3
import win32crypt

from respondoia.config import app_dir

DB_FILE = "respondoia.db"
KEY_FILE = "respondoia.key"

_NO_UI = 0x1  # CRYPTPROTECT_UI_FORBIDDEN


class DatabaseError(Exception):
    """code: "key" (key unreadable or from another account), "open" or "corrupt"."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def protect(data: bytes) -> bytes:
    return win32crypt.CryptProtectData(data, None, None, None, None, _NO_UI)


def unprotect(blob: bytes) -> bytes:
    try:
        return win32crypt.CryptUnprotectData(blob, None, None, None, _NO_UI)[1]
    except pywintypes.error as exc:
        raise DatabaseError("key") from exc


def load_or_create_key(key_path: Path) -> bytes:
    """32 random bytes. The file holds only the DPAPI blob."""
    if key_path.exists():
        key = unprotect(key_path.read_bytes())
        if len(key) != 32:
            raise DatabaseError("key")
        return key
    key = os.urandom(32)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = key_path.with_name(key_path.name + ".tmp")
    temporary.write_bytes(protect(key))
    os.replace(temporary, key_path)
    return key


# MIGRATIONS[i] takes the schema from version i to i + 1. Never edit a released one.
MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE contact (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        name_key TEXT NOT NULL,
        is_group INTEGER NOT NULL DEFAULT 0,
        label TEXT,
        label_source TEXT,
        language TEXT,
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL
    );
    CREATE INDEX contact_name_key ON contact(name_key);
    CREATE TABLE style_sample (
        id INTEGER PRIMARY KEY,
        language TEXT NOT NULL,
        contact_id INTEGER REFERENCES contact(id) ON DELETE CASCADE,
        label TEXT,
        source TEXT NOT NULL,
        chat_key TEXT NOT NULL,
        sent_on TEXT NOT NULL DEFAULT '',
        time TEXT NOT NULL DEFAULT '',
        text TEXT NOT NULL,
        text_hash TEXT NOT NULL,
        run_id INTEGER NOT NULL,
        excluded INTEGER NOT NULL DEFAULT 0,
        added_at TEXT NOT NULL,
        UNIQUE (chat_key, sent_on, time, text_hash)
    );
    CREATE TABLE style_profile (
        language TEXT PRIMARY KEY,
        data TEXT NOT NULL,
        manual TEXT NOT NULL DEFAULT '',
        extracted_at TEXT,
        attempted_at TEXT,
        last_sample_id INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE pasted (
        text_hash TEXT PRIMARY KEY,
        at TEXT NOT NULL
    );
    CREATE TABLE name_token (
        hash TEXT PRIMARY KEY
    );
    """,
    # 2: chat history, its search index and the contact memory.
    """
    ALTER TABLE contact ADD COLUMN used_at TEXT;
    ALTER TABLE contact ADD COLUMN history INTEGER;
    ALTER TABLE contact ADD COLUMN avatar TEXT NOT NULL DEFAULT '';
    CREATE TABLE history_message (
        id INTEGER PRIMARY KEY,
        contact_id INTEGER NOT NULL REFERENCES contact(id) ON DELETE CASCADE,
        segment INTEGER NOT NULL,
        ord INTEGER NOT NULL,
        sent_on TEXT NOT NULL DEFAULT '',
        time TEXT NOT NULL DEFAULT '',
        author TEXT NOT NULL,
        sender TEXT NOT NULL DEFAULT '',
        kind TEXT NOT NULL,
        text TEXT NOT NULL,
        quote TEXT NOT NULL DEFAULT '',
        fingerprint TEXT NOT NULL,
        seen_at TEXT NOT NULL,
        UNIQUE (contact_id, segment, ord)
    );
    CREATE INDEX history_order ON history_message(contact_id, segment, ord);
    CREATE VIRTUAL TABLE history_fts USING fts5(
        text, quote, content='history_message', content_rowid='id',
        tokenize='unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER history_ai AFTER INSERT ON history_message BEGIN
        INSERT INTO history_fts(rowid, text, quote) VALUES (new.id, new.text, new.quote);
    END;
    CREATE TRIGGER history_ad AFTER DELETE ON history_message BEGIN
        INSERT INTO history_fts(history_fts, rowid, text, quote)
            VALUES ('delete', old.id, old.text, old.quote);
    END;
    CREATE TRIGGER history_au AFTER UPDATE ON history_message BEGIN
        INSERT INTO history_fts(history_fts, rowid, text, quote)
            VALUES ('delete', old.id, old.text, old.quote);
        INSERT INTO history_fts(rowid, text, quote) VALUES (new.id, new.text, new.quote);
    END;
    CREATE TABLE contact_memory (
        contact_id INTEGER PRIMARY KEY REFERENCES contact(id) ON DELETE CASCADE,
        text TEXT NOT NULL,
        edited INTEGER NOT NULL DEFAULT 0,
        through_id INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT,
        attempted_at TEXT
    );
    CREATE TABLE history_state (
        contact_id INTEGER PRIMARY KEY REFERENCES contact(id) ON DELETE CASCADE,
        loaded_at TEXT,
        reached_start INTEGER NOT NULL DEFAULT 0
    );
    """,
)


class Database:
    """One connection shared by every thread, one transaction at a time."""

    def __init__(self, conn: sqlcipher3.Connection) -> None:
        self._conn = conn
        self._lock = threading.RLock()

    @classmethod
    def open(cls, path: Path, key: bytes) -> "Database":
        """Create or migrate. A file that does not open is left as it is."""
        try:
            conn = sqlcipher3.connect(str(path), check_same_thread=False, isolation_level=None)
        except sqlcipher3.Error as exc:
            raise DatabaseError("open") from exc
        try:
            conn.execute("PRAGMA cipher_log_level = NONE")
            conn.execute(f"PRAGMA key = \"x'{key.hex()}'\"")
            # A wrong key only shows on the first read, so read now.
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
            for pragma in (
                "foreign_keys = ON",
                "temp_store = MEMORY",
                "secure_delete = ON",
                "busy_timeout = 5000",
                "journal_mode = WAL",
            ):
                conn.execute("PRAGMA " + pragma)
            db = cls(conn)
            db._migrate()
        except sqlcipher3.Error as exc:
            conn.close()
            raise DatabaseError("open") from exc
        return db

    @property
    def version(self) -> int:
        with self._lock:
            return self._conn.execute("PRAGMA user_version").fetchone()[0]

    @contextmanager
    def transaction(self) -> Iterator[sqlcipher3.Connection]:
        """Commit on success, roll back on any exception."""
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                # Also after a failed COMMIT, so the next BEGIN does not fail forever.
                if self._conn.in_transaction:
                    self._conn.execute("ROLLBACK")
                raise

    def checkpoint(self) -> None:
        """Move the write-ahead log into the file and empty it, so deleted text leaves it."""
        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()

    def close(self, timeout_s: float = 2.0) -> None:
        """Close, unless another thread still holds the database (a stopped thread may
        never release it). The process is ending then, and the file stays consistent."""
        if not self._lock.acquire(timeout=timeout_s):
            return
        try:
            self._conn.close()
        finally:
            self._lock.release()

    def _migrate(self) -> None:
        for number in range(self.version, len(MIGRATIONS)):
            # executescript commits on its own; the version is set inside the same script.
            self._conn.executescript(
                f"BEGIN;\n{MIGRATIONS[number]}\nPRAGMA user_version = {number + 1};\nCOMMIT;"
            )


def open_default() -> Database:
    folder = app_dir()
    return Database.open(folder / DB_FILE, load_or_create_key(folder / KEY_FILE))


# What the database library raises for a failed query, for callers that must not crash.
SqlError = sqlcipher3.Error
