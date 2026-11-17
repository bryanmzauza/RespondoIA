import logging
import time
import tomllib

import pytest
from PySide6.QtCore import QObject, Signal
from test_session import BOUNDS, FakePorts, make_reply

from respondoia import app as app_module
from respondoia import i18n
from respondoia.app import Controller, Worker
from respondoia.capture import CaptureError
from respondoia.config import Settings
from respondoia.domain import OpenChat, Rect
from respondoia.hotkey import HotkeyRegistrationError
from respondoia.llm import LlmError
from respondoia.session import ChooseResult, StartResult, StartStatus

CHAT = OpenChat("Marina Duarte", Rect(420, 0, 1600, 900))
STARTED = StartResult(StartStatus.STARTED, CHAT)


class Recorder(QObject):
    def __init__(self):
        super().__init__()
        self.calls = []

    def names(self):
        return [call[0] for call in self.calls]

    def record(self, *call):
        self.calls.append(call)


class FakeWorker(Recorder):
    started = Signal(int, object)
    replied = Signal(int, object)
    failed = Signal(int, str)
    chosen = Signal(int, object)
    choose_failed = Signal(int, str)
    previewed = Signal(int, object)
    crashed = Signal(int)
    fell_back = Signal(int)

    def start(self, generation, requested_at):
        self.requested_at = requested_at
        self.record("start", generation)

    def suggest(self, generation):
        self.record("suggest", generation)

    def regenerate(self, generation, selected, instruction):
        self.record("regenerate", generation, selected, instruction)

    def choose(self, generation, index):
        self.record("choose", generation, index)

    def close(self):
        self.record("close")

    def set_settings(self, settings):
        self.record("set_settings", settings)


class FakePanel(Recorder):
    suggestion_chosen = Signal(int)
    regenerate_requested = Signal(tuple, str)
    mark_private_requested = Signal()
    retry_requested = Signal()
    closed = Signal()
    settings_requested = Signal()

    def show_loading(self, title, bounds):
        self.record("show_loading", title, bounds)

    def show_reply(self, reply):
        self.record("show_reply", reply)

    def show_preview(self, preview):
        self.record("show_preview", preview)

    def show_error(self, code):
        self.record("show_error", code)

    def dismiss(self):
        self.record("dismiss")

    def bring_to_front(self):
        self.record("bring_to_front")

    def set_corner(self, corner):
        self.record("set_corner", corner)

    def set_quick_instructions(self, items):
        self.record("set_quick_instructions", list(items))


class FakeTray(Recorder):
    pause_toggled = Signal(bool)
    quit_requested = Signal()
    settings_requested = Signal()

    def retranslate(self):
        self.record("retranslate")

    def notify(self, key, /, **values):
        self.record("notify", key, values)

    def notices(self):
        return [call[1] for call in self.calls if call[0] == "notify"]


class FakeHotkey(Recorder):
    triggered = Signal()

    def __init__(self):
        super().__init__()
        self.registered = False
        self.taken = False

    def register(self, hotkey):
        if self.taken:
            raise HotkeyRegistrationError("taken")
        self.registered = True
        self.record("register", hotkey)

    def unregister(self):
        self.registered = False
        self.record("unregister")

    def replace(self, hotkey):
        if self.taken:
            raise HotkeyRegistrationError("taken")
        self.registered = True
        self.record("replace", hotkey)


class Rig:
    def __init__(self, tmp_path, settings=None, config_error=None):
        self.worker, self.panel, self.tray, self.hotkey = (
            FakeWorker(),
            FakePanel(),
            FakeTray(),
            FakeHotkey(),
        )
        self.config_file = tmp_path / "config.toml"
        self.controller = Controller(
            self.worker,
            self.panel,
            self.tray,
            self.hotkey,
            settings or Settings(),
            config_file=self.config_file,
            config_error=config_error,
        )
        self.controller.resume()
        # What the controller set up on the panel at start, kept apart from the session calls.
        self.panel_setup = list(self.panel.calls)
        self.panel.calls.clear()

    def press(self):
        self.hotkey.triggered.emit()

    def open_panel(self):
        """Hotkey, chat accepted, reply shown. Returns the generation in use."""
        self.press()
        generation = self.worker.calls[-1][1]
        self.worker.started.emit(generation, STARTED)
        self.worker.replied.emit(generation, make_reply())
        return generation


