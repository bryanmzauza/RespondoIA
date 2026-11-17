"""Wires the tray, the hotkey, the panel and the worker thread together."""

import logging
import os
import random
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date
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
from respondoia.knowledge import create_knowledge, knowledge_path
from respondoia.llm import (
    AnthropicClient,
    LlmError,
    PreviewCallback,
    SuggestRequest,
    SuggestResult,
)
from respondoia.memory import scroll
from respondoia.memory.loader import LoaderPorts, LoadLimits, load
from respondoia.memory.service import HistoryContext, HistoryService
from respondoia.privacy import is_private, normalize_name
from respondoia.session import (
    NO_HISTORY,
    NO_STYLE,
    ChooseResult,
    HistoryPort,
    Ports,
    Session,
    StartResult,
    StartStatus,
    StylePort,
)
from respondoia.style.parser import ExportChat, ExportError, parse_export
from respondoia.style.service import StyleService
from respondoia.ui.memory_view import MemoryWindow
from respondoia.ui.panel import SuggestionPanel
from respondoia.ui.settings import SettingsWindow
from respondoia.ui.style_view import StyleWindow
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
    history_changed = Signal(int, object)  # generation, HistoryContext after an answer
    history_grew = Signal(int)  # contact id whose stored history grew

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
            self._report_growth(result.history)

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

    @Slot(int, object)
    def confirm_identity(self, generation: int, contact_id) -> None:
        session = self._session
        if session is None:
            return

        def confirm():
            history = session.confirm_identity(contact_id)
            self.history_changed.emit(generation, history)
            self._report_growth(history)

        self._guard(generation, confirm)

    @Slot()
    def drop_history(self) -> None:
        if self._session is not None:
            self._session.drop_history()

    def _report_growth(self, history: HistoryContext | None) -> None:
        if history is not None and history.state == "ready" and history.added > 0:
            self.history_grew.emit(history.contact_id)

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

    def __init__(
        self, service: StyleService, private_contacts: Callable[[], Sequence[str]] = tuple
    ) -> None:
        super().__init__()
        self._service = service
        self._private = private_contacts
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
        # Nothing is kept from a chat with a private contact.
        others = [name for name, _ in chat.authors if name != author]
        if any(is_private(name, self._private()) for name in others):
            self.failed.emit("export_private")
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


@dataclass(frozen=True)
class LoadOutcome:
    purpose: str  # "quick", "full", "gap" or "style"
    reason: str  # why the loader stopped, see LoadReport
    read: int  # lines read
    history: HistoryContext | None  # None for the style screen
    style_added: int  # the user's own messages added to the style


class Desktop:
    """What the loader does to the desktop besides scrolling."""

    def focus(self, hwnd: int) -> bool:
        return paste.focus_window(hwnd)

    def cursor_pos(self) -> tuple[int, int]:
        return scroll.cursor_pos()

    def put_cursor(self, x: int, y: int) -> None:
        scroll.put_cursor(x, y)


def real_loader_ports(guard) -> Callable[[int], LoaderPorts]:
    def make(hwnd: int) -> LoaderPorts:
        return LoaderPorts(
            read=lambda: reader.read_chat_view(hwnd),
            scroll=scroll.wheel,
            foreground=scroll.foreground_window,
            stopped=guard.stopped,
            sleep=time.sleep,
            random=random.uniform,
            today=date.today,
            under_cursor=lambda: scroll.window_under_cursor(hwnd),
        )

    return make


def _limits(purpose: str, settings: Settings) -> LoadLimits:
    if purpose == "quick":
        return LoadLimits(settings.quick_load_screens, settings.max_load_months)
    gap = purpose == "gap"
    return LoadLimits(settings.max_load_screens, settings.max_load_months, stop_on_join=gap)


