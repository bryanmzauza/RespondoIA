from datetime import datetime, timedelta

import pytest

from respondoia import contacts
from respondoia.config import Settings
from respondoia.domain import ChatLine
from respondoia.llm import ExtractResult, LlmError
from respondoia.memory import store
from respondoia.memory.recall import ContactMemory
from respondoia.memory.service import HistoryService

ON = Settings(history_enabled=True)


class FakeClient:
    def __init__(self, fail: str | None = None):
        self.texts: list[str] = []
        self.fail = fail

    def extract(self, system, text, schema):
        self.texts.append(text)
        if self.fail:
            raise LlmError(self.fail)
        value = ContactMemory(text=f"resumo {len(self.texts)}")
        return ExtractResult(value, "claude-haiku-5-5", 100, 20, 5)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 17, 10, 0)

    def __call__(self):
        return self.now


def line(text, author="contact", time="10:00", day=""):
    return ChatLine(author, "", time, "text", text, "", day=day)


def lines(prefix, n, start=0, day="2026-11-17"):
    out = [
        line(f"{prefix} {start + i}", "user" if i % 2 else "contact", f"09:{start + i:02d}")
        for i in range(n)
    ]
    out[0] = ChatLine(**{**out[0].__dict__, "day": day})
    return out


def service(db, settings=ON, client=None, clock=None):
    return HistoryService(db, lambda: client, lambda: settings, clock or Clock())


def see(db, title, is_group=False):
    """What the style service does first on every hotkey press."""
    with db.transaction() as conn:
        contacts.see(conn, title, is_group, None, "2026-11-17T09:00:00")


def observe(history, db, title, screen, is_group=False, avatar=""):
    see(db, title, is_group)
    return history.observe(title, screen, is_group, avatar)


def test_off_by_default_stores_nothing(db):
    history = service(db, Settings())
    result = observe(history, db, "Ana", lines("pedido", 6))
    assert result.state == "off" and result.memory == "" and result.snippets == ()
    with db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM history_message").fetchone()[0] == 0
        assert contacts.find(conn, "Ana").used_at is not None


def test_nothing_attached_when_off_group_or_out_of_scope(db):
    history = service(db)
    assert observe(history, db, "Família", lines("x", 4), is_group=True).state == "group"
    observe(history, db, "Ana", lines("pedido", 6))
    history.set_contact_history("Ana", None, False)
    result = observe(history, db, "Ana", lines("pedido", 8))
    assert result.state == "out_of_scope" and result.memory == "" and result.snippets == ()
    with db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM history_message").fetchone()[0] == 0


def test_groups_can_be_included(db):
    history = service(db, Settings(history_enabled=True, history_include_groups=True))
    assert observe(history, db, "Família", lines("x", 4), is_group=True).state == "ready"


def test_first_use_stores_the_screen_and_reports_ready(db):
    result = observe(service(db), db, "Ana", lines("pedido", 6))
    assert (result.state, result.messages, result.added) == ("ready", 6, 6)
    assert result.owner == "Ana" and result.owner_since == "" and result.last_day == "2026-11-17"


def test_hotkey_twice_adds_nothing(db):
    history = service(db)
    observe(history, db, "Ana", lines("pedido", 6))
    again = observe(history, db, "Ana", lines("pedido", 6))
    assert (again.added, again.messages, again.gap_since) == (0, 6, "")


def test_unrelated_screen_for_a_known_name_asks(db):
    # Same person after a gap, or another person with the same name: nothing local tells,
    # and the only candidate holds history, so the app asks instead of guessing.
    history = service(db)
    observe(history, db, "Ana", lines("pedido", 6, day="2026-11-10"))
    result = history.observe("Ana", lines("outro assunto", 6, start=30), False, "")
    assert result.state == "ask"


def test_gap_after_confirmation_keeps_both_runs(db):
    history = service(db)
    first = observe(history, db, "Ana", lines("pedido", 6, day="2026-11-10"))
    result = history.confirm("Ana", lines("outro assunto", 6, start=30), first.contact_id)
    assert result.state == "ready" and result.gap_since == "2026-11-10"
    assert result.messages == 12


def test_ambiguous_identity_sends_no_memory_or_snippets(db):
    history = service(db)
    first = observe(history, db, "Ana", lines("orçamento bolo", 6))
    with db.transaction() as conn:
        store.save_memory(conn, first.contact_id, "ZZ-MEMORY-A", 6, "2026-11-17T10:00:00")
    result = history.observe("Ana", [line("e o orçamento do bolo?", time="11:00")], False, "")
    assert result.state == "ask" and result.memory == "" and result.snippets == ()
    assert result.candidates == ((first.contact_id, "2026-11-17"),)


