from dataclasses import replace

from respondoia import contacts
from respondoia.memory import store
from respondoia.memory.merge import Stored, fingerprint, merge

NOW = "2026-11-17T10:00:00"


def msgs(n, start=0, day="2026-11-10", word="pedido"):
    return [
        Stored(
            day,
            f"10:{(start + i) % 60:02d}",
            "contact" if i % 2 else "user",
            "",
            "text",
            f"{word} {start + i}",
            "",
        )
        for i in range(n)
    ]


def contact(conn, name="Ana"):
    return contacts.see(conn, name, False, None, NOW).id


def save(conn, cid, incoming):
    current = store.segments(conn, cid)
    result = merge([s.messages for s in current], incoming)
    return store.save_merge(conn, cid, result, NOW, current)


def ids(conn, cid):
    return [
        r[0]
        for r in conn.execute(
            "SELECT id FROM history_message WHERE contact_id = ? ORDER BY id", (cid,)
        )
    ]


def test_append_inserts_only_the_new_rows(db):
    base = msgs(12)
    with db.transaction() as conn:
        cid = contact(conn)
        assert save(conn, cid, base[:10]) == 10
        before = ids(conn, cid)
        assert save(conn, cid, base[8:]) == 2
        assert ids(conn, cid)[:10] == before
        assert [m.text for m in store.segments(conn, cid)[0].messages] == [m.text for m in base]


def test_prepend_keeps_order(db):
    base = msgs(20)
    with db.transaction() as conn:
        cid = contact(conn)
        save(conn, cid, base[10:])
        assert save(conn, cid, base[:12]) == 10
        (segment,) = store.segments(conn, cid)
        assert [m.text for m in segment.messages] == [m.text for m in base]


def test_join_two_segments_into_one(db):
    base = msgs(40)
    with db.transaction() as conn:
        cid = contact(conn)
        save(conn, cid, base[:10])
        save(conn, cid, base[30:])
        assert len(store.segments(conn, cid)) == 2
        save(conn, cid, base[8:32])
        (segment,) = store.segments(conn, cid)
        assert [m.text for m in segment.messages] == [m.text for m in base]
        assert store.count(conn, cid) == 40


def test_edit_updates_the_row(db):
    base = msgs(10)
    with db.transaction() as conn:
        cid = contact(conn)
        save(conn, cid, base)
        edited = list(base)
        edited[4] = replace(edited[4], text="texto corrigido")
        assert save(conn, cid, edited) == 0
        assert store.segments(conn, cid)[0].messages[4].text == "texto corrigido"


def test_search_ignores_accents_and_excludes_visible(db):
    with db.transaction() as conn:
        cid = contact(conn)
        rows = msgs(3) + [
            Stored("2026-11-10", "11:00", "contact", "", "text", "manda o orçamento do bolo", "")
        ]
        save(conn, cid, rows)
        found = store.search(conn, cid, ["orcamento"], set(), 5)
        assert len(found) == 1
        assert store.search(conn, cid, ["orcamento"], {fingerprint(rows[-1])}, 5) == []
        assert store.search(conn, cid, [], set(), 5) == []


def test_search_stays_inside_the_contact(db):
    with db.transaction() as conn:
        a, b = contact(conn, "Ana"), contact(conn, "Bia")
        save(conn, a, msgs(3, word="orçamento"))
        assert store.search(conn, b, ["orcamento"], set(), 5) == []


def test_around_gives_the_neighbours(db):
    with db.transaction() as conn:
        cid = contact(conn)
        save(conn, cid, msgs(10))
        (hit,) = store.search(conn, cid, ["5"], set(), 1)
        assert [m.text for m in store.around(conn, hit)] == ["pedido 4", "pedido 5", "pedido 6"]


def test_rows_after_follow_the_chat_order(db):
    base = msgs(20)
    with db.transaction() as conn:
        cid = contact(conn)
        save(conn, cid, base[10:])
        save(conn, cid, base[:12])
        rows = store.rows_after(conn, cid, 0, 100)
        assert [m.text for _, m in rows] == [m.text for m in base]
        last = max(i for i, _ in rows)
        assert store.rows_after(conn, cid, last, 100) == []


def test_forget_contact_removes_messages_index_and_memory(db):
    with db.transaction() as conn:
        cid = contact(conn)
        save(conn, cid, msgs(5, word="orçamento"))
        store.save_memory(conn, cid, "resumo", 5, NOW)
        store.mark_loaded(conn, cid, True, NOW)
        assert store.forget_contact(conn, [cid]) == 5
        assert store.count(conn, cid) == 0
        assert store.search(conn, cid, ["orcamento"], set(), 5) == []
        assert (
            conn.execute(
                "SELECT count(*) FROM history_fts WHERE history_fts MATCH 'orcamento'"
            ).fetchone()[0]
            == 0
        )
        assert store.load_memory(conn, cid) is None
        assert store.status(conn, cid).messages == 0 and store.status(conn, cid).loaded_at is None


