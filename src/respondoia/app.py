"""Wires the tray, the hotkey, the panel and the worker thread together."""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal, Slot
from PySide6.QtWidgets import QApplication

from respondoia import autostart, capture, config, i18n, paste, reader
from respondoia.capture import CaptureError
from respondoia.config import ConfigError, Settings
from respondoia.domain import OpenChat
from respondoia.hotkey import (
    GlobalHotkey,
    HotkeyError,
    HotkeyRegistrationError,
    display_hotkey,
    parse_hotkey,
)
from respondoia.llm import (
    AnthropicClient,
    LlmError,
    PreviewCallback,
    SuggestRequest,
    SuggestResult,
)
from respondoia.session import ChooseResult, Ports, Session, StartResult, StartStatus
from respondoia.ui.panel import SuggestionPanel
from respondoia.ui.settings import SettingsWindow
from respondoia.ui.tray import Tray

logger = logging.getLogger("respondoia")

# A hotkey press that waited longer than this behind a slow call is dropped.
MAX_START_DELAY_S = 1.0

_NOTICES = {
    ChooseResult.COPIED: "notice.copied",
    ChooseResult.COPIED_CHAT_CHANGED: "notice.chat_changed",
    ChooseResult.COPIED_FOCUS_FAILED: "notice.focus_failed",
}


class Worker(QObject):
    """Runs every Session method on the worker thread. The interface only gets signals.

    Each request carries the generation number it belongs to and each answer carries
    it back, so the controller can drop answers to a session it already left.
    """

    started = Signal(int, object)  # generation, StartResult
    previewed = Signal(int, object)  # generation, SuggestionPreview
    replied = Signal(int, object)  # generation, SuggestionReply
    failed = Signal(int, str)  # generation, error code
    chosen = Signal(int, object)  # generation, ChooseResult
    choose_failed = Signal(int, str)  # generation, error code
    crashed = Signal(int)  # generation

    def __init__(self, ports: Ports, settings: Settings) -> None:
        super().__init__()
        self._ports = ports
        self._settings = settings
        self._session: Session | None = None

    @property
    def settings(self) -> Settings:
        return self._settings

    @Slot(object)
    def set_settings(self, settings: Settings) -> None:
        self._settings = settings

    @Slot(int, float)
    def start(self, generation: int, requested_at: float) -> None:
        self.close()
        if time.monotonic() - requested_at > MAX_START_DELAY_S:
            # The window in front now is not the one the hotkey was pressed on.
            logger.info("start expired")
            self.started.emit(generation, StartResult(StartStatus.EXPIRED))
            return

        def begin():
            session = Session(self._ports, self._settings)
            result = session.start()
            if result.status is StartStatus.STARTED:
                self._session = session
            self.started.emit(generation, result)

        self._guard(generation, begin)

    @Slot(int)
    def suggest(self, generation: int) -> None:
        self._ask(generation, lambda session, preview: session.suggest(preview))

    @Slot(int, tuple, str)
    def regenerate(self, generation: int, selected_ids: tuple, instruction: str) -> None:
        self._ask(
            generation,
            lambda session, preview: session.regenerate(selected_ids, instruction, preview),
        )

    @Slot(int, int)
    def choose(self, generation: int, index: int) -> None:
        session, self._session = self._session, None
        if session is None:
            return

        def choose():
            try:
                result = session.choose(index)
            except LlmError as exc:  # the model refused after the options were shown
                self.choose_failed.emit(generation, exc.code)
            else:
                self.chosen.emit(generation, result)

        self._guard(generation, choose)

    @Slot()
    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    def _ask(self, generation: int, call) -> None:
        session = self._session
        if session is None:
            return

        def ask():
            try:
                reply = call(session, lambda shown: self.previewed.emit(generation, shown))
            except LlmError as exc:
                self.failed.emit(generation, exc.code)
            except CaptureError:
                self.failed.emit(generation, "capture")
            else:
                self.replied.emit(generation, reply)

        self._guard(generation, ask)

    def _guard(self, generation: int, step) -> None:
        try:
            step()
        except Exception as exc:  # noqa: BLE001 - the app must outlive any single request
            # Only the class name is logged: an error message can quote chat content.
            logger.error("worker error type=%s", type(exc).__name__)
            self.close()
            self.crashed.emit(generation)