class HistoryWorker(QObject):
    """Runs the loader and the memory updates off the hotkey's thread."""

    loaded = Signal(object)  # LoadOutcome

    def __init__(
        self,
        history: HistoryService,
        style: StyleService,
        settings: Callable[[], Settings],
        loader_ports: Callable[[int], LoaderPorts],
        desktop: Desktop | None = None,
        avatar: Callable[[int, object], str] = lambda hwnd, rect: "",
    ) -> None:
        super().__init__()
        self._history = history
        self._style = style
        self._settings = settings
        self._loader_ports = loader_ports
        self._desktop = desktop or Desktop()
        self._avatar = avatar

    @Slot(str, int, str, object)
    def load(self, purpose: str, hwnd: int, title: str, contact_id) -> None:
        try:
            outcome = self._load(purpose, hwnd, title, contact_id)
        except Exception as exc:  # noqa: BLE001 - a failed load must not end the app
            logger.error("load error type=%s", type(exc).__name__)
            outcome = LoadOutcome(purpose, "unreadable", 0, None, 0)
        self.loaded.emit(outcome)

    def _load(self, purpose: str, hwnd: int, title: str, contact_id) -> LoadOutcome:
        settings = self._settings()
        ports = self._loader_ports(hwnd)
        view = ports.read()
        if view is None:
            return LoadOutcome(purpose, "unreadable", 0, None, 0)
        title = title or view.chat.title
        private = settings.private_contacts
        if is_private(view.chat.title, private) or is_private(title, private):
            return LoadOutcome(purpose, "private", 0, None, 0)
        if normalize_name(view.chat.title) != normalize_name(title):
            return LoadOutcome(purpose, "chat_changed", 0, None, 0)
        old_cursor = self._desktop.cursor_pos()
        if not self._desktop.focus(hwnd):
            return LoadOutcome(purpose, "focus", 0, None, 0)
        bounds = view.chat.bounds
        self._desktop.put_cursor(
            (bounds.left + bounds.right) // 2, (bounds.top + bounds.bottom) // 2
        )

        def joins(block) -> bool:
            return purpose == "gap" and self._history.reaches_older(contact_id, block)

        report = load(
            hwnd,
            title,
            _limits(purpose, settings),
            ports,
            joins,
            private=lambda name: is_private(name, private),
        )
        if report.reason != "user":
            self._desktop.put_cursor(*old_cursor)
        style_added = self._style.learn_loaded(title, report.lines)
        history = None
        if purpose != "style":
            try:
                avatar = self._avatar(hwnd, view.chat.avatar)
            except Exception:  # noqa: BLE001 - no photo only weakens the identity
                avatar = ""
            history = self._history.store_loaded(
                title, report.lines, report.reached_start, avatar
            )
            self._history.apply_retention()  # a long load can reach past the retention
        logger.info(
            "load stopped purpose=%s reason=%s screens=%d lines=%d ms=%d",
            purpose,
            report.reason,
            report.screens,
            len(report.lines),
            report.ms,
        )
        return LoadOutcome(purpose, report.reason, len(report.lines), history, style_added)

    @Slot(object)
    def startup(self, private_contacts) -> None:
        """Nothing stays from a contact made private, even by hand in the config file."""
        for name in private_contacts:
            self._style.forget(name)
        self._history.apply_retention()

    @Slot(str, object)
    def forget_contact(self, title: str, contact_id) -> None:
        self._history.forget_contact(title, contact_id)

    @Slot()
    def forget_all(self) -> None:
        self._history.forget_all()

    @Slot(str, object, bool)
    def set_contact_history(self, title: str, contact_id, on: bool) -> None:
        self._history.set_contact_history(title, contact_id, on)



class MemoryWorker(QObject):
    """The contact memory on its own thread: a first memory can take minutes of calls, and
    loads and deletes must not wait behind it."""

    memory_updated = Signal(int)  # contact id
    memory_ready = Signal(int, str)  # contact id, memory text

    def __init__(self, history: HistoryService) -> None:
        super().__init__()
        self._history = history

    @Slot(int)
    def update_memory(self, contact_id: int) -> None:
        if self._history.update_memory_if_due(contact_id):
            self.memory_updated.emit(contact_id)

    @Slot(int)
    def load_memory(self, contact_id: int) -> None:
        self.memory_ready.emit(contact_id, self._history.memory(contact_id))

    @Slot(int, str)
    def save_memory(self, contact_id: int, text: str) -> None:
        self._history.save_memory(contact_id, text)


