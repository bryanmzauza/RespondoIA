"""The controller and the history worker: loading, the identity answer, startup cleanup."""

import pytest
from PySide6.QtCore import Signal
from test_app import CHAT, FakeHotkey, FakePanel, FakeTray, FakeWorker, Recorder
from test_history_loader import FakeChat
from test_session import make_reply

from respondoia import app as app_module
from respondoia import i18n
from respondoia.app import Controller, HistoryWorker, LoadOutcome
from respondoia.config import Settings
from respondoia.domain import Rect
from respondoia.memory.service import HistoryContext, HistoryService
from respondoia.session import StartResult, StartStatus
from respondoia.style.service import StyleService

ON = Settings(history_enabled=True)
READY = HistoryContext("ready", 7, "Marina Duarte", messages=12)
LOAD_REASONS = (
    "limit",
    "months",
    "start",
    "joined",
    "user",
    "focus",
    "chat_changed",
    "private",
    "lost",
    "unreadable",
)


class FakeHistoryWorker(Recorder):
    loaded = Signal(object)
    memory_updated = Signal(int)
    memory_ready = Signal(int, str)

    def load(self, purpose, hwnd, title, contact_id):
        self.record("load", purpose, hwnd, title, contact_id)

    def update_memory(self, contact_id):
        self.record("update_memory", contact_id)

    def startup(self, private_contacts):
        self.record("startup", tuple(private_contacts))

    def forget_contact(self, title, contact_id):
        self.record("forget_contact", title, contact_id)

    def forget_all(self):
        self.record("forget_all")

    def set_contact_history(self, title, contact_id, on):
        self.record("set_contact_history", title, contact_id, on)

    def load_memory(self, contact_id):
        self.record("load_memory", contact_id)

    def save_memory(self, contact_id, text):
        self.record("save_memory", contact_id, text)


class FakeHistoryAwareWorker(FakeWorker):
    history_changed = Signal(int, object)
    history_grew = Signal(int)

    def confirm_identity(self, generation, contact_id):
        self.record("confirm_identity", generation, contact_id)


class FakeGuard:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    def arm(self, delay_s=0.3):
        self.calls.append(("arm", delay_s))
        return self.ok

    def disarm(self):
        self.calls.append(("disarm",))

    def stop(self):
        self.calls.append(("stop",))


class FakeTrayWithLoading(FakeTray):
    def set_loading(self, loading):
        self.record("set_loading", loading)


class HistoryRig:
    def __init__(self, tmp_path, settings=ON, guard_ok=True):
        self.worker = FakeHistoryAwareWorker()
        self.panel, self.tray, self.hotkey = FakePanel(), FakeTrayWithLoading(), FakeHotkey()
        self.history = FakeHistoryWorker()
        self.guard = FakeGuard(guard_ok)
        self.controller = Controller(
            self.worker,
            self.panel,
            self.tray,
            self.hotkey,
            settings,
            config_file=tmp_path / "config.toml",
            history_worker=self.history,
            guard=self.guard,
        )
        self.controller.resume()

    def open_panel(self, history=READY):
        self.hotkey.triggered.emit()
        generation = self.worker.calls[-1][1]
        self.worker.started.emit(
            generation, StartResult(StartStatus.STARTED, CHAT, None, history, hwnd=55)
        )
        self.worker.replied.emit(generation, make_reply())
        return generation


@pytest.fixture(autouse=True)
def with_api_key(qapp, monkeypatch):
    i18n.install(qapp, "en")
    monkeypatch.setattr(app_module.config, "get_api_key", lambda: "sk-test-not-a-real-key")


def test_load_closes_the_panel_and_arms_the_guard(tmp_path):
    rig = HistoryRig(tmp_path)
    rig.open_panel()
    rig.controller.request_load("quick")
    assert ("dismiss",) in rig.panel.calls and rig.guard.calls == [("arm", 0.6)]
    assert rig.history.calls == [("load", "quick", 55, "Marina Duarte", 7)]
    assert ("set_loading", True) in rig.tray.calls
    assert "notice.loading" in rig.tray.notices()


def test_load_without_raw_input_does_not_start(tmp_path):
    rig = HistoryRig(tmp_path, guard_ok=False)
    rig.open_panel()
    rig.controller.request_load("quick")
    assert rig.history.calls == [] and "error.load_unavailable" in rig.tray.notices()


@pytest.mark.parametrize("reason", LOAD_REASONS)
def test_load_result_notice_by_reason(tmp_path, reason):
    rig = HistoryRig(tmp_path)
    rig.open_panel()
    rig.controller.request_load("full")
    rig.history.loaded.emit(LoadOutcome("full", reason, 40, READY, 0))
    assert rig.tray.notices()[-1] == f"notice.load_{reason}"
    assert rig.guard.calls[-1] == ("disarm",) and ("set_loading", False) in rig.tray.calls


def test_every_load_reason_is_translated(qapp):
    for key in [f"notice.load_{reason}" for reason in LOAD_REASONS] + [
        "notice.loading",
        "notice.load_ask",
        "error.load_unavailable",
        "error.no_whatsapp",
    ]:
        assert i18n.t(key) != key, key


def test_load_that_could_not_tell_the_contact_says_so(tmp_path):
    rig = HistoryRig(tmp_path)
    rig.open_panel()
    rig.controller.request_load("quick")
    rig.history.loaded.emit(LoadOutcome("quick", "limit", 40, HistoryContext("ask"), 0))
    assert rig.tray.notices()[-1] == "notice.load_ask"