def test_confirm_same_person_attaches_memory(db):
    history = service(db)
    first = observe(history, db, "Ana", lines("pedido", 6))
    with db.transaction() as conn:
        store.save_memory(conn, first.contact_id, "Cliente fiel.", 6, "2026-11-17T10:00:00")
    result = history.confirm("Ana", [line("oi de novo", time="12:00")], first.contact_id)
    assert result.state == "ready" and result.memory == "Cliente fiel."
    # The next press shows the confirmed message again: the messages decide from now on.
    again = [line("oi de novo", time="12:00"), line("e aí?", time="12:30")]
    assert history.observe("Ana", again, False, "").state == "ready"


def test_confirm_another_person_creates_a_namesake(db):
    history = service(db)
    first = observe(history, db, "Ana", lines("pedido", 6))
    with db.transaction() as conn:
        store.save_memory(conn, first.contact_id, "ZZ-MEMORY-A", 6, "2026-11-17T10:00:00")
    result = history.confirm("Ana", [line("sou a outra Ana", time="12:00")], None)
    assert result.state == "ready" and result.contact_id != first.contact_id
    assert result.memory == "" and result.owner_since == "2026-11-17"
    with db.transaction() as conn:
        assert len(contacts.candidates(conn, "Ana")) == 2


def test_an_answer_never_outlives_its_screen(db):
    # The user answers "another person" for Ana 2; Ana 1's chat must still be Ana 1.
    history = service(db)
    first = observe(history, db, "Ana", lines("bolo", 8))
    other = history.confirm("Ana", lines("reunião", 6, start=30), None)
    assert other.contact_id != first.contact_id
    back = history.observe("Ana", lines("bolo", 10, start=4), False, "")
    assert back.contact_id == first.contact_id and back.added == 6


def test_a_same_person_answer_does_not_open_the_memory_to_a_namesake(db):
    history = service(db)
    first = observe(history, db, "Ana", lines("bolo", 8))
    with db.transaction() as conn:
        store.save_memory(conn, first.contact_id, "ZZ-MEMORY-A", 8, "x")
    same = history.confirm("Ana", lines("depois da lacuna", 6, start=30), first.contact_id)
    assert same.memory == "ZZ-MEMORY-A"
    namesake = history.observe("Ana", lines("outra pessoa", 4, start=50), False, "")
    assert namesake.state == "ask" and namesake.memory == ""


def test_real_photo_decides_after_a_gap(db):
    history = service(db)
    first = observe(history, db, "Ana", lines("pedido", 6), avatar="ab12")
    result = history.observe("Ana", lines("outro assunto", 4, start=40), False, "ab12")
    assert result.state == "ready" and result.contact_id == first.contact_id


def test_snippets_find_an_old_agreement(db):
    clock = Clock()
    history = service(db, clock=clock)
    old = [
        line("quero o bolo de limão para o aniversário", time="10:00", day="2026-10-20"),
        line("fica R$ 85 com cobertura", "user", "10:05"),
        line("fechado então", time="10:06"),
        line("até lá", "user", "10:07"),
    ]
    observe(history, db, "Ana", old)
    clock.now += timedelta(days=28)
    screen = [
        old[2],
        old[3],
        line("oi! e aquele bolo de limão que combinamos?", time="09:00", day="2026-11-17"),
    ]
    result = history.observe("Ana", screen, False, "")
    texts = [m.text for s in result.snippets for m in s.messages]
    assert "quero o bolo de limão para o aniversário" in texts
    assert "fica R$ 85 com cobertura" in texts


def test_snippets_and_memory_are_masked_again(db):
    history = service(db)
    old = [
        line("orçamento do bolo", time="07:00"),
        line("certo", "user", "08:00"),
        line("obrigada", time="08:01"),
    ]
    first = observe(history, db, "Ana", old)
    with db.transaction() as conn:
        # As if an older version had stored the number unmasked.
        conn.execute(
            "UPDATE history_message SET text = 'orçamento do bolo, cpf 529.982.247-25'"
            " WHERE text = 'orçamento do bolo'"
        )
        store.save_memory(conn, first.contact_id, "cpf 529.982.247-25", 1, "x")
    screen = [old[1], old[2], line("e o orçamento?", time="09:00")]
    result = history.observe("Ana", screen, False, "")
    snippets = " ".join(m.text for s in result.snippets for m in s.messages)
    assert "[CPF]" in snippets and "[CPF]" in result.memory
    assert "247-25" not in snippets + result.memory


def test_memory_due_rules_and_one_attempt_a_day(db):
    clock = Clock()
    client = FakeClient()
    history = service(db, client=client, clock=clock)
    first = observe(history, db, "Ana", lines("pedido", 19))
    assert not history.update_memory_if_due(first.contact_id)  # 19 < 20
    observe(history, db, "Ana", lines("pedido", 21))
    assert history.update_memory_if_due(first.contact_id)
    assert history.memory(first.contact_id) == "resumo 1"
    observe(history, db, "Ana", lines("pedido", 50))
    clock.now += timedelta(hours=25)
    assert not history.update_memory_if_due(first.contact_id)  # 29 new < 30
    observe(history, db, "Ana", lines("pedido", 51))
    assert history.update_memory_if_due(first.contact_id)  # 30 new, a day later
    assert "resumo 1" in client.texts[-1]