class Controller(QObject):
    """Interface-thread logic: one session at a time, from the hotkey to the notice."""

    request_start = Signal(int, float)
    request_suggest = Signal(int)
    request_regenerate = Signal(int, tuple, str)
    request_choose = Signal(int, int)
    request_close = Signal()
    request_settings = Signal(object)

    def __init__(
        self,
        worker,
        panel,
        tray,
        hotkey,
        settings,
        config_file: Path | None = None,
        config_error: str | None = None,
    ):
        super().__init__()
        # With an invalid config.toml the private list is unknown, so nothing is read.
        self._config_error = config_error
        self._last_request: tuple = ("suggest",)
        # A choice made while the reply is still streaming waits for the worker.
        self._choosing = False
        self._panel = panel
        self._tray = tray
        self._hotkey = hotkey
        self._settings = settings
        self._config_file = config_file
        self._settings_window: SettingsWindow | None = None
        self._generation = 0
        self._active = False
        self._chat: OpenChat | None = None
        self._pressed_at = 0.0
        self._waiting_for = "suggestion"

        self.request_start.connect(worker.start)
        self.request_suggest.connect(worker.suggest)
        self.request_regenerate.connect(worker.regenerate)
        self.request_choose.connect(worker.choose)
        self.request_close.connect(worker.close)
        self.request_settings.connect(worker.set_settings)
        worker.started.connect(self._on_started)
        worker.previewed.connect(self._on_previewed)
        worker.replied.connect(self._on_replied)
        worker.failed.connect(self._on_failed)
        worker.chosen.connect(self._on_chosen)
        worker.choose_failed.connect(self._on_choose_failed)
        worker.crashed.connect(self._on_crashed)

        hotkey.triggered.connect(self.on_hotkey)
        panel.suggestion_chosen.connect(self._on_suggestion_chosen)
        panel.regenerate_requested.connect(self._on_regenerate_requested)
        panel.mark_private_requested.connect(self._on_mark_private)
        panel.retry_requested.connect(self._on_retry)
        panel.closed.connect(self._end_session)
        tray.pause_toggled.connect(self._on_pause_toggled)
        tray.settings_requested.connect(lambda: self.open_settings())

    def on_hotkey(self) -> None:
        if self._config_error:
            return
        if self._active:
            self._panel.bring_to_front()
            return
        if not config.get_api_key():
            self._tray.notify("error.no_api_key")
            return
        self._generation += 1
        self._active = True
        self._choosing = False
        self._pressed_at = time.perf_counter()
        self._waiting_for = "suggestion"
        self.request_start.emit(self._generation, time.monotonic())

    def resume(self) -> bool:
        """Register the hotkey. False, with a notice, if it cannot be used."""
        if self._config_error:
            self._tray.notify("error.config", key=self._config_error)
            return False
        try:
            self._hotkey.register(parse_hotkey(self._settings.hotkey))
        except HotkeyRegistrationError:
            shown = display_hotkey(self._settings.hotkey)
            self._tray.notify("notice.hotkey_in_use", hotkey=shown)
            return False
        return True

    def _on_started(self, generation: int, result) -> None:
        if generation != self._generation or not self._active:
            return
        if result.status is not StartStatus.STARTED:
            # Another window or a private contact: stay silent.
            self._active = False
            if result.status is StartStatus.UNKNOWN_CHAT:
                self._tray.notify("notice.unknown_chat")
            return
        self._chat = result.chat
        self._last_request = ("suggest",)
        self._panel.show_loading(result.chat.title, result.chat.bounds)
        self.request_suggest.emit(generation)

    def _on_previewed(self, generation: int, preview) -> None:
        if generation != self._generation or not self._active or self._choosing:
            return
        self._panel.show_preview(preview)
        total_ms = round((time.perf_counter() - self._pressed_at) * 1000)
        logger.info("%s preview total_ms=%d", self._waiting_for, total_ms)

    def _on_replied(self, generation: int, reply) -> None:
        if generation != self._generation or not self._active or self._choosing:
            return
        self._panel.show_reply(reply)
        total_ms = round((time.perf_counter() - self._pressed_at) * 1000)
        logger.info("%s ok total_ms=%d", self._waiting_for, total_ms)

    def _on_failed(self, generation: int, code: str) -> None:
        # After a choice the worker reports how the choice went; the panel stays closed.
        if generation == self._generation and self._active and not self._choosing:
            self._panel.show_error(code)

    def _on_chosen(self, generation: int, result) -> None:
        if generation != self._generation:
            return
        self._active = False
        self._choosing = False
        if result in _NOTICES:
            self._tray.notify(_NOTICES[result])

    def _on_choose_failed(self, generation: int, code: str) -> None:
        if generation != self._generation:
            return
        self._active = False
        self._choosing = False
        self._tray.notify("error." + code)

    def _on_crashed(self, generation: int) -> None:
        if generation != self._generation:
            return
        self._panel.dismiss()
        self._end_session()
        self._tray.notify("error.unexpected")

    def _on_suggestion_chosen(self, index: int) -> None:
        if self._active and not self._choosing:
            self._choosing = True
            self._panel.dismiss()
            self.request_choose.emit(self._generation, index)

    def _on_regenerate_requested(self, selected_ids: tuple, instruction: str) -> None:
        if self._active and self._chat is not None:
            self._pressed_at = time.perf_counter()
            self._waiting_for = "regenerate"
            self._last_request = ("regenerate", selected_ids, instruction)
            self._panel.show_loading(self._chat.title, self._chat.bounds)
            self.request_regenerate.emit(self._generation, selected_ids, instruction)

    def _on_retry(self) -> None:
        """Repeat the request that failed, with the same messages and instruction."""
        if self._active and self._chat is not None:
            self._pressed_at = time.perf_counter()
            self._panel.show_loading(self._chat.title, self._chat.bounds)
            if self._last_request[0] == "regenerate":
                self.request_regenerate.emit(self._generation, *self._last_request[1:])
            else:
                self.request_suggest.emit(self._generation)

    def _on_mark_private(self) -> None:
        if not self._active or self._chat is None:
            return
        path = self._config_file or config.config_path()
        try:
            self._settings = config.add_private_contact(path, self._chat.title)
        except ConfigError as exc:
            self._tray.notify("error.config", key=exc.key)
            return
        self.request_settings.emit(self._settings)
        self._panel.dismiss()
        self._end_session()
        self._tray.notify("notice.private_added")

    def open_settings(self) -> None:
        if self._config_error:
            # The window would start from defaults and could overwrite the private list.
            self._tray.notify("error.config", key=self._config_error)
            return
        if self._settings_window is not None and self._settings_window.isVisible():
            self._settings_window.raise_()
            self._settings_window.activateWindow()
            return
        window = SettingsWindow(self._settings, bool(config.get_api_key()))
        window.saved.connect(self.apply_settings)
        window.api_key_entered.connect(config.set_api_key)
        self._settings_window = window
        window.show()

    def apply_settings(self, new: Settings) -> bool:
        """Apply and save new settings. False, with the reason shown, if nothing changed."""
        old = self._settings
        if self._active:
            self._panel.dismiss()
            self._end_session()
        hotkey_changed = new.hotkey != old.hotkey and self._hotkey.registered
        if hotkey_changed:
            try:
                self._hotkey.replace(parse_hotkey(new.hotkey))
            except HotkeyRegistrationError:
                shown = display_hotkey(new.hotkey)
                self._settings_error("settings.hotkey_in_use", hotkey=shown)
                return False
        try:
            config.save_settings(self._config_file or config.config_path(), new)
        except ConfigError as exc:
            if hotkey_changed:
                self._hotkey.replace(parse_hotkey(old.hotkey))
            self._settings_error("error.config", key=exc.key)
            return False
        self._settings = new
        self.request_settings.emit(new)
        if new.language != old.language:
            language = i18n.pick_language(new.language, i18n.system_locale())
            i18n.install(QCoreApplication.instance(), language)
            self._tray.retranslate()
        if new.start_with_windows != old.start_with_windows:
            autostart.set_enabled(new.start_with_windows)
        if self._settings_window is not None:
            self._settings_window.close()
        self._tray.notify("settings.saved")
        return True

    def _settings_error(self, key: str, **values: object) -> None:
        if self._settings_window is not None and self._settings_window.isVisible():
            self._settings_window.show_error(key, **values)
        else:
            self._tray.notify(key, **values)

    def _on_pause_toggled(self, paused: bool) -> None:
        if not paused:
            self.resume()
            return
        self._hotkey.unregister()
        if self._active:
            self._panel.dismiss()
            self._end_session()

    def _end_session(self) -> None:
        """Leave the current session. Answers still on their way will be dropped."""
        self._active = False
        self._choosing = False
        self._chat = None
        self._generation += 1
        self.request_close.emit()


