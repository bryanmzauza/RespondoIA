"""Style data in the encrypted database: the user's own messages, the profiles, the notes
the user wrote, and hashes of what the app pasted.

Every function takes an open connection from Database.transaction().
"""

from collections import Counter
from collections.abc import Sequence
from datetime import datetime, timedelta

from respondoia.style.profile import EXTRACT_MIN, StyleProfile
from respondoia.style.sampler import OwnMessage, StyleStats, import_key, text_hash

POOL_LIMIT = 500  # per language
PASTED_DAYS = 365
EXTRACT_EVERY = timedelta(hours=24)


def add_samples(conn, messages: Sequence[OwnMessage], contact_id: int | None, now: str) -> int:
    """Store new messages; duplicates are ignored. Returns how many were added."""
    if not messages:
        return 0
    base = conn.execute("SELECT COALESCE(MAX(run_id), 0) + 1 FROM style_sample").fetchone()[0]
    added = 0
    for m in messages:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO style_sample (language, contact_id, label, source, chat_key,"
            " sent_on, time, text, text_hash, run_id, added_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                m.language,
                contact_id,
                m.label,
                "import" if m.chat_key.startswith("import:") else "screen",
                m.chat_key,
                m.sent_on,
                m.time,
                m.text,
                text_hash(m.text),
                base + m.run,
                now,
            ),
        )
        added += cursor.rowcount
    for language in {m.language for m in messages}:
        conn.execute(
            "DELETE FROM style_sample WHERE language = ? AND id NOT IN"
            " (SELECT id FROM style_sample WHERE language = ? ORDER BY id DESC LIMIT ?)",
            (language, language, POOL_LIMIT),
        )
    return added


def newest(messages: Sequence[OwnMessage]) -> list[OwnMessage]:
    """At most POOL_LIMIT messages per language, the latest ones: the rest would be trimmed."""
    kept, seen = [], Counter()
    for message in reversed(messages):
        if seen[message.language] < POOL_LIMIT:
            seen[message.language] += 1
            kept.append(message)
    return kept[::-1]


def forget_import(conn, other_author: str) -> list[str]:
    """Delete what was imported from a chat with this person. Returns the languages touched."""
    key = import_key([other_author])
    languages = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT language FROM style_sample WHERE chat_key = ?", (key,)
        ).fetchall()
    ]
    conn.execute("DELETE FROM style_sample WHERE chat_key = ?", (key,))
    return languages


def samples(conn, language: str) -> list[OwnMessage]:
    """Kept messages, oldest first. The label is the contact's, or the one chosen on import."""
    rows = conn.execute(
        "SELECT s.text, s.chat_key, s.sent_on, s.time, s.run_id, COALESCE(c.label, s.label)"
        " FROM style_sample s LEFT JOIN contact c ON c.id = s.contact_id"
        " WHERE s.language = ? AND s.excluded = 0 ORDER BY s.id",
        (language,),
    ).fetchall()
    return [OwnMessage(r[0], language, r[1], r[2], r[3], r[4], r[5]) for r in rows]


def languages(conn) -> list[tuple[str, int]]:
    rows = conn.execute(
        "SELECT language, count(*) FROM style_sample WHERE excluded = 0"
        " GROUP BY language ORDER BY count(*) DESC, language"
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def exclude_example(conn, language: str, text: str) -> None:
    conn.execute(
        "UPDATE style_sample SET excluded = 1 WHERE language = ? AND text = ?", (language, text)
    )


def save_profile(conn, profile: StyleProfile, extracted: bool) -> None:
    """Keep the user's notes. An extraction also records how far the samples went."""
    conn.execute(
        "INSERT INTO style_profile (language, data) VALUES (?, ?)"
        " ON CONFLICT (language) DO UPDATE SET data = excluded.data",
        (profile.language, profile.model_dump_json()),
    )
    if extracted:
        last = conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM style_sample WHERE language = ?",
            (profile.language,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE style_profile SET extracted_at = ?, last_sample_id = ? WHERE language = ?",
            (profile.updated_at, last, profile.language),
        )


def load_profile(conn, language: str) -> tuple[StyleProfile, str] | None:
    """The profile and the user's notes, or None."""
    row = conn.execute(
        "SELECT data, manual FROM style_profile WHERE language = ?", (language,)
    ).fetchone()
    if row is None:
        return None
    return StyleProfile.model_validate_json(row[0]), row[1]


def save_manual(conn, language: str, text: str, now: str = "") -> None:
    """Keep the notes even before a profile exists: an empty one is created for them."""
    empty = StyleProfile(
        language=language, messages=0, stats=StyleStats(), examples=[], updated_at=now
    )
    conn.execute(
        "INSERT INTO style_profile (language, data, manual) VALUES (?, ?, ?)"
        " ON CONFLICT (language) DO UPDATE SET manual = excluded.manual",
        (language, empty.model_dump_json(), text.strip()),
    )


def extraction_due(conn, language: str, now: datetime) -> bool:
    """EXTRACT_MIN new messages since the last extraction, and that one a day old."""
    row = conn.execute(
        "SELECT COALESCE(attempted_at, extracted_at), last_sample_id FROM style_profile"
        " WHERE language = ?",
        (language,),
    ).fetchone()
    extracted_at, last_id = row if row else (None, 0)
    new = conn.execute(
        "SELECT count(*) FROM style_sample WHERE language = ? AND excluded = 0 AND id > ?",
        (language, last_id),
    ).fetchone()[0]
    if new < EXTRACT_MIN:
        return False
    return extracted_at is None or now - datetime.fromisoformat(extracted_at) >= EXTRACT_EVERY


def mark_attempt(conn, language: str, now: datetime) -> None:
    """An extraction is about to be paid for: the daily limit counts it even if it fails."""
    conn.execute(
        "UPDATE style_profile SET attempted_at = ? WHERE language = ?",
        (now.isoformat(timespec="seconds"), language),
    )


def remember_pasted(conn, text: str, now: datetime) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO pasted (text_hash, at) VALUES (?, ?)",
        (text_hash(text), now.isoformat()),
    )


def pasted_hashes(conn, now: datetime) -> set[str]:
    """Hashes of what the app pasted or copied in the last PASTED_DAYS days."""
    cutoff = (now - timedelta(days=PASTED_DAYS)).isoformat()
    conn.execute("DELETE FROM pasted WHERE at < ?", (cutoff,))
    return {r[0] for r in conn.execute("SELECT text_hash FROM pasted").fetchall()}


def reset(conn) -> None:
    """Forget the learned style. Contacts and their labels stay."""
    # The pasted hashes stay: what the app wrote must not come back as the user's style.
    for table in ("style_profile", "style_sample"):
        conn.execute(f"DELETE FROM {table}")
