from respondoia import contacts
from respondoia.style import store
from respondoia.style.sampler import OwnMessage

STAMP = "2026-11-17T10:00:00"


def test_see_creates_the_contact_once(db):
    with db.transaction() as conn:
        first = contacts.see(conn, "Marina Duarte", False, "pt", STAMP)
        again = contacts.see(conn, " marina  duarte ", False, None, "2026-11-18T10:00:00")
        assert first.id == again.id and again.language == "pt"
        assert conn.execute("SELECT count(*) FROM contact").fetchone()[0] == 1
        assert conn.execute("SELECT last_seen FROM contact").fetchone()[0] == "2026-11-18T10:00:00"


def test_find_needs_a_unique_match(db):
    with db.transaction() as conn:
        assert contacts.find(conn, "Ana") is None
        contacts.see(conn, "Ana", False, None, STAMP)
        assert contacts.find(conn, "ana").name == "Ana"
        conn.execute(
            "INSERT INTO contact (name, name_key, first_seen, last_seen) VALUES ('Ana', 'ana', '', '')"
        )
        assert contacts.find(conn, "Ana") is None
        assert contacts.see(conn, "Ana", False, None, STAMP) is None


def test_user_label_beats_a_model_guess(db):
    with db.transaction() as conn:
        contact = contacts.see(conn, "Marina Duarte", False, "pt", STAMP)
        contacts.set_label(conn, contact.id, "client", "model")
        assert contacts.find(conn, "Marina Duarte").label == "client"
        contacts.set_label(conn, contact.id, "friend", "user")
        contacts.set_label(conn, contact.id, "supplier", "model")
        found = contacts.find(conn, "Marina Duarte")
        assert (found.label, found.label_source) == ("friend", "user")
        contacts.set_label(conn, contact.id, None, "user")
        contacts.set_label(conn, contact.id, "client", "model")
        found = contacts.find(conn, "Marina Duarte")
        assert (found.label, found.label_source) == (None, "user")


def test_forget_removes_the_contact_and_its_samples(db):
    with db.transaction() as conn:
        contact = contacts.see(conn, "Marina", False, "pt", STAMP)
        messages = [OwnMessage(f"msg {n}", "pt", f"contact:{contact.id}", "", f"{n}", 0, None) for n in range(3)]
        store.add_samples(conn, messages, contact.id, STAMP)
        assert contacts.forget(conn, "  marina ") == 1
        assert conn.execute("SELECT count(*) FROM contact").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM style_sample").fetchone()[0] == 0


def test_group_flag_is_sticky(db):
    with db.transaction() as conn:
        contacts.see(conn, "Família", True, None, STAMP)
        assert contacts.see(conn, "Família", False, None, STAMP).is_group


def test_namesake_makes_the_name_ambiguous(db):
    with db.transaction() as conn:
        first = contacts.see(conn, "Ana", False, None, STAMP)
        second = contacts.add_namesake(conn, "Ana", False, "2026-11-18T10:00:00")
        assert second.id != first.id and contacts.find(conn, "Ana") is None
        assert [c.id for c in contacts.candidates(conn, "ana")] == [first.id, second.id]
        assert contacts.see(conn, "Ana", False, None, STAMP) is None


def test_history_fields(db):
    with db.transaction() as conn:
        cid = contacts.see(conn, "Ana", False, None, STAMP).id
        contacts.mark_used(conn, cid, STAMP)
        contacts.set_history(conn, cid, 0)
        contacts.set_avatar(conn, cid, "ab12")
        found = contacts.find(conn, "Ana")
        assert (found.used_at, found.history, found.avatar, found.first_seen) == (
            STAMP, 0, "ab12", STAMP)
