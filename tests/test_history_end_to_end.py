"""Real session, real history and style services, real database; fake desktop and model."""

from dataclasses import replace

from test_history_loader import FakeChat
from test_session import FakePorts

from respondoia.config import Settings
from respondoia.domain import ChatLine, Look, Rect
from respondoia.memory import store
from respondoia.memory.loader import LoadLimits, load
from respondoia.memory.service import HistoryService
from respondoia.session import Session, StartStatus
from respondoia.style.service import StyleService

ON = Settings(history_enabled=True)


def services(db, tmp_path):
    history = HistoryService(db, lambda: None, lambda: ON)
    style = StyleService(db, lambda: None, tmp_path / "knowledge.md")
    return history, style


def press(fake, history, style, avatar=""):
    if avatar:  # a real photo in the header
        fake.chat = replace(fake.chat, avatar=Rect(430, 10, 470, 50))

    def look(hwnd, photo, crops, looks, area):
        return Look(avatar, (None,) * len(crops), ("",) * len(looks))

    ports = replace(fake.ports(), history=history, style=style, look=look)
    session = Session(ports, ON)
    result = session.start()
    assert result.status is StartStatus.STARTED
    return session, result


def test_load_then_hotkey_adds_nothing(db, tmp_path):
    history, style = services(db, tmp_path)
    chat = FakeChat(n=80, title="Marina Duarte")
    report = load(42, "Marina Duarte", LoadLimits(screens=80, months=0), chat.ports())
    style.learn_loaded("Marina Duarte", report.lines)
    loaded = history.store_loaded("Marina Duarte", report.lines, report.reached_start, "")
    assert loaded.state == "ready" and loaded.messages == 80
    fake = FakePorts()
    fake.lines = list(chat.read().lines)  # the bottom of the chat, as the hotkey sees it
    _, result = press(fake, history, style)
    assert result.history.state == "ready"
    assert (result.history.added, result.history.messages) == (0, 80)


def lines(prefix, n, start=0):
    return [
        ChatLine(
            "contact" if i % 2 else "user",
            "",
            f"10:{start + i:02d}",
            "text",
            f"{prefix} {start + i}",
            "",
        )
        for i in range(n)
    ]


def test_two_contacts_with_one_name_never_share_memory(db, tmp_path):
    history, style = services(db, tmp_path)
    fake = FakePorts(title="Ana")
    fake.lines = lines("bolo de limão", 8)
    session, first = press(fake, history, style)
    with db.transaction() as conn:
        store.save_memory(conn, first.history.contact_id, "ZZ-MEMORY-A", 99, "x")

    # Another Ana, after a gap, without a photo: nothing ties her to the first one.
    other = FakePorts(title="Ana")
    other.lines = lines("reunião de pais", 6, start=30)
    session, second = press(other, history, style)
    assert second.history.state == "ask"
    session.suggest()
    request = other.calls[-1][1]
    assert "ZZ-MEMORY-A" not in str(request.context) and request.context.snippets == ()

    # The user says it is another person: she gets her own record and never A's memory.
    answer = session.confirm_identity(None)
    assert answer.state == "ready" and answer.contact_id != first.history.contact_id
    session.regenerate((), "")
    assert "ZZ-MEMORY-A" not in str(other.calls[-1][1].context)

    later = FakePorts(title="Ana")
    later.lines = lines("reunião de pais", 8, start=32)
    session, third = press(later, history, style)
    assert third.history.state == "ready" and third.history.contact_id == answer.contact_id
    session.suggest()
    assert "ZZ-MEMORY-A" not in str(later.calls[-1][1].context)
    assert third.history.owner_since != ""  # the panel can say which Ana this is


def test_the_first_ana_still_gets_her_memory(db, tmp_path):
    history, style = services(db, tmp_path)
    fake = FakePorts(title="Ana")
    fake.lines = lines("bolo de limão", 8)
    _, first = press(fake, history, style)
    with db.transaction() as conn:
        store.save_memory(conn, first.history.contact_id, "Gosta de limão.", 99, "x")
    again = FakePorts(title="Ana")
    again.lines = lines("bolo de limão", 10, start=4)
    session, result = press(again, history, style)
    assert result.history.state == "ready" and result.history.memory == "Gosta de limão."
    session.suggest()
    assert again.calls[-1][1].context.memory == "Gosta de limão."
