"""Wires the tray, the hotkey, the panel and the worker thread together."""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal, Slot
from PySide6.QtWidgets import QApplication

from respondoia import autostart, capture, config, database, i18n, paste, reader
from respondoia.capture import CaptureError
from respondoia.config import ConfigError, Settings
from respondoia.database import Database, DatabaseError
from respondoia.domain import OpenChat
from respondoia.hotkey import (
    GlobalHotkey,
    HotkeyError,
    HotkeyRegistrationError,
    display_hotkey,
    parse_hotkey,
)
from respondoia.knowledge import knowledge_path
from respondoia.llm import (
    AnthropicClient,
    LlmError,
    PreviewCallback,
    SuggestRequest,
    SuggestResult,
)
from respondoia.session import (
    NO_STYLE,
    ChooseResult,
    Ports,
    Session,
    StartResult,
    StartStatus,
    StylePort,
)
from respondoia.style.parser import ExportChat, ExportError, parse_export
from respondoia.style.service import StyleService
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
    fell_back = Signal(int)  # generation: the screenshot was sent instead of text
    learned = Signal(str)  # language of a chat the style just learned from

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
            if result.context is not None and result.context.language:
                self.learned.emit(result.context.language)

        self._guard(generation, begin)

    @Slot(int)
    def suggest(self, generation: int) -> None:
        def call(session, preview):
            try:
                return session.suggest(preview)
            finally:
                if session.fell_back:
                    self.fell_back.emit(generation)

        self._ask(generation, call)

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

    @Slot(str)
    def forget(self, title: str) -> None:
        self._ports.style.forget(title)

    @Slot(str, object)
    def set_label(self, title: str, label: str | None) -> None:
        self._ports.style.set_label(title, label)

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


class StyleWorker(QObject):
    """Runs the slow style work (reading an export, asking the model) off the hotkey's thread."""

    overview_ready = Signal(object)  # StyleOverview
    authors_found = Signal(object)  # ((author, count), ...)
    imported = Signal(object)  # ImportResult
    refreshed = Signal(str)  # language
    failed = Signal(str)  # error code, e.g. "export_format" or "rate_limit"

    def __init__(self, service: StyleService) -> None:
        super().__init__()
        self._service = service
        self._export: ExportChat | None = None  # read and waiting for the user's name

    @Slot()
    def load(self) -> None:
        self.overview_ready.emit(self._service.overview())

    @Slot(str)
    def import_file(self, path: str) -> None:
        self._export = None
        try:
            self._export = parse_export(Path(path).read_bytes(), Path(path).name)
        except ExportError as exc:
            self.failed.emit("export_" + exc.code)
            return
        except OSError:
            self.failed.emit("export_format")
            return
        self.authors_found.emit(self._export.authors)

    @Slot(str, object)
    def import_chosen(self, author: str, label: str | None) -> None:
        chat, self._export = self._export, None
        if chat is None:
            return
        self.imported.emit(self._service.import_export(chat, author, label))
        self.load()

    @Slot()
    def cancel_import(self) -> None:
        self._export = None

    @Slot(str)
    def refresh(self, language: str) -> None:
        try:
            self._service.refresh(language)
        except LlmError as exc:
            self.failed.emit(exc.code)
            return
        self.refreshed.emit(language)
        self.load()

    @Slot(str)
    def refresh_if_due(self, language: str) -> None:
        if self._service.refresh_if_due(language):
            self.refreshed.emit(language)

    @Slot(str, str)
    def save_manual(self, language: str, text: str) -> None:
        self._service.save_manual(language, text)
        self.load()

    @Slot(str, str)
    def exclude_example(self, language: str, text: str) -> None:
        self._service.exclude_example(language, text)
        self.load()

    @Slot()
    def reset(self) -> None:
        self._service.reset()
        self.load()


