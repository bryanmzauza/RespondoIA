"""Contact records: who the app has seen, their label and last language.

A contact is found by its displayed name. Two contacts with the same name cannot be
told apart from the title alone, so a lookup only succeeds on a unique match.
"""

from dataclasses import dataclass

from respondoia.privacy import normalize_name

_COLUMNS = "id, name, is_group, label, label_source, language"


@dataclass(frozen=True)
class Contact:
    id: int
    name: str
    is_group: bool
    label: str | None
    label_source: str | None  # "model", "user" or None
    language: str | None


def _rows(conn, title: str) -> list[Contact]:
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM contact WHERE name_key = ?", (normalize_name(title),)
    ).fetchall()
    return [Contact(r[0], r[1], bool(r[2]), r[3], r[4], r[5]) for r in rows]


def find(conn, title: str) -> Contact | None:
    """Exactly one match, else None."""
    rows = _rows(conn, title)
    return rows[0] if len(rows) == 1 else None


def see(conn, title: str, is_group: bool, language: str | None, now: str) -> Contact | None:
    """Create the record the first time, update it after. None when the name is ambiguous."""
    rows = _rows(conn, title)
    if not rows:
        conn.execute(
            "INSERT INTO contact (name, name_key, is_group, language, first_seen, last_seen)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (title.strip(), normalize_name(title), int(is_group), language, now, now),
        )
        return find(conn, title)
    if len(rows) > 1:
        return None
    conn.execute(
        "UPDATE contact SET last_seen = ?, is_group = ?, language = COALESCE(?, language)"
        " WHERE id = ?",
        (now, int(is_group), language, rows[0].id),
    )
    return find(conn, title)


def set_label(conn, contact_id: int, label: str | None, source: str) -> None:
    """A model guess never replaces what the user chose, including "no label"."""
    query = "UPDATE contact SET label = ?, label_source = ? WHERE id = ?"
    if source == "model":
        query += " AND (label_source IS NULL OR label_source != 'user')"
    conn.execute(query, (label, source, contact_id))


def forget(conn, title: str) -> int:
    """Delete every record with this name. Their style samples go with them."""
    cursor = conn.execute("DELETE FROM contact WHERE name_key = ?", (normalize_name(title),))
    return cursor.rowcount