@pytest.fixture(autouse=True)
def with_api_key(qapp, monkeypatch):
    i18n.install(qapp, "en")
    monkeypatch.setattr(app_module.config, "get_api_key", lambda: "sk-test-not-a-real-key")


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def test_hotkey_starts_a_session_and_fills_the_panel(rig, caplog):
    with caplog.at_level(logging.INFO, logger="respondoia"):
        rig.press()
        assert rig.worker.calls == [("start", 1)]
        assert rig.panel.calls == []  # nothing on screen before the chat is accepted
        rig.worker.started.emit(1, STARTED)
        assert rig.panel.calls == [("show_loading", "Marina Duarte", CHAT.bounds)]
        assert rig.worker.calls[-1] == ("suggest", 1)
        reply = make_reply()
        rig.worker.replied.emit(1, reply)
    assert rig.panel.calls[-1] == ("show_reply", reply)
    assert "suggestion ok total_ms=" in caplog.text
    assert "Marina" not in caplog.text


def test_second_hotkey_while_active_does_not_start_a_session(rig):
    rig.press()
    rig.press()  # the first call is still on its way
    rig.worker.started.emit(1, STARTED)
    rig.press()  # loading
    rig.worker.replied.emit(1, make_reply())
    rig.press()  # panel open
    assert rig.worker.names().count("start") == 1
    assert rig.worker.names().count("suggest") == 1
    assert rig.panel.names().count("bring_to_front") == 3
    rig.panel.suggestion_chosen.emit(0)
    rig.press()  # the paste is in progress
    assert rig.worker.names().count("choose") == 1 and rig.worker.names().count("start") == 1


def test_stale_generation_is_ignored(rig):
    rig.press()
    rig.worker.started.emit(1, STARTED)
    rig.panel.closed.emit()  # Esc while the model is still answering
    rig.worker.replied.emit(1, make_reply())
    rig.worker.failed.emit(1, "network")
    assert "show_reply" not in rig.panel.names() and "show_error" not in rig.panel.names()
    assert "close" in rig.worker.names()
    rig.press()
    assert rig.worker.calls[-1] == ("start", 3) or rig.worker.calls[-1][0] == "start"
    rig.worker.started.emit(1, STARTED)  # an answer from the old session
    assert rig.panel.names().count("show_loading") == 1


def test_failure_shows_error_and_keeps_running(rig):
    rig.press()
    rig.worker.started.emit(1, STARTED)
    rig.worker.failed.emit(1, "network")
    assert rig.panel.calls[-1] == ("show_error", "network")
    rig.panel.retry_requested.emit()
    assert rig.panel.calls[-1] == ("show_loading", "Marina Duarte", CHAT.bounds)
    assert rig.worker.calls[-1] == ("suggest", 1)
    reply = make_reply()
    rig.worker.replied.emit(1, reply)
    assert rig.panel.calls[-1] == ("show_reply", reply)
    rig.panel.closed.emit()
    rig.press()
    assert rig.worker.names().count("start") == 2


def test_missing_api_key_notifies_and_does_not_start(rig, monkeypatch):
    monkeypatch.setattr(app_module.config, "get_api_key", lambda: None)
    rig.press()
    assert rig.tray.notices() == ["error.no_api_key"]
    assert rig.worker.calls == [] and rig.panel.calls == []
    monkeypatch.setattr(app_module.config, "get_api_key", lambda: "sk-test-not-a-real-key")
    rig.press()
    assert rig.worker.calls == [("start", 1)]


