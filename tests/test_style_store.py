from datetime import datetime, timedelta

from respondoia import contacts
from respondoia.style import store
from respondoia.style.profile import StyleProfile
from respondoia.style.sampler import OwnMessage, StyleStats, text_hash

NOW = datetime(2026, 11, 17, 10, 0)
STAMP = NOW.isoformat()


def own(text, run=0, chat_key="contact:1", language="pt", label=None, time="09:00"):
    return OwnMessage(text, language, chat_key, "", time, run, label)


def a_profile(language="pt", examples=("fechado então",)):
    return StyleProfile(
        language=language,
        messages=1,
        stats=StyleStats(messages=1),
        examples=list(examples),
        updated_at=STAMP,
    )


def test_duplicates_are_ignored(db):
    with db.transaction() as conn:
        assert store.add_samples(conn, [own("oi tudo bem"), own("bora sim", time="09:01")], None, STAMP) == 2
        assert store.add_samples(conn, [own("oi tudo bem")], None, STAMP) == 0
        assert [m.text for m in store.samples(conn, "pt")] == ["oi tudo bem", "bora sim"]


def test_run_ids_do_not_collide_across_batches(db):
    with db.transaction() as conn:
        store.add_samples(conn, [own("um"), own("dois", time="09:01")], None, STAMP)
        store.add_samples(conn, [own("três", time="09:02")], None, STAMP)
        runs = [m.run for m in store.samples(conn, "pt")]
    assert runs[0] == runs[1] and runs[2] != runs[0]


def test_pool_keeps_the_newest_up_to_the_limit(db, monkeypatch):
    monkeypatch.setattr(store, "POOL_LIMIT", 5)
    with db.transaction() as conn:
        store.add_samples(conn, [own(f"msg {n}", time=f"09:{n:02d}") for n in range(8)], None, STAMP)
        store.add_samples(conn, [own("in english", language="en")], None, STAMP)
        assert [m.text for m in store.samples(conn, "pt")] == [f"msg {n}" for n in range(3, 8)]
        assert store.languages(conn) == [("pt", 5), ("en", 1)]


def test_screen_samples_take_the_contact_label(db):
    with db.transaction() as conn:
        contact = contacts.see(conn, "Marina Duarte", False, "pt", STAMP)
        contacts.set_label(conn, contact.id, "client", "user")
        store.add_samples(conn, [own("bom dia")], contact.id, STAMP)
        store.add_samples(conn, [own("oi", chat_key="import:x", label="friend")], None, STAMP)
        assert [(m.text, m.label) for m in store.samples(conn, "pt")] == [
            ("bom dia", "client"),
            ("oi", "friend"),
        ]


def test_manual_notes_survive_a_new_profile(db):
    with db.transaction() as conn:
        store.save_profile(conn, a_profile(), extracted=False)
        store.save_manual(conn, "pt", "nunca uso prezado")
        store.save_profile(conn, a_profile(examples=("outro",)), extracted=True)
        profile, manual = store.load_profile(conn, "pt")
    assert manual == "nunca uso prezado" and profile.examples == ["outro"]
    with db.transaction() as conn:
        assert store.load_profile(conn, "en") is None


def test_excluded_example_is_not_picked_again(db):
    with db.transaction() as conn:
        store.add_samples(conn, [own("fechado então, até amanhã")], None, STAMP)
        store.exclude_example(conn, "pt", "fechado então, até amanhã")
        assert store.samples(conn, "pt") == []


def test_extraction_due_rules(db):
    with db.transaction() as conn:
        store.add_samples(conn, [own(f"m {n}", time=f"{n}") for n in range(39)], None, STAMP)
        assert not store.extraction_due(conn, "pt", NOW)
        store.add_samples(conn, [own("m 39", time="39")], None, STAMP)
        assert store.extraction_due(conn, "pt", NOW)
        store.save_profile(conn, a_profile(), extracted=True)
        store.add_samples(conn, [own(f"n {n}", time=f"{n}") for n in range(40)], None, STAMP)
        assert not store.extraction_due(conn, "pt", NOW + timedelta(hours=2))
        assert store.extraction_due(conn, "pt", NOW + timedelta(hours=25))


def test_pasted_hashes_last_a_year(db):
    with db.transaction() as conn:
        store.remember_pasted(conn, "Consigo sim", NOW)
        assert store.pasted_hashes(conn, NOW + timedelta(days=200)) == {text_hash("Consigo sim")}
        assert store.pasted_hashes(conn, NOW + timedelta(days=366)) == set()


def test_reset_clears_style_and_keeps_labels(db):
    with db.transaction() as conn:
        contact = contacts.see(conn, "Marina Duarte", False, "pt", STAMP)
        contacts.set_label(conn, contact.id, "client", "user")
        store.add_samples(conn, [own("bom dia")], contact.id, STAMP)
        store.save_profile(conn, a_profile(), extracted=False)
        store.remember_pasted(conn, "x", NOW)
        store.reset(conn)
        assert store.samples(conn, "pt") == [] and store.load_profile(conn, "pt") is None
        assert store.pasted_hashes(conn, NOW) == set()
        assert contacts.find(conn, "Marina Duarte").label == "client"