def build_ports(current: Callable[[], Settings]) -> Ports:
    """current() gives the settings in force, so a change in the window applies at once."""

    def suggest(request: SuggestRequest, on_preview: PreviewCallback | None) -> SuggestResult:
        key = config.get_api_key()
        if not key:
            raise LlmError("auth")
        return AnthropicClient(key, current().model).suggest(request, on_preview)

    return Ports(
        foreground_window=capture.foreground_window,
        window_info=capture.window_info,
        read_open_chat=reader.read_open_chat,
        capture_chat=lambda hwnd, bounds: capture.capture_chat(
            hwnd, bounds, current().max_image_side
        ),
        suggest=suggest,
        insert_text=paste.insert_text,
        copy_text=lambda text: paste.copy_text(paste.prepare_text(text)),
    )


def _setup_log() -> None:
    handler = RotatingFileHandler(
        config.app_dir() / "respondoia.log", maxBytes=512_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def run(argv: Sequence[str]) -> int:
    app = QApplication(list(argv))
    app.setQuitOnLastWindowClosed(False)

    bad_key = None  # reported, the app still reads chats
    config_error = None  # reported, the hotkey stays off: the private list is unknown
    try:
        settings = config.load_settings(config.config_path())
        parse_hotkey(settings.hotkey)
    except ConfigError as exc:
        settings, config_error = Settings(), exc.key
    except HotkeyError:
        settings, bad_key = replace(settings, hotkey=Settings().hotkey), "general.hotkey"

    i18n.install(app, i18n.pick_language(settings.language, i18n.system_locale()))
    _setup_log()

    tray = Tray()
    tray.show()
    panel = SuggestionPanel()
    hotkey = GlobalHotkey(app)
    thread = QThread()
    # The ports read the worker's settings, which the controller updates on every save.
    worker = Worker(build_ports(lambda: worker.settings), settings)
    worker.moveToThread(thread)
    thread.start()
    controller = Controller(worker, panel, tray, hotkey, settings, config_error=config_error)
    tray.quit_requested.connect(app.quit)

    if bad_key:
        tray.notify("error.config", key=bad_key)
    if controller.resume():
        tray.notify("notice.started", hotkey=display_hotkey(settings.hotkey))
    logger.info("app started")

    code = app.exec()
    hotkey.unregister()
    thread.quit()
    thread.wait(3000)
    logger.info("app stopped")
    return code