def test_pause_unregisters_the_hotkey_and_closes_the_session(rig):
    generation = rig.open_panel()
    rig.tray.pause_toggled.emit(True)
    assert not rig.hotkey.registered
    assert rig.panel.calls[-1] == ("dismiss",) and rig.worker.calls[-1] == ("close",)
    rig.worker.replied.emit(generation, make_reply())  # late answer after the pause
    assert rig.panel.calls[-1] == ("dismiss",)
    rig.tray.pause_toggled.emit(False)
    assert rig.hotkey.registered
    rig.press()
    assert rig.worker.calls[-1][0] == "start"


def test_resume_reports_a_taken_hotkey(tmp_path):
    rig = Rig(tmp_path)
    rig.tray.pause_toggled.emit(True)
    rig.hotkey.taken = True
    rig.tray.pause_toggled.emit(False)
    assert rig.tray.calls[-1] == ("notify", "notice.hotkey_in_use", {"hotkey": "Ctrl+Shift+Space"})
    assert not rig.hotkey.registered


@pytest.mark.parametrize(
    "status", [StartStatus.NOT_WHATSAPP, StartStatus.PRIVATE, StartStatus.EXPIRED]
)
def test_private_and_other_windows_show_nothing(rig, status):
    rig.press()
    rig.worker.started.emit(1, StartResult(status))
    assert rig.panel.calls == [] and rig.tray.notices() == []
    assert rig.worker.names() == ["start"]
    rig.press()
    assert rig.worker.names() == ["start", "start"]


def test_unknown_chat_notifies_and_reads_nothing(rig):
    rig.press()
    rig.worker.started.emit(1, StartResult(StartStatus.UNKNOWN_CHAT))
    assert rig.tray.notices() == ["notice.unknown_chat"]
    assert rig.panel.calls == [] and rig.worker.names() == ["start"]


@pytest.mark.parametrize(
    "result,notice",
    [
        (ChooseResult.PASTED, None),
        (ChooseResult.COPIED, "notice.copied"),
        (ChooseResult.COPIED_CHAT_CHANGED, "notice.chat_changed"),
        (ChooseResult.COPIED_FOCUS_FAILED, "notice.focus_failed"),
    ],
)
def test_choice_hides_the_panel_and_reports_the_result(rig, result, notice):
    generation = rig.open_panel()
    rig.panel.suggestion_chosen.emit(1)
    assert rig.panel.calls[-1] == ("dismiss",)
    assert rig.worker.calls[-1] == ("choose", generation, 1)
    rig.worker.chosen.emit(generation, result)
    assert rig.tray.notices() == ([notice] if notice else [])
    rig.press()
    assert rig.worker.calls[-1][0] == "start"


def test_regenerate_shows_loading_and_asks_the_worker(rig):
    generation = rig.open_panel()
    rig.panel.regenerate_requested.emit((2,), "pedir prazo")
    assert rig.panel.calls[-1] == ("show_loading", "Marina Duarte", CHAT.bounds)
    assert rig.worker.calls[-1] == ("regenerate", generation, (2,), "pedir prazo")
    assert rig.worker.names().count("start") == 1


def test_mark_private_saves_the_contact_and_closes(rig):
    rig.open_panel()
    rig.panel.mark_private_requested.emit()
    saved = tomllib.loads(rig.config_file.read_text(encoding="utf-8"))
    assert saved["privacy"]["private_contacts"] == ["Marina Duarte"]
    assert rig.panel.calls[-1] == ("dismiss",)
    assert rig.worker.calls[-2][0] == "set_settings"
    assert rig.worker.calls[-2][1].private_contacts == ("Marina Duarte",)
    assert rig.worker.calls[-1] == ("close",)
    assert rig.tray.notices() == ["notice.private_added"]