def test_a_load_that_added_messages_asks_for_a_memory_update(tmp_path):
    rig = HistoryRig(tmp_path)
    rig.open_panel()
    rig.controller.request_load("quick")
    rig.history.loaded.emit(
        LoadOutcome("quick", "limit", 40, HistoryContext("ready", 7, added=30), 0)
    )
    assert ("update_memory", 7) in rig.history.calls


def test_pause_stops_a_running_load(tmp_path):
    rig = HistoryRig(tmp_path)
    rig.open_panel()
    rig.controller.request_load("quick")
    rig.tray.pause_toggled.emit(True)
    assert ("stop",) in rig.guard.calls


def test_history_grew_asks_for_a_memory_update(tmp_path):
    rig = HistoryRig(tmp_path)
    rig.worker.history_grew.emit(7)
    assert rig.history.calls == [("update_memory", 7)]


def test_identity_answer_reaches_the_worker(tmp_path):
    rig = HistoryRig(tmp_path)
    generation = rig.open_panel(HistoryContext("ask", candidates=((3, "2026-10-01"),)))
    rig.controller.answer_identity(3)
    assert rig.worker.calls[-1] == ("confirm_identity", generation, 3)


def test_style_load_without_whatsapp_shows_an_error(tmp_path, monkeypatch):
    rig = HistoryRig(tmp_path)
    monkeypatch.setattr(app_module.capture, "find_whatsapp_window", lambda allowed: None)
    rig.controller.request_load("style")
    assert rig.history.calls == [] and "error.no_whatsapp" in rig.tray.notices()


def test_style_load_needs_no_open_panel(tmp_path, monkeypatch):
    rig = HistoryRig(tmp_path)
    window = type("W", (), {"hwnd": 77})()
    monkeypatch.setattr(app_module.capture, "find_whatsapp_window", lambda allowed: window)
    rig.controller.request_load("style")
    assert rig.history.calls == [("load", "style", 77, "", None)]


def test_startup_cleans_private_contacts_and_old_messages(tmp_path):
    rig = HistoryRig(tmp_path, Settings(history_enabled=True, private_contacts=("Bia",)))
    rig.controller.start_history()
    assert rig.history.calls == [("startup", ("Bia",))]


# The worker itself, with a real database and the fake chat of the loader tests.


class FakeDesktop:
    def __init__(self):
        self.cursor = (5, 5)
        self.calls = []

    def focus(self, hwnd):
        self.calls.append(("focus", hwnd))
        return True

    def cursor_pos(self):
        return self.cursor

    def put_cursor(self, x, y):
        self.calls.append(("put_cursor", x, y))
        self.cursor = (x, y)


def make_worker(db, tmp_path, chat, settings=ON):
    history = HistoryService(db, lambda: None, lambda: settings)
    style = StyleService(db, lambda: None, tmp_path / "knowledge.md")
    desktop = FakeDesktop()
    worker = HistoryWorker(
        history,
        style,
        lambda: settings,
        loader_ports=lambda hwnd: chat.ports(),
        desktop=desktop,
        avatar=lambda hwnd, rect: "",
    )
    return worker, desktop


def test_worker_loads_stores_and_puts_the_cursor_back(qtbot, db, tmp_path):
    chat = FakeChat(n=60, title="Marina Duarte")
    worker, desktop = make_worker(db, tmp_path, chat)
    with qtbot.waitSignal(worker.loaded) as signal:
        worker.load("full", 42, "Marina Duarte", None)
    outcome = signal.args[0]
    assert outcome.reason == "start" and outcome.read == 60
    assert outcome.history.state == "ready" and outcome.history.messages == 60
    assert desktop.calls[0] == ("focus", 42) and desktop.cursor == (5, 5)


def test_worker_style_load_keeps_only_the_users_lines(qtbot, db, tmp_path):
    chat = FakeChat(n=60, title="Marina Duarte")
    worker, _ = make_worker(db, tmp_path, chat, Settings())
    with qtbot.waitSignal(worker.loaded) as signal:
        worker.load("style", 42, "", None)
    outcome = signal.args[0]
    assert outcome.history is None
    with db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM history_message").fetchone()[0] == 0
        texts = [r[0] for r in conn.execute("SELECT text FROM style_sample")]
    # Only the user's lines (every third message) can be learned; none of the contact's.
    assert all(int(text.split()[-1]) % 3 == 0 for text in texts)


def test_worker_refuses_a_private_contact(qtbot, db, tmp_path):
    chat = FakeChat(n=60, title="Marina Duarte")
    settings = Settings(history_enabled=True, private_contacts=("Marina Duarte",))
    worker, desktop = make_worker(db, tmp_path, chat, settings)
    with qtbot.waitSignal(worker.loaded) as signal:
        worker.load("style", 42, "", None)
    assert signal.args[0].reason == "private" and desktop.calls == []
    assert not any(event[0] == "scroll" for event in chat.events)


def test_worker_startup_forgets_private_contacts(qtbot, db, tmp_path):
    chat = FakeChat(n=60, title="Marina Duarte")
    worker, _ = make_worker(db, tmp_path, chat)
    with qtbot.waitSignal(worker.loaded):
        worker.load("full", 42, "Marina Duarte", None)
    worker.startup(("Marina Duarte",))
    with db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM history_message").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM contact").fetchone()[0] == 0


def test_bounds_center_is_where_the_cursor_goes(qtbot, db, tmp_path):
    chat = FakeChat(n=30, title="Marina Duarte")
    worker, desktop = make_worker(db, tmp_path, chat)
    with qtbot.waitSignal(worker.loaded):
        worker.load("quick", 42, "Marina Duarte", None)
    bounds = Rect(0, 0, 800, 900)
    assert ("put_cursor", bounds.width // 2, bounds.height // 2) in desktop.calls