class Controller(QObject):
    """Interface-thread logic: one session at a time, from the hotkey to the notice."""

    request_start = Signal(int, float)
    request_suggest = Signal(int)
    request_regenerate = Signal(int, tuple, str)
    request_choose = Signal(int, int)
    request_close = Signal()
    request_settings = Signal(object)
    request_forget = Signal(str)
    request_refresh_due = Signal(str)

    def __init__(
        self,
        worker,
        panel,
        tray,
        hotkey,
        settings,
        config_file: Path | None = None,
        config_error: str | None = None,
        style_worker=None,
    ):
        super().__init__()
        self._style_worker = style_worker
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
        self._fallback_shown = False
        self._paused = False
        self._settings_window: SettingsWindow | None = None
        self._window_base = settings  # what the open settings window started from
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
        self.request_forget.connect(worker.forget)
        if style_worker is not None:
            self.request_refresh_due.connect(style_worker.refresh_if_due)
            worker.learned.connect(self._on_learned)
        worker.started.connect(self._on_started)
        worker.previewed.connect(self._on_previewed)
        worker.replied.connect(self._on_replied)
        worker.failed.connect(self._on_failed)
        worker.chosen.connect(self._on_chosen)
        worker.choose_failed.connect(self._on_choose_failed)
        worker.crashed.connect(self._on_crashed)
        worker.fell_back.connect(self._on_fell_back)

        hotkey.triggered.connect(self.on_hotkey)
        panel.suggestion_chosen.connect(self._on_suggestion_chosen)
        panel.regenerate_requested.connect(self._on_regenerate_requested)
        panel.mark_private_requested.connect(self._on_mark_private)
        panel.retry_requested.connect(self._on_retry)
        panel.closed.connect(self._end_session)
        tray.pause_toggled.connect(self._on_pause_toggled)
        tray.settings_requested.connect(lambda: self.open_settings())
        panel.settings_requested.connect(lambda: self.open_settings())
        self._apply_to_panel(settings)

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
        self._fallback_shown = False
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

    def _on_fell_back(self, generation: int) -> None:
        if generation == self._generation and not self._fallback_shown:
            self._fallback_shown = True
            self._tray.notify("notice.screenshot_fallback")

    def _on_learned(self, language: str) -> None:
        # Costs a model call at most once a day per language, and only with a key.
        if config.get_api_key():
            self.request_refresh_due.emit(language)

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
        self.request_forget.emit(self._chat.title)
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
        self._window_base = self._settings
        window.saved.connect(self._on_window_saved)
        window.api_key_entered.connect(config.set_api_key)
        self._settings_window = window
        window.show()

    def _on_window_saved(self, new: Settings) -> None:
        # A contact marked private while the window was open is not in the window's
        # list. Apply only the removals made in the window to the current list.
        removed = set(self._window_base.private_contacts) - set(new.private_contacts)
        kept = tuple(c for c in self._settings.private_contacts if c not in removed)
        self.apply_settings(replace(new, private_contacts=kept))

    def apply_settings(self, new: Settings) -> bool:
        """Apply and save new settings. False, with the reason shown, if nothing changed."""
        old = self._settings
        if self._active:
            self._panel.dismiss()
            self._end_session()
        # While paused nothing is registered; resume() picks up the new hotkey.
        hotkey_changed = new.hotkey != old.hotkey and not self._paused
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
                try:
                    self._hotkey.replace(parse_hotkey(old.hotkey))
                except HotkeyRegistrationError:
                    self._hotkey.unregister()
            self._settings_error("error.config", key=exc.key)
            return False
        self._settings = new
        self.request_settings.emit(new)
        if new.language != old.language:
            language = i18n.pick_language(new.language, i18n.system_locale())
            i18n.install(QCoreApplication.instance(), language)
            self._tray.retranslate()
        self._apply_to_panel(new)
        if new.start_with_windows != old.start_with_windows:
            autostart.set_enabled(new.start_with_windows)
        if self._settings_window is not None:
            self._settings_window.close()
        self._tray.notify("settings.saved")
        return True

    def _apply_to_panel(self, settings: Settings) -> None:
        self._panel.set_corner(settings.panel_position)
        # The instruction sent to the model is the label in the interface language.
        labels = [i18n.instruction_label(entry) for entry in settings.quick_instructions]
        self._panel.set_quick_instructions([(label, label) for label in labels])

    def _settings_error(self, key: str, **values: object) -> None:
        if self._settings_window is not None and self._settings_window.isVisible():
            self._settings_window.show_error(key, **values)
        else:
            self._tray.notify(key, **values)

    def _on_pause_toggled(self, paused: bool) -> None:
        self._paused = paused
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


def open_database(tray) -> Database | None:
    """The encrypted database, or None with one notice: suggestions work without it."""
    try:
        return database.open_default()
    except DatabaseError as exc:
        logger.info("database unavailable code=%s", exc.code)
        tray.notify("error.style_unavailable")
        return None


def model_client(current: Callable[[], Settings]) -> AnthropicClient | None:
    key = config.get_api_key()
    return AnthropicClient(key, current().model) if key else None


def build_ports(current: Callable[[], Settings], style: StylePort = NO_STYLE) -> Ports:
    """current() gives the settings in force, so a change in the window applies at once."""

    def suggest(request: SuggestRequest, on_preview: PreviewCallback | None) -> SuggestResult:
        key = config.get_api_key()
        if not key:
            raise LlmError("auth")
        return AnthropicClient(key, current().model).suggest(request, on_preview)

    return Ports(
        foreground_window=capture.foreground_window,
        window_info=capture.window_info,
        read_chat_view=reader.read_chat_view,
        capture_chat=lambda hwnd, bounds: capture.capture_chat(
            hwnd, bounds, current().max_image_side
        ),
        suggest=suggest,
        insert_text=paste.insert_text,
        copy_text=lambda text: paste.copy_text(paste.prepare_text(text)),
        style=style,
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
    db = open_database(tray)
    # One service for both threads: the database serializes its own access.
    style = StyleService(db, lambda: model_client(lambda: worker.settings), knowledge_path())
    thread = QThread()
    # The ports read the worker's settings, which the controller updates on every save.
    worker = Worker(build_ports(lambda: worker.settings, style), settings)
    worker.moveToThread(thread)
    thread.start()
    style_thread = QThread()
    style_worker = StyleWorker(style)
    style_worker.moveToThread(style_thread)
    style_thread.start()
    controller = Controller(
        worker,
        panel,
        tray,
        hotkey,
        settings,
        config_error=config_error,
        style_worker=style_worker,
    )
    tray.quit_requested.connect(app.quit)

    if bad_key:
        tray.notify("error.config", key=bad_key)
    if controller.resume():
        tray.notify("notice.started", hotkey=display_hotkey(settings.hotkey))
    logger.info("app started")

    code = app.exec()
    hotkey.unregister()
    for running in (thread, style_thread):
        running.quit()
        if not running.wait(3000):
            # A model call is still running. Stop the thread instead of destroying it
            # while it runs, which would abort the process.
            running.terminate()
            running.wait(1000)
    if db is not None:
        db.close()
    logger.info("app stopped")
    return code