def test_a_worker_crash_notifies_and_frees_the_hotkey(rig):
    generation = rig.open_panel()
    rig.worker.crashed.emit(generation)
    assert rig.panel.calls[-1] == ("dismiss",)
    assert rig.tray.notices() == ["error.unexpected"]
    rig.press()
    assert rig.worker.calls[-1][0] == "start"


# The worker, on the calling thread, with fake ports.


class Catcher:
    def __init__(self, worker):
        self.events = []
        for name in (
            "started",
            "previewed",
            "replied",
            "failed",
            "chosen",
            "choose_failed",
            "crashed",
            "fell_back",
        ):
            getattr(worker, name).connect(lambda *args, n=name: self.events.append((n, *args)))


def test_worker_runs_a_session_from_start_to_paste(qapp):
    fake = FakePorts()
    worker = Worker(fake.ports(), Settings(input="screenshot"))
    seen = Catcher(worker)
    worker.start(1, time.monotonic())
    worker.suggest(1)
    worker.regenerate(1, (2,), "pedir prazo")
    worker.choose(1, 0)
    assert [event[0] for event in seen.events] == [
        "started",
        "previewed",
        "replied",
        "previewed",
        "replied",
        "chosen",
    ]
    assert seen.events[0][2].chat == OpenChat("Marina Duarte", BOUNDS)
    assert seen.events[-1] == ("chosen", 1, ChooseResult.PASTED)
    assert fake.count("capture_chat") == 1 and fake.count("insert_text") == 1


def test_worker_maps_failures_to_codes(qapp):
    fake = FakePorts()
    worker = Worker(fake.ports(), Settings())
    seen = Catcher(worker)
    worker.start(1, time.monotonic())
    fake.suggest_error = LlmError("rate_limit")
    worker.suggest(1)
    fake.suggest_error = None
    fake.capture_error = CaptureError("blank")
    worker.start(2, time.monotonic())
    worker.suggest(2)
    assert [e for e in seen.events if e[0] == "failed"][-2:] == [
        ("failed", 1, "rate_limit"),
        ("failed", 2, "capture"),
    ]


def test_worker_reports_an_unexpected_error_without_its_text(qapp, caplog):
    fake = FakePorts()
    worker = Worker(fake.ports(), Settings())
    seen = Catcher(worker)
    worker.start(1, time.monotonic())
    fake.suggest_error = ValueError("ZZ-SECRET-TEXT")
    with caplog.at_level(logging.INFO, logger="respondoia"):
        worker.suggest(1)
    assert seen.events[-1] == ("crashed", 1)
    assert "ValueError" in caplog.text and "ZZ-SECRET" not in caplog.text
    worker.choose(1, 0)  # the session was dropped: nothing happens, nothing is pasted
    assert fake.count("insert_text") == 0 and fake.count("copy_text") == 0


def test_worker_uses_new_settings_for_the_next_session(qapp):
    fake = FakePorts()
    worker = Worker(fake.ports(), Settings())
    seen = Catcher(worker)
    worker.set_settings(Settings(private_contacts=("Marina Duarte",)))
    worker.start(1, time.monotonic())
    assert seen.events[-1][2].status is StartStatus.PRIVATE
    worker.suggest(1)
    assert fake.count("capture_chat") == 0 and fake.count("suggest") == 0


def test_closing_the_worker_session_blocks_late_requests(qapp):
    fake = FakePorts()
    worker = Worker(fake.ports(), Settings())
    worker.start(1, time.monotonic())
    worker.suggest(1)
    worker.close()
    worker.choose(1, 0)
    assert fake.count("insert_text") == 0 and fake.count("copy_text") == 0


