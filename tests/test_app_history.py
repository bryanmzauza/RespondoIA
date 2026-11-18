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
    "covered",
    "minimized",
    "chat_changed",
    "private",
    "lost",
    "unreadable",
)


class FakeHistoryWorker(Recorder):
    loaded = Signal(object)
    load_mode = Signal(str)
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

    def drop_history(self):
        self.record("drop_history")


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
    rig.history.load_mode.emit("wheel")
    assert rig.tray.notices()[-1] == "notice.loading"
    rig.history.load_mode.emit("background")
    assert rig.tray.notices()[-1] == "notice.loading_background"


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


def test_memory_work_runs_apart_from_loads_and_deletes(tmp_path):
    # A first memory can take minutes of calls; loads and deletes must not wait behind it.
    worker, panel, tray, hotkey = (
        FakeHistoryAwareWorker(),
        FakePanel(),
        FakeTrayWithLoading(),
        FakeHotkey(),
    )
    loads, memory = FakeHistoryWorker(), FakeHistoryWorker()
    controller = Controller(
        worker,
        panel,
        tray,
        hotkey,
        ON,
        config_file=tmp_path / "config.toml",
        history_worker=loads,
        memory_worker=memory,
        guard=FakeGuard(),
    )
    worker.history_grew.emit(7)
    controller._history = READY
    controller.open_memory()
    assert memory.calls == [("update_memory", 7), ("load_memory", 7)] and loads.calls == []
    assert not hasattr(app_module.HistoryWorker, "update_memory")


# The worker itself, with a real database and the fake chat of the loader tests.


class FakeDesktop:
    def __init__(self, minimized=False):
        self.cursor = (5, 5)
        self.calls = []
        self.is_minimized = minimized

    def minimized(self, hwnd):
        return self.is_minimized

    def restore_behind(self, hwnd):
        self.calls.append(("restore_behind", hwnd))
        self.is_minimized = False
        return True

    def minimize(self, hwnd):
        self.calls.append(("minimize", hwnd))
        self.is_minimized = True

    def focus(self, hwnd):
        self.calls.append(("focus", hwnd))
        return True

    def cursor_pos(self):
        return self.cursor

    def put_cursor(self, x, y):
        self.calls.append(("put_cursor", x, y))
        self.cursor = (x, y)