class Controller(QObject):
    """Interface-thread logic: one session at a time, from the hotkey to the notice."""

    request_start = Signal(int, float)
    request_suggest = Signal(int)
    request_regenerate = Signal(int, tuple, str)
    request_choose = Signal(int, int)
    request_close = Signal()
    request_settings = Signal(object)
    request_forget = Signal(str)
    request_set_label = Signal(str, object)
    request_refresh_due = Signal(str)
    request_style_load = Signal()
    request_import_file = Signal(str)
    request_import_chosen = Signal(str, object)
    request_import_cancel = Signal()
    request_refresh = Signal(str)
    request_save_manual = Signal(str, str)
    request_exclude_example = Signal(str, str)
    request_style_reset = Signal()
    request_history_load = Signal(str, int, str, object)  # purpose, hwnd, title, contact id
    request_memory_update = Signal(int)
    request_history_startup = Signal(object)
    request_confirm = Signal(int, object)  # generation, contact id or None
    request_forget_contact_history = Signal(str, object)
    request_forget_all_history = Signal()
    request_contact_history = Signal(str, object, bool)
    request_load_memory = Signal(int)
    request_save_memory = Signal(int, str)
    request_drop_history = Signal()

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
        history_worker=None,
        guard=None,
        memory_worker=None,
    ):
        super().__init__()
        self._style_worker = style_worker
        self._history_worker = history_worker
        self._guard_input = guard  # InputGuard: stops the loader on real input
        self._hwnd = 0
        self._history: HistoryContext | None = None
        self._loading = False
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
        self._pending_key: str | None = None  # API key typed in the settings window
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
        self.request_set_label.connect(worker.set_label)
        self._style_window: StyleWindow | None = None
        self._context = None  # the open chat's UserContext
        if style_worker is not None:
            self.request_refresh_due.connect(style_worker.refresh_if_due)
            worker.learned.connect(self._on_learned)
            self.request_style_load.connect(style_worker.load)
            self.request_import_file.connect(style_worker.import_file)
            self.request_import_chosen.connect(style_worker.import_chosen)
            self.request_import_cancel.connect(style_worker.cancel_import)
            self.request_refresh.connect(style_worker.refresh)
            self.request_save_manual.connect(style_worker.save_manual)
            self.request_exclude_example.connect(style_worker.exclude_example)
            self.request_style_reset.connect(style_worker.reset)
            style_worker.overview_ready.connect(self._on_style_overview)
            style_worker.authors_found.connect(self._on_authors_found)
            style_worker.imported.connect(self._on_imported)
            style_worker.refreshed.connect(self._on_refreshed)
            style_worker.failed.connect(self._on_style_failed)
        if history_worker is not None:
            memory = memory_worker or history_worker
            self.request_history_load.connect(history_worker.load)
            self.request_memory_update.connect(memory.update_memory)
            self.request_history_startup.connect(history_worker.startup)
            self.request_forget_contact_history.connect(history_worker.forget_contact)
            self.request_forget_all_history.connect(history_worker.forget_all)
            self.request_contact_history.connect(history_worker.set_contact_history)
            self.request_load_memory.connect(memory.load_memory)
            self.request_save_memory.connect(memory.save_memory)
            self.request_confirm.connect(worker.confirm_identity)
            history_worker.loaded.connect(self._on_loaded)
            memory.memory_ready.connect(self._on_memory_ready)
            worker.history_changed.connect(self._on_history_changed)
            worker.history_grew.connect(self.request_memory_update)
            self.request_drop_history.connect(worker.drop_history)
            panel.load_requested.connect(self.request_load)
            panel.identity_answered.connect(self.answer_identity)
            panel.memory_requested.connect(self.open_memory)
            panel.contact_history_toggled.connect(self._on_contact_history)
            panel.contact_history_deleted.connect(self._on_contact_history_deleted)
        self._memory_window: MemoryWindow | None = None
        self._memory_owner = ""
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
        tray.style_requested.connect(lambda: self.open_style())
        panel.style_requested.connect(lambda: self.open_style())
        panel.label_chosen.connect(self._on_label_chosen)
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
        self._context = result.context
        self._hwnd = result.hwnd
        self._history = result.history
        if self._history_worker is not None:
            self._panel.set_history(result.history)
        self._last_request = ("suggest",)
        self._panel.show_loading(result.chat.title, result.chat.bounds)
        if result.context is not None:
            context = result.context
            self._panel.set_label(
                context.label, context.label is not None and not context.label_from_user
            )
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
        context = self._context
        if context is not None and context.ask_label and reply.contact_label:
            self._panel.set_label(reply.contact_label, True)
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

    def _on_label_chosen(self, label: str | None) -> None:
        if self._active and self._chat is not None:
            self.request_set_label.emit(self._chat.title, label)

    def open_style(self) -> None:
        if self._style_worker is None:
            return
        if self._style_window is not None and self._style_window.isVisible():
            self._style_window.raise_()
            self._style_window.activateWindow()
            return
        window = StyleWindow()
        window.import_requested.connect(self._on_import_requested)
        window.author_chosen.connect(self._on_author_chosen)
        window.import_cancelled.connect(self.request_import_cancel)
        window.refresh_requested.connect(self._on_refresh_requested)
        window.manual_saved.connect(self.request_save_manual)
        window.example_removed.connect(self.request_exclude_example)
        window.reset_requested.connect(self.request_style_reset)
        if self._history_worker is not None:
            window.load_requested.connect(lambda: self.request_load("style"))
        self._style_window = window
        window.show()
        self.request_style_load.emit()

    def open_knowledge(self) -> None:
        """Create the file from the translated template the first time, then open it."""
        path = knowledge_path()
        try:
            create_knowledge(path, i18n.t("knowledge.template"))
        except OSError:
            self._tray.notify("error.save_failed")
            return
        try:
            os.startfile(str(path))
        except OSError:  # no application is associated with .md files
            subprocess.Popen(["notepad.exe", str(path)])

    def _on_import_requested(self, path: str) -> None:
        self._style_window.show_busy("style.importing")
        self.request_import_file.emit(path)

    def _on_author_chosen(self, author: str, label) -> None:
        self._style_window.show_busy("style.importing")
        self.request_import_chosen.emit(author, label)

    def _on_refresh_requested(self, language: str) -> None:
        if not config.get_api_key():
            self._style_window.show_error("error.no_api_key")
            return
        self._style_window.show_busy("style.refreshing")
        self.request_refresh.emit(language)

    def _on_style_overview(self, overview) -> None:
        if self._style_window is not None:
            self._style_window.show_overview(overview)

    def _on_authors_found(self, authors) -> None:
        if self._style_window is not None:
            self._style_window.ask_author(authors)

    def _on_imported(self, result) -> None:
        if self._style_window is not None:
            self._style_window.show_notice("style.imported", count=result.added)

    def _on_refreshed(self, language: str) -> None:
        if self._style_window is not None and self._style_window.isVisible():
            self._style_window.show_notice("style.refreshed")

    def _on_style_failed(self, code: str) -> None:
        if self._style_window is not None:
            self._style_window.show_error("error." + code)

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
        self._pending_key = None
        window.api_key_entered.connect(self._on_key_entered)
        window.knowledge_requested.connect(self.open_knowledge)
        if self._history_worker is not None:
            window.delete_all_requested.connect(self._on_delete_all_history)
        self._settings_window = window
        window.show()

    def _on_key_entered(self, key: str) -> None:
        self._pending_key = key

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
        except (ConfigError, OSError) as exc:
            if hotkey_changed:
                try:
                    self._hotkey.replace(parse_hotkey(old.hotkey))
                except HotkeyRegistrationError:
                    self._hotkey.unregister()
            if isinstance(exc, ConfigError):
                self._settings_error("error.config", key=exc.key)
            else:
                self._settings_error("error.save_failed")
            return False
        self._settings = new
        self.request_settings.emit(new)
        if self._pending_key:
            # Stored only now: a key typed in a window whose save failed is not kept.
            config.set_api_key(self._pending_key)
            self._pending_key = None
        if new.language != old.language:
            language = i18n.pick_language(new.language, i18n.system_locale())
            i18n.install(QCoreApplication.instance(), language)
            self._tray.retranslate()
        self._apply_to_panel(new)
        if new.start_with_windows != old.start_with_windows:
            try:
                autostart.set_enabled(new.start_with_windows)
            except OSError:
                self._tray.notify("error.save_failed")
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
        if self._loading and self._guard_input is not None:
            self._guard_input.stop()
        self._hotkey.unregister()
        if self._active:
            self._panel.dismiss()
            self._end_session()

    # History.

    def start_history(self) -> None:
        """Once at start: forget private contacts, drop messages past the retention."""
        if self._history_worker is not None:
            self.request_history_startup.emit(tuple(self._settings.private_contacts))

    def request_load(self, purpose: str) -> None:
        """Scroll the open chat and read it. The panel closes: WhatsApp needs the focus."""
        if self._history_worker is None or self._guard_input is None or self._loading:
            return
        if purpose == "style":
            window = capture.find_whatsapp_window(self._settings.allowed_windows)
            if window is None:
                self._tray.notify("error.no_whatsapp")
                return
            hwnd, title, contact_id = window.hwnd, "", None
        else:
            if not self._active or self._chat is None or not self._hwnd:
                return
            hwnd, title = self._hwnd, self._chat.title
            contact_id = self._history.contact_id if self._history else None
        # The delay lets the click that asked for the load settle before listening.
        if not self._guard_input.arm(delay_s=0.6):
            self._tray.notify("error.load_unavailable")
            return
        if purpose != "style":
            self._panel.dismiss()
            self._end_session()
        self._loading = True
        self._tray.set_loading(True)
        self._tray.notify("notice.loading")
        self.request_history_load.emit(purpose, hwnd, title, contact_id)

    def _on_loaded(self, outcome) -> None:
        self._guard_input.disarm()
        self._loading = False
        self._tray.set_loading(False)
        history = outcome.history
        if history is not None and history.state == "ask":
            self._tray.notify("notice.load_ask")
        else:
            self._tray.notify(f"notice.load_{outcome.reason}", count=outcome.read)
        if history is not None and history.state == "ready" and history.added > 0:
            self.request_memory_update.emit(history.contact_id)
        if outcome.purpose == "style" and self._style_worker is not None:
            if self._style_window is not None and outcome.reason != "private":
                self._style_window.show_notice("style.loaded", count=outcome.style_added)
            self.request_style_load.emit()

    def answer_identity(self, contact_id) -> None:
        """The user said which stored contact this is, or None for another person."""
        if self._active:
            self.request_confirm.emit(self._generation, contact_id)

    def _on_history_changed(self, generation: int, history) -> None:
        if generation == self._generation and self._active:
            self._history = history
            self._panel.set_history(history)

    def _on_contact_history(self, on: bool) -> None:
        history = self._history
        if not self._active or self._chat is None or history is None:
            return
        self.request_contact_history.emit(self._chat.title, history.contact_id, on)
        if on:
            self._history = HistoryContext("ready", history.contact_id, history.owner)
        else:
            # Turning a contact off deletes what is stored; this session stops using it too.
            self.request_drop_history.emit()
            self._history = HistoryContext("out_of_scope", history.contact_id, history.owner)
        self._panel.set_history(self._history)

    def _on_contact_history_deleted(self) -> None:
        history = self._history
        if not self._active or self._chat is None or history is None:
            return
        self.request_forget_contact_history.emit(self._chat.title, history.contact_id)
        self.request_drop_history.emit()
        self._history = HistoryContext(
            "ready", history.contact_id, history.owner, history.owner_since
        )
        self._panel.set_history(self._history)

    def _on_delete_all_history(self) -> None:
        self.request_forget_all_history.emit()
        self.request_drop_history.emit()

    def open_memory(self) -> None:
        history = self._history
        if history is None or history.contact_id is None or history.state != "ready":
            return
        if self._memory_window is None:
            self._memory_window = MemoryWindow()
            self._memory_window.saved.connect(self.request_save_memory)
        self._memory_owner = history.owner
        self.request_load_memory.emit(history.contact_id)

    def _on_memory_ready(self, contact_id: int, text: str) -> None:
        if self._memory_window is None:
            return
        self._memory_window.show_memory(contact_id, self._memory_owner, text)
        self._memory_window.show()
        self._memory_window.raise_()

    def _end_session(self) -> None:
        """Leave the current session. Answers still on their way will be dropped."""
        self._active = False
        self._choosing = False
        self._chat = None
        self._context = None
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