def test_session_work_happens_on_the_worker_thread(qtbot, tmp_path):
    import threading

    from PySide6.QtCore import QThread

    fake = FakePorts()
    threads = set()
    original = fake.suggest

    def suggest(request, on_preview=None):
        threads.add(threading.get_ident())
        return original(request, on_preview)

    fake.suggest = suggest
    thread = QThread()
    worker = Worker(fake.ports(), Settings(input="screenshot"))
    worker.moveToThread(thread)
    thread.start()
    panel, tray, hotkey = FakePanel(), FakeTray(), FakeHotkey()
    controller = Controller(
        worker, panel, tray, hotkey, Settings(), config_file=tmp_path / "c.toml"
    )
    try:
        controller.resume()
        hotkey.triggered.emit()
        qtbot.waitUntil(lambda: "show_reply" in panel.names(), timeout=3000)
        panel.regenerate_requested.emit((2,), "pedir prazo")
        qtbot.waitUntil(lambda: panel.names().count("show_reply") == 2, timeout=3000)
        panel.suggestion_chosen.emit(0)
        qtbot.waitUntil(lambda: fake.count("insert_text") == 1, timeout=3000)
        qtbot.waitUntil(lambda: not controller._active, timeout=3000)
    finally:
        thread.quit()
        thread.wait(3000)
    assert threads and threading.get_ident() not in threads
    assert fake.calls[-1] == ("insert_text", 100, "sugestão 1")
    assert tray.notices() == []


def test_the_hotkey_request_carries_the_time_it_was_pressed(rig):
    rig.press()
    assert abs(rig.worker.requested_at - time.monotonic()) < 1


def test_invalid_config_keeps_the_hotkey_off(tmp_path):
    rig = Rig(tmp_path, config_error="suggestions.count")
    assert not rig.hotkey.registered
    assert rig.tray.calls[-1] == ("notify", "error.config", {"key": "suggestions.count"})
    rig.tray.pause_toggled.emit(True)
    rig.tray.pause_toggled.emit(False)
    assert not rig.hotkey.registered
    rig.press()  # nothing is registered, but a stray signal must not start a session either
    assert rig.worker.calls == []


def test_retry_repeats_the_request_that_failed(rig):
    generation = rig.open_panel()
    rig.panel.regenerate_requested.emit((2,), "pedir prazo")
    rig.worker.failed.emit(generation, "network")
    rig.panel.retry_requested.emit()
    assert rig.panel.calls[-1] == ("show_loading", "Marina Duarte", CHAT.bounds)
    assert rig.worker.calls[-1] == ("regenerate", generation, (2,), "pedir prazo")


def test_worker_drops_a_start_that_waited_behind_a_slow_call(qapp):
    fake = FakePorts()
    worker = Worker(fake.ports(), Settings())
    seen = Catcher(worker)
    worker.start(1, time.monotonic() - 5)
    assert seen.events == [("started", 1, StartResult(StartStatus.EXPIRED))]
    assert fake.calls == []


def preview_of(reply):
    from respondoia.llm import SuggestionPreview

    return SuggestionPreview(summary=reply.summary, suggestions=reply.suggestions)


def test_preview_fills_the_panel_and_is_logged(rig, caplog):
    reply = make_reply()
    with caplog.at_level(logging.INFO, logger="respondoia"):
        rig.press()
        rig.worker.started.emit(1, STARTED)
        rig.worker.previewed.emit(1, preview_of(reply))
        assert rig.panel.calls[-1] == ("show_preview", preview_of(reply))
        rig.worker.replied.emit(1, reply)
    assert rig.panel.calls[-1] == ("show_reply", reply)
    assert "suggestion preview total_ms=" in caplog.text


def test_choice_during_the_stream_waits_for_the_worker(rig):
    rig.press()
    rig.worker.started.emit(1, STARTED)
    rig.worker.previewed.emit(1, preview_of(make_reply()))
    rig.panel.suggestion_chosen.emit(2)
    assert rig.panel.calls[-1] == ("dismiss",)
    assert rig.worker.calls[-1] == ("choose", 1, 2)
    rig.worker.replied.emit(1, make_reply())  # the list arrives after the choice
    rig.worker.failed.emit(1, "truncated")  # or the end of the stream broke
    assert rig.panel.calls[-1] == ("dismiss",)  # the panel stays closed
    rig.worker.chosen.emit(1, ChooseResult.PASTED)
    rig.press()
    assert rig.worker.calls[-1][0] == "start"