def test_forget_by_name_cascades_history(db):
    with db.transaction() as conn:
        cid = contact(conn)
        save(conn, cid, msgs(5, word="orçamento"))
        store.save_memory(conn, cid, "resumo", 5, NOW)
        contacts.forget(conn, "Ana")
        assert conn.execute("SELECT count(*) FROM history_message").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM contact_memory").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT count(*) FROM history_fts WHERE history_fts MATCH 'orcamento'"
            ).fetchone()[0]
            == 0
        )


def test_delete_all_leaves_the_index_empty(db):
    with db.transaction() as conn:
        for name in ("Ana", "Bia"):
            cid = contact(conn, name)
            save(conn, cid, msgs(5, word="orçamento"))
            store.save_memory(conn, cid, "resumo", 5, NOW)
        store.forget_all(conn)
        for table in ("history_message", "contact_memory", "history_state"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT count(*) FROM history_fts WHERE history_fts MATCH 'orcamento'"
            ).fetchone()[0]
            == 0
        )
    db.checkpoint()


def test_retention_uses_seen_at_without_a_date(db):
    with db.transaction() as conn:
        cid = contact(conn)
        old = Stored("2025-01-02", "10:00", "contact", "", "text", "antiga", "")
        undated = Stored("", "10:01", "contact", "", "text", "sem data", "")
        recent = Stored("2026-11-10", "10:02", "contact", "", "text", "recente", "")
        for i, message in enumerate((old, undated, recent)):
            conn.execute(
                "INSERT INTO history_message (contact_id, segment, ord, sent_on, time, author,"
                " kind, text, fingerprint, seen_at) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cid,
                    i,
                    message.sent_on,
                    message.time,
                    message.author,
                    message.kind,
                    message.text,
                    fingerprint(message),
                    "2025-01-03T10:00:00" if i < 2 else NOW,
                ),
            )
        store.save_memory(conn, cid, "resumo", 3, NOW)
        assert store.apply_retention(conn, "2025-11-17") == 2
        assert [m.text for s in store.segments(conn, cid) for m in s.messages] == ["recente"]
        assert store.load_memory(conn, cid).text == "resumo"


def test_status_reports_a_gap(db):
    with db.transaction() as conn:
        cid = contact(conn)
        save(conn, cid, msgs(5, day="2026-11-03"))
        assert store.status(conn, cid).gap_since == ""
        save(conn, cid, msgs(5, start=30, day="2026-11-12"))
        status = store.status(conn, cid)
        assert (status.messages, status.gap_since, status.last_day) == (
            10,
            "2026-11-03",
            "2026-11-12",
        )


WORD = "zzqwertyword"


def index_bytes(conn) -> bytes:
    return b"".join(row[0] or b"" for row in conn.execute("SELECT block FROM history_fts_data"))


def stored_word(conn, name="Ana", day="2026-11-10"):
    cid = contact(conn, name)
    save(conn, cid, msgs(4, word=WORD, day=day))
    assert WORD.encode() in index_bytes(conn)
    return cid


def test_deleted_words_leave_the_search_index(db):
    paths = {
        "contact": lambda conn, cid: store.forget_contact(conn, [cid]),
        "all": lambda conn, cid: store.forget_all(conn),
        "cascade": lambda conn, cid: contacts.forget(conn, "Ana"),
        "retention": lambda conn, cid: store.apply_retention(conn, "2026-12-01"),
    }
    for name, delete in paths.items():
        with db.transaction() as conn:
            cid = stored_word(conn)
        with db.transaction() as conn:
            delete(conn, cid)
        # The index writes a delete when the transaction commits.
        with db.transaction() as conn:
            assert WORD.encode() not in index_bytes(conn), name


def test_row_ids_are_never_reused(db):
    with db.transaction() as conn:
        cid = contact(conn)
        save(conn, cid, msgs(5))
        highest = max(store.segments(conn, cid)[0].ids)
        store.forget_contact(conn, [cid])
        save(conn, cid, msgs(5, start=20))
        assert min(store.segments(conn, cid)[0].ids) > highest


def test_memory_round_trip_and_attempt(db):
    with db.transaction() as conn:
        cid = contact(conn)
        assert store.load_memory(conn, cid) is None
        store.mark_memory_attempt(conn, cid, NOW)
        assert store.load_memory(conn, cid).attempted_at == NOW
        store.save_memory(conn, cid, "resumo", 7, NOW, edited=True)
        row = store.load_memory(conn, cid)
        assert (row.text, row.through_id, row.edited) == ("resumo", 7, True)