def build_ports(
    current: Callable[[], Settings], style: StylePort = NO_STYLE, history: HistoryPort = NO_HISTORY
) -> Ports:
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
        history=history,
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
    history = HistoryService(
        db, lambda: model_client(lambda: worker.settings), lambda: worker.settings
    )
    guard = scroll.InputGuard(app)
    thread = QThread()
    # The ports read the worker's settings, which the controller updates on every save.
    worker = Worker(build_ports(lambda: worker.settings, style, history), settings)
    worker.moveToThread(thread)
    thread.start()
    style_thread = QThread()
    style_worker = StyleWorker(style, lambda: worker.settings.private_contacts)
    style_worker.moveToThread(style_thread)
    style_thread.start()
    history_thread = QThread()
    history_worker = HistoryWorker(
        history, style, lambda: worker.settings, real_loader_ports(guard)
    )
    history_worker.moveToThread(history_thread)
    history_thread.start()
    memory_thread = QThread()
    memory_worker = MemoryWorker(history)
    memory_worker.moveToThread(memory_thread)
    memory_thread.start()
    controller = Controller(
        worker,
        panel,
        tray,
        hotkey,
        settings,
        config_error=config_error,
        style_worker=style_worker,
        history_worker=history_worker,
        guard=guard,
        memory_worker=memory_worker,
    )
    tray.quit_requested.connect(app.quit)

    if bad_key:
        tray.notify("error.config", key=bad_key)
    if controller.resume():
        tray.notify("notice.started", hotkey=display_hotkey(settings.hotkey))
    if config_error is None:
        controller.start_history()
    logger.info("app started")

    code = app.exec()
    hotkey.unregister()
    guard.disarm()
    for running in (thread, style_thread, history_thread, memory_thread):
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