def test_a_choice_that_cannot_be_made_is_reported(rig):
    rig.press()
    rig.worker.started.emit(1, STARTED)
    rig.worker.previewed.emit(1, preview_of(make_reply()))
    rig.panel.suggestion_chosen.emit(0)
    rig.worker.choose_failed.emit(1, "refused")
    assert rig.tray.notices() == ["error.refused"]
    rig.press()
    assert rig.worker.calls[-1][0] == "start"


def test_worker_reports_a_choice_after_a_refusal(qapp):
    fake = FakePorts()
    worker = Worker(fake.ports(), Settings())
    seen = Catcher(worker)
    worker.start(1, time.monotonic())
    fake.suggest_error = LlmError("refused")
    worker.suggest(1)
    worker.choose(1, 0)
    assert seen.events[-1] == ("choose_failed", 1, "refused")
    assert fake.count("insert_text") == 0


def test_apply_settings_with_a_taken_hotkey_saves_nothing(rig):
    from dataclasses import replace

    rig.hotkey.taken = True
    assert rig.controller.apply_settings(replace(Settings(), hotkey="ctrl+alt+r")) is False
    assert not rig.config_file.exists()
    assert "set_settings" not in rig.worker.names()
    assert rig.tray.calls[-1] == ("notify", "settings.hotkey_in_use", {"hotkey": "Ctrl+Alt+R"})


def test_apply_settings_updates_file_worker_tray_and_autostart(rig, monkeypatch):
    from dataclasses import replace

    from respondoia.config import load_settings

    switched = []
    monkeypatch.setattr(app_module.autostart, "set_enabled", switched.append)
    new = replace(Settings(), suggestion_count=2, language="en", start_with_windows=True)
    assert rig.controller.apply_settings(new) is True
    assert load_settings(rig.config_file) == new
    assert ("set_settings", new) in rig.worker.calls
    assert "retranslate" in rig.tray.names() and switched == [True]
    assert "replace" not in rig.hotkey.names()  # same hotkey: nothing to change


def test_apply_settings_closes_an_open_session_and_changes_the_hotkey(rig, monkeypatch):
    from dataclasses import replace

    monkeypatch.setattr(app_module.autostart, "set_enabled", lambda enabled: None)
    rig.open_panel()
    assert rig.controller.apply_settings(replace(Settings(), hotkey="ctrl+alt+r")) is True
    assert rig.panel.calls[-1] == ("dismiss",) or ("dismiss",) in rig.panel.calls
    assert "close" in rig.worker.names() and "replace" in rig.hotkey.names()


def test_settings_from_the_tray_open_the_window(rig, monkeypatch):
    opened = []
    monkeypatch.setattr(rig.controller, "open_settings", lambda: opened.append(True))
    rig.tray.settings_requested.emit()
    assert opened == [True]


def test_panel_gets_corner_and_translated_instructions(tmp_path):
    from dataclasses import replace

    rig = Rig(
        tmp_path,
        replace(
            Settings(),
            panel_position="top-left",
            quick_instructions=("ask_for_time", "Responder com carinho"),
        ),
    )
    assert ("set_corner", "top-left") in rig.panel_setup
    assert (
        "set_quick_instructions",
        [
            ("Ask for more time", "Ask for more time"),
            ("Responder com carinho", "Responder com carinho"),
        ],
    ) in rig.panel_setup


def test_apply_settings_updates_the_panel(rig, monkeypatch):
    from dataclasses import replace

    monkeypatch.setattr(app_module.autostart, "set_enabled", lambda enabled: None)
    rig.controller.apply_settings(replace(Settings(), panel_position="bottom-left"))
    assert (
        rig.panel.calls[-2:][0] == ("set_corner", "bottom-left")
        or ("set_corner", "bottom-left") in rig.panel.calls
    )