def make_worker(db, tmp_path, chat, settings=ON, scroller=None, minimized=False):
    history = HistoryService(db, lambda: None, lambda: settings)
    style = StyleService(db, lambda: None, tmp_path / "knowledge.md")
    desktop = FakeDesktop(minimized)
    worker = HistoryWorker(
        history,
        style,
        lambda: settings,
        loader_ports=lambda hwnd, found=None: chat.ports(),
        desktop=desktop,
        avatar=lambda hwnd, rect: "",
        accessible=lambda hwnd: scroller,
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


# The panel, the memory window and the settings window, wired to the history worker.


def test_started_shows_the_history_in_the_panel(tmp_path):
    rig = HistoryRig(tmp_path)
    rig.open_panel()
    assert ("set_history", READY) in rig.panel.calls


def test_panel_signals_reach_the_history(tmp_path):
    rig = HistoryRig(tmp_path)
    generation = rig.open_panel(HistoryContext("ask", candidates=((3, "2026-10-01"),)))
    rig.panel.identity_answered.emit(None)
    assert rig.worker.calls[-1] == ("confirm_identity", generation, None)
    rig.panel.load_requested.emit("gap")
    assert rig.history.calls[-1][:2] == ("load", "gap")


def test_identity_answer_updates_the_panel(tmp_path):
    rig = HistoryRig(tmp_path)
    generation = rig.open_panel(HistoryContext("ask", candidates=((3, "2026-10-01"),)))
    rig.worker.history_changed.emit(generation, READY)
    assert rig.panel.calls[-1] == ("set_history", READY)


def test_contact_off_and_delete_reach_the_worker(tmp_path):
    rig = HistoryRig(tmp_path)
    rig.open_panel()
    rig.panel.contact_history_toggled.emit(False)
    assert ("set_contact_history", "Marina Duarte", 7, False) in rig.history.calls
    assert rig.panel.calls[-1][0] == "set_history"
    assert rig.panel.calls[-1][1].state == "out_of_scope"
    rig.panel.contact_history_toggled.emit(True)
    assert ("set_contact_history", "Marina Duarte", 7, True) in rig.history.calls
    assert rig.worker.calls.count(("drop_history",)) == 1
    rig.panel.contact_history_deleted.emit()
    assert ("forget_contact", "Marina Duarte", 7) in rig.history.calls
    assert rig.worker.calls.count(("drop_history",)) == 2
    assert rig.panel.calls[-1][1].messages == 0


def test_memory_window_gets_and_saves_the_text(qtbot, tmp_path):
    rig = HistoryRig(tmp_path)
    rig.open_panel()
    rig.panel.memory_requested.emit()
    assert ("load_memory", 7) in rig.history.calls
    rig.history.memory_ready.emit(7, "Cliente fiel.")
    window = rig.controller._memory_window
    assert window.editor.toPlainText() == "Cliente fiel."
    window.editor.setPlainText("Prefere áudio.")
    window.save_button.click()
    assert ("save_memory", 7, "Prefere áudio.") in rig.history.calls
    window.close()


def test_settings_delete_all_reaches_the_worker(qtbot, tmp_path):
    rig = HistoryRig(tmp_path)
    rig.controller.open_settings()
    rig.controller._settings_window.delete_all_requested.emit()
    assert ("forget_all",) in rig.history.calls
    rig.controller._settings_window.close()


def test_style_window_load_requests_a_style_load(qtbot, tmp_path, monkeypatch):
    rig = HistoryRig(tmp_path)
    rig.controller._style_worker = Recorder()  # any object: open_style only needs one
    window_info = type("W", (), {"hwnd": 77})()
    monkeypatch.setattr(app_module.capture, "find_whatsapp_window", lambda allowed: window_info)
    monkeypatch.setattr(
        rig.controller, "request_style_load", type("S", (), {"emit": lambda s: None})()
    )
    rig.controller.open_style()
    rig.controller._style_window.load_requested.emit()
    assert rig.history.calls[-1] == ("load", "style", 77, "", None)
    rig.controller._style_window.close()


def test_background_load_leaves_the_mouse_and_the_focus_alone(qtbot, db, tmp_path):
    chat = FakeChat(n=60, title="Marina Duarte")
    worker, desktop = make_worker(db, tmp_path, chat, scroller=object())
    modes = []
    worker.load_mode.connect(modes.append)
    with qtbot.waitSignal(worker.loaded) as signal:
        worker.load("full", 42, "Marina Duarte", None)
    assert modes == ["background"] and desktop.calls == []
    assert signal.args[0].history.messages == 60


def test_wheel_load_says_so(qtbot, db, tmp_path):
    chat = FakeChat(n=30, title="Marina Duarte")
    worker, desktop = make_worker(db, tmp_path, chat)
    modes = []
    worker.load_mode.connect(modes.append)
    with qtbot.waitSignal(worker.loaded):
        worker.load("quick", 42, "Marina Duarte", None)
    assert modes == ["wheel"] and desktop.calls[0] == ("focus", 42)


def test_minimized_whatsapp_is_restored_behind_and_minimized_again(qtbot, db, tmp_path):
    chat = FakeChat(n=60, title="Marina Duarte")
    worker, desktop = make_worker(db, tmp_path, chat, scroller=object(), minimized=True)
    with qtbot.waitSignal(worker.loaded) as signal:
        worker.load("full", 42, "Marina Duarte", None)
    assert desktop.calls == [("restore_behind", 42), ("minimize", 42)]
    assert signal.args[0].history.messages == 60


def test_open_whatsapp_is_left_as_it_was(qtbot, db, tmp_path):
    chat = FakeChat(n=60, title="Marina Duarte")
    worker, desktop = make_worker(db, tmp_path, chat, scroller=object())
    with qtbot.waitSignal(worker.loaded):
        worker.load("full", 42, "Marina Duarte", None)
    assert desktop.calls == []


# Filling a gap on its own at the hotkey.

GAP = HistoryContext("ready", 7, "Marina Duarte", messages=40, gap_since="2026-11-03", added=5)


def test_a_new_gap_is_filled_in_the_background_at_the_hotkey(tmp_path):
    rig = HistoryRig(tmp_path)
    rig.open_panel(GAP)
    assert rig.history.calls == [("load", "auto_gap", 55, "Marina Duarte", 7)]
    assert ("dismiss",) not in rig.panel.calls  # the panel stays open
    rig.history.load_mode.emit("background")
    rig.history.loaded.emit(LoadOutcome("auto_gap", "joined", 30, GAP, 0))
    assert "notice.loading_background" not in rig.tray.notices()
    assert not any(n.startswith("notice.load_") for n in rig.tray.notices())


def test_the_gap_is_tried_once_per_contact(tmp_path):
    rig = HistoryRig(tmp_path)
    rig.open_panel(GAP)
    rig.history.loaded.emit(LoadOutcome("auto_gap", "lost", 3, GAP, 0))
    rig.panel.closed.emit()
    rig.open_panel(GAP)
    assert [c for c in rig.history.calls if c[0] == "load"] == [
        ("load", "auto_gap", 55, "Marina Duarte", 7)
    ]


def test_no_gap_or_nothing_new_loads_nothing(tmp_path):
    rig = HistoryRig(tmp_path)
    rig.open_panel(READY)
    rig.panel.closed.emit()
    rig.open_panel(HistoryContext("ready", 7, "Marina Duarte", gap_since="2026-11-03", added=0))
    assert rig.history.calls == []


def test_the_automatic_load_never_takes_the_mouse(qtbot, db, tmp_path):
    chat = FakeChat(n=60, title="Marina Duarte")
    worker, desktop = make_worker(db, tmp_path, chat)  # no accessibility scrolling
    with qtbot.waitSignal(worker.loaded) as signal:
        worker.load("auto_gap", 42, "Marina Duarte", 7)
    assert signal.args[0].reason == "skipped" and desktop.calls == []
    assert not any(event[0] == "scroll" for event in chat.events)