def test_memory_failure_waits_a_day(db):
    clock = Clock()
    client = FakeClient(fail="network")
    history = service(db, client=client, clock=clock)
    first = observe(history, db, "Ana", lines("pedido", 25))
    assert not history.update_memory_if_due(first.contact_id)
    client.fail = None
    assert not history.update_memory_if_due(first.contact_id)
    clock.now += timedelta(hours=25)
    assert history.update_memory_if_due(first.contact_id)


def test_memory_is_split_in_blocks_and_masked(db):
    client = FakeClient()
    history = service(db, client=client)
    screen = lines("pedido", 250)
    screen[3] = line("meu cpf é 529.982.247-25", time="08:59")
    first = observe(history, db, "Ana", screen)
    assert history.update_memory_if_due(first.contact_id)
    assert len(client.texts) == 2 and "resumo 1" in client.texts[1]
    assert all("247-25" not in text for text in client.texts)


class DeletingClient(FakeClient):
    """Runs a deletion while the model call is in flight, as a click on Delete would."""

    def __init__(self, delete):
        super().__init__()
        self.delete = delete

    def extract(self, system, text, schema):
        self.delete()
        return super().extract(system, text, schema)


@pytest.mark.parametrize("path", ["all", "contact", "off"])
def test_a_deletion_during_a_memory_update_wins(db, path):
    client = DeletingClient(lambda: None)
    history = service(db, client=client)
    first = observe(history, db, "Ana", lines("pedido", 25))
    client.delete = {
        "all": history.forget_all,
        "contact": lambda: history.forget_contact("Ana", None),
        "off": lambda: history.set_contact_history("Ana", None, False),
    }[path]
    assert not history.update_memory_if_due(first.contact_id)
    with db.transaction() as conn:
        assert store.load_memory(conn, first.contact_id) is None


def test_a_user_edit_during_a_memory_update_wins(db):
    client = DeletingClient(lambda: None)
    history = service(db, client=client)
    first = observe(history, db, "Ana", lines("pedido", 25))
    client.delete = lambda: history.save_memory(first.contact_id, "Prefere áudio.")
    history.update_memory_if_due(first.contact_id)
    assert history.memory(first.contact_id) == "Prefere áudio."


@pytest.mark.parametrize("settings", [Settings(), "contact_off"])
def test_no_memory_call_when_history_is_off(db, settings):
    client = FakeClient()
    current = [ON]
    history = HistoryService(db, lambda: client, lambda: current[0], Clock())
    first = observe(history, db, "Ana", lines("pedido", 25))
    if settings == "contact_off":
        with db.transaction() as conn:
            contacts.set_history(conn, first.contact_id, 0)
    else:
        current[0] = settings
    assert not history.update_memory_if_due(first.contact_id) and client.texts == []


def test_user_edit_is_kept_and_marked(db):
    client = FakeClient()
    history = service(db, client=client)
    first = observe(history, db, "Ana", lines("pedido", 25))
    history.save_memory(first.contact_id, "Prefere áudio.")
    assert history.memory(first.contact_id) == "Prefere áudio."
    with db.transaction() as conn:
        assert store.load_memory(conn, first.contact_id).edited


def test_forget_contact_and_all(db):
    history = service(db)
    observe(history, db, "Ana", lines("pedido", 6))
    observe(history, db, "Bia", lines("festa", 6))
    assert history.forget_contact("Ana", None) == 6
    assert observe(history, db, "Bia", lines("festa", 6)).messages == 6
    history.forget_all()
    with db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM history_message").fetchone()[0] == 0


def test_retention_respects_the_setting(db):
    clock = Clock()
    history = service(db, Settings(history_enabled=True, retention_months=1), clock=clock)
    observe(history, db, "Ana", lines("pedido", 4, day="2026-09-01"))
    clock.now = datetime(2026, 11, 17, 10, 0)
    assert history.apply_retention() == 4


def test_database_errors_never_leave_the_service(db):
    history = service(db)
    db.close()
    assert history.observe("Ana", lines("pedido", 3), False, "").state == "unavailable"
    assert not history.update_memory_if_due(1)
    assert history.forget_contact("Ana", None) == 0


def test_without_a_database(db):
    history = HistoryService(None, lambda: None, lambda: ON)
    assert history.observe("Ana", lines("pedido", 3), False, "").state == "unavailable"


@pytest.mark.parametrize("state", ["off", "unavailable"])
def test_log_has_no_content(db, caplog, state):
    settings = ON if state == "unavailable" else Settings()
    history = service(db, settings)
    if state == "unavailable":
        db.close()
    with caplog.at_level("INFO", logger="respondoia"):
        history.observe("ZZ-NAME", [line("ZZ-TEXT")], False, "")
    assert "ZZ-" not in caplog.text