def test_settings_from_the_panel_open_the_window(rig, monkeypatch):
    opened = []
    monkeypatch.setattr(rig.controller, "open_settings", lambda: opened.append(True))
    rig.panel.settings_requested.emit()
    assert opened == [True]


def test_fallback_notifies_once_per_session(rig):
    rig.press()
    rig.worker.started.emit(1, STARTED)
    rig.worker.fell_back.emit(1)
    rig.worker.fell_back.emit(1)  # a retry falls back again
    assert rig.tray.notices() == ["notice.screenshot_fallback"]
    rig.worker.fell_back.emit(0)  # an older session
    assert rig.tray.notices() == ["notice.screenshot_fallback"]


def test_worker_reports_the_fallback(qapp):
    fake = FakePorts()  # no lines read: the session sends the screenshot
    worker = Worker(fake.ports(), Settings())
    seen = Catcher(worker)
    worker.start(1, time.monotonic())
    worker.suggest(1)
    assert ("fell_back", 1) in seen.events


def test_saving_the_window_keeps_a_contact_marked_private_meanwhile(rig, monkeypatch, qtbot):
    from PySide6.QtCore import Qt

    from respondoia.config import load_settings

    monkeypatch.setattr(app_module.autostart, "set_enabled", lambda enabled: None)
    rig.controller.open_settings()
    window = rig.controller._settings_window
    qtbot.addWidget(window)
    rig.open_panel()
    rig.panel.mark_private_requested.emit()  # the window was opened before this
    qtbot.mouseClick(window.save_button, Qt.MouseButton.LeftButton)
    assert load_settings(rig.config_file).private_contacts == ("Marina Duarte",)


def test_a_contact_removed_in_the_window_is_removed(tmp_path, monkeypatch, qtbot):
    from dataclasses import replace

    from PySide6.QtCore import Qt

    from respondoia.config import load_settings

    monkeypatch.setattr(app_module.autostart, "set_enabled", lambda enabled: None)
    rig = Rig(tmp_path, replace(Settings(), private_contacts=("Caio Lima", "Bia Rocha")))
    rig.controller.open_settings()
    window = rig.controller._settings_window
    qtbot.addWidget(window)
    window.private_list.setCurrentRow(0)
    qtbot.mouseClick(window.private_remove, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(window.save_button, Qt.MouseButton.LeftButton)
    assert load_settings(rig.config_file).private_contacts == ("Bia Rocha",)


def test_a_new_hotkey_registers_even_if_the_old_one_never_did(tmp_path, monkeypatch):
    from dataclasses import replace

    monkeypatch.setattr(app_module.autostart, "set_enabled", lambda enabled: None)
    hotkey = FakeHotkey()
    hotkey.taken = True
    rig_settings = Settings()
    controller = Controller(
        FakeWorker(), FakePanel(), FakeTray(), hotkey, rig_settings, config_file=tmp_path / "c.toml"
    )
    assert controller.resume() is False and not hotkey.registered
    hotkey.taken = False
    assert controller.apply_settings(replace(rig_settings, hotkey="ctrl+alt+r")) is True
    assert hotkey.registered


def test_a_hotkey_changed_while_paused_registers_on_resume(rig, monkeypatch):
    from dataclasses import replace

    from respondoia.hotkey import parse_hotkey

    monkeypatch.setattr(app_module.autostart, "set_enabled", lambda enabled: None)
    rig.tray.pause_toggled.emit(True)
    assert rig.controller.apply_settings(replace(Settings(), hotkey="ctrl+alt+r")) is True
    assert not rig.hotkey.registered
    rig.tray.pause_toggled.emit(False)
    assert rig.hotkey.registered and rig.hotkey.calls[-1] == (
        "register",
        parse_hotkey("ctrl+alt+r"),
    )
