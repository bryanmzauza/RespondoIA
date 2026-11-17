"""Chat history in the encrypted database: messages by contact, the search index and the
contact memory. Every function takes an open transaction; nothing here commits.

Messages live in segments, each a run the app knows to be continuous, ordered inside by
`ord`. Two segments mean a gap between them that has not been loaded yet.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from respondoia.memory.merge import Merge, Stored, fingerprint

SPACING = 1024  # room between neighbours, so a run grows at both ends without renumbering
_FIELDS = "sent_on, time, author, sender, kind, text, quote"


@dataclass(frozen=True)
class Segment:
    id: int
    messages: tuple[Stored, ...]
    ids: tuple[int, ...]  # history_message.id of each message
    ords: tuple[int, ...]


@dataclass(frozen=True)
class Status:
    messages: int
    last_day: str  # "" unknown
    gap_since: str  # last day of the segment before the newest one, "" without a gap
    reached_start: bool
    loaded_at: str | None


@dataclass(frozen=True)
class MemoryRow:
    text: str
    edited: bool
    through_id: int  # highest history_message.id already summarized
    updated_at: str | None
    attempted_at: str | None


def _first_day(segment: Segment) -> str:
    return next((m.sent_on for m in segment.messages if m.sent_on), "")


def _last_day(segment: Segment) -> str:
    return next((m.sent_on for m in reversed(segment.messages) if m.sent_on), "")


def segments(conn, contact_id: int) -> list[Segment]:
    """Oldest first: by the first known day, then by when the segment was created."""
    rows = conn.execute(
        f"SELECT segment, id, ord, {_FIELDS} FROM history_message WHERE contact_id = ?"
        " ORDER BY segment, ord",
        (contact_id,),
    ).fetchall()
    grouped: dict[int, list] = {}
    for row in rows:
        grouped.setdefault(row[0], []).append(row)
    found = [
        Segment(
            segment,
            tuple(Stored(*r[3:]) for r in members),
            tuple(r[1] for r in members),
            tuple(r[2] for r in members),
        )
        for segment, members in grouped.items()
    ]
    # Undated segments sort by creation; a dated one is placed by its first day.
    return sorted(found, key=lambda s: (_first_day(s) or "9999", s.ids[0]))


def recent(conn, contact_id: int, limit: int = 300) -> tuple[Stored, ...]:
    """The tail of the newest segment."""
    found = segments(conn, contact_id)
    return found[-1].messages[-limit:] if found else ()


def _insert(conn, contact_id: int, segment: int, order: int, message: Stored, now: str) -> None:
    conn.execute(
        f"INSERT INTO history_message (contact_id, segment, ord, {_FIELDS}, fingerprint, seen_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            contact_id,
            segment,
            order,
            message.sent_on,
            message.time,
            message.author,
            message.sender,
            message.kind,
            message.text,
            message.quote,
            fingerprint(message),
            now,
        ),
    )


def _matches(old: Stored, new: Stored) -> bool:
    return fingerprint(old) == fingerprint(new) or (old.author, old.time, old.kind) == (
        new.author,
        new.time,
        new.kind,
    )


def _grow_in_place(conn, contact_id: int, segment: Segment, result: Sequence[Stored], now: str):
    """Rows only added before or after the stored ones, maybe some edited: write just that.
    None when the result is not shaped like that."""
    old = segment.messages
    for start in range(len(result) - len(old) + 1):
        if all(_matches(o, result[start + i]) for i, o in enumerate(old)):
            break
    else:
        return None
    for i, (message, row_id) in enumerate(zip(result[start : start + len(old)], segment.ids)):
        if message != old[i]:
            conn.execute(
                "UPDATE history_message SET sent_on = ?, text = ?, quote = ?, fingerprint = ?"
                " WHERE id = ?",
                (message.sent_on, message.text, message.quote, fingerprint(message), row_id),
            )
    before, after = result[:start], result[start + len(old) :]
    for i, message in enumerate(before):
        _insert(
            conn,
            contact_id,
            segment.id,
            segment.ords[0] - SPACING * (len(before) - i),
            message,
            now,
        )
    for i, message in enumerate(after):
        _insert(conn, contact_id, segment.id, segment.ords[-1] + SPACING * (i + 1), message, now)
    return len(before) + len(after)


def save_merge(conn, contact_id: int, merge: Merge, now: str, current: Sequence[Segment]) -> int:
    """Write a merge computed against `current`. Returns the rows inserted."""
    if not merge.result:
        return 0
    if len(merge.replaces) == 1:
        grown = _grow_in_place(conn, contact_id, current[merge.replaces[0]], merge.result, now)
        if grown is not None:
            return grown
    absorbed = [current[k].id for k in merge.replaces]
    for segment_id in absorbed:
        conn.execute(
            "DELETE FROM history_message WHERE contact_id = ? AND segment = ?",
            (contact_id, segment_id),
        )
    number = conn.execute(
        "SELECT coalesce(max(segment), 0) + 1 FROM history_message WHERE contact_id = ?",
        (contact_id,),
    ).fetchone()[0]
    for i, message in enumerate(merge.result):
        _insert(conn, contact_id, number, i * SPACING, message, now)
    return len(merge.result) - sum(len(current[k].messages) for k in merge.replaces)


def count(conn, contact_id: int) -> int:
    return conn.execute(
        "SELECT count(*) FROM history_message WHERE contact_id = ?", (contact_id,)
    ).fetchone()[0]


def status(conn, contact_id: int) -> Status:
    found = segments(conn, contact_id)
    state = conn.execute(
        "SELECT loaded_at, reached_start FROM history_state WHERE contact_id = ?", (contact_id,)
    ).fetchone()
    return Status(
        messages=sum(len(s.messages) for s in found),
        last_day=_last_day(found[-1]) if found else "",
        gap_since=_last_day(found[-2]) if len(found) > 1 else "",
        reached_start=bool(state and state[1]),
        loaded_at=state[0] if state else None,
    )


def mark_loaded(conn, contact_id: int, reached_start: bool, now: str) -> None:
    conn.execute(
        "INSERT INTO history_state (contact_id, loaded_at, reached_start) VALUES (?, ?, ?)"
        " ON CONFLICT(contact_id) DO UPDATE SET loaded_at = excluded.loaded_at,"
        " reached_start = max(reached_start, excluded.reached_start)",
        (contact_id, now, int(reached_start)),
    )


def search(conn, contact_id: int, terms: Sequence[str], exclude: set[str], limit: int) -> list[int]:
    """Ids of the best matches for any of the terms, without the excluded fingerprints."""
    if not terms or limit <= 0:
        return []
    query = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
    rows = conn.execute(
        "SELECT m.id, m.fingerprint FROM history_fts JOIN history_message m"
        " ON m.id = history_fts.rowid WHERE history_fts MATCH ? AND m.contact_id = ?"
        " ORDER BY bm25(history_fts) LIMIT ?",
        (query, contact_id, limit + len(exclude)),
    ).fetchall()
    return [row_id for row_id, mark in rows if mark not in exclude][:limit]


def around(conn, message_id: int) -> tuple[Stored, ...]:
    """The message with the one before and the one after it in its segment."""
    hit = conn.execute(
        "SELECT contact_id, segment, ord FROM history_message WHERE id = ?", (message_id,)
    ).fetchone()
    if hit is None:
        return ()
    rows = conn.execute(
        f"SELECT {_FIELDS} FROM ("
        f" SELECT ord, {_FIELDS} FROM history_message WHERE contact_id = ? AND segment = ?"
        " AND ord < ? ORDER BY ord DESC LIMIT 1) UNION ALL"
        f" SELECT {_FIELDS} FROM history_message WHERE id = ? UNION ALL"
        f" SELECT {_FIELDS} FROM ("
        f" SELECT ord, {_FIELDS} FROM history_message WHERE contact_id = ? AND segment = ?"
        " AND ord > ? ORDER BY ord LIMIT 1)",
        (*hit, message_id, *hit),
    ).fetchall()
    return tuple(Stored(*r) for r in rows)


def rows_after(conn, contact_id: int, after_id: int, limit: int) -> list[tuple[int, Stored]]:
    """Messages with an id above after_id, in chat order."""
    out = []
    for segment in segments(conn, contact_id):
        out += [(i, m) for i, m in zip(segment.ids, segment.messages) if i > after_id]
    return out[:limit]


def load_memory(conn, contact_id: int) -> MemoryRow | None:
    row = conn.execute(
        "SELECT text, edited, through_id, updated_at, attempted_at FROM contact_memory"
        " WHERE contact_id = ?",
        (contact_id,),
    ).fetchone()
    return MemoryRow(row[0], bool(row[1]), *row[2:]) if row else None


def save_memory(
    conn, contact_id: int, text: str, through_id: int, now: str, edited: bool = False
) -> None:
    conn.execute(
        "INSERT INTO contact_memory (contact_id, text, edited, through_id, updated_at)"
        " VALUES (?, ?, ?, ?, ?) ON CONFLICT(contact_id) DO UPDATE SET text = excluded.text,"
        " edited = excluded.edited, through_id = excluded.through_id,"
        " updated_at = excluded.updated_at",
        (contact_id, text, int(edited), through_id, now),
    )


def mark_memory_attempt(conn, contact_id: int, now: str) -> None:
    conn.execute(
        "INSERT INTO contact_memory (contact_id, text, attempted_at) VALUES (?, '', ?)"
        " ON CONFLICT(contact_id) DO UPDATE SET attempted_at = excluded.attempted_at",
        (contact_id, now),
    )


def forget_contact(conn, contact_ids: Sequence[int]) -> int:
    """Messages, memory and load state of these contacts. Returns the messages deleted."""
    deleted = 0
    for contact_id in contact_ids:
        deleted += conn.execute(
            "DELETE FROM history_message WHERE contact_id = ?", (contact_id,)
        ).rowcount
        conn.execute("DELETE FROM contact_memory WHERE contact_id = ?", (contact_id,))
        conn.execute("DELETE FROM history_state WHERE contact_id = ?", (contact_id,))
    return deleted


def forget_all(conn) -> None:
    for table in ("history_message", "contact_memory", "history_state"):
        conn.execute(f"DELETE FROM {table}")


def apply_retention(conn, cutoff_day: str) -> int:
    """Delete messages older than the cutoff. Without a day, the day they were seen counts.
    The contact memory stays."""
    return conn.execute(
        "DELETE FROM history_message WHERE (sent_on != '' AND sent_on < ?)"
        " OR (sent_on = '' AND seen_at < ?)",
        (cutoff_day, cutoff_day),
    ).rowcount
