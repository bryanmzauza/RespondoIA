"""One hotkey press, from the foreground window to the pasted reply.

No Qt and no Win32 here: system calls come in as ports, so the safety rules can be
tested without a desktop. A session is used from one thread.
"""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum

from respondoia.capture import CaptureError, is_allowed_window
from respondoia.config import Settings
from respondoia.domain import ChatRef, OpenChat, Rect, WindowInfo
from respondoia.llm import LlmError, SuggestionReply, SuggestRequest, SuggestResult, check_reply
from respondoia.privacy import (
    PasteDecision,
    SendDecision,
    check_before_paste,
    check_before_send,
)

# Log lines carry event names, durations, model ids and error codes. Never a chat
# title, a message or a suggestion.
logger = logging.getLogger("respondoia")


@dataclass(frozen=True)
class Ports:
    foreground_window: Callable[[], WindowInfo | None]
    window_info: Callable[[int], WindowInfo | None]
    read_open_chat: Callable[[int], OpenChat | None]
    capture_chat: Callable[[int, Rect], bytes]  # hwnd, chat bounds -> PNG bytes
    suggest: Callable[[SuggestRequest], SuggestResult]
    insert_text: Callable[[int, str], bool]
    copy_text: Callable[[str], None]


class StartStatus(Enum):
    STARTED = "started"
    NOT_WHATSAPP = "not_whatsapp"
    PRIVATE = "private"
    UNKNOWN_CHAT = "unknown_chat"
    EXPIRED = "expired"  # the request waited too long to run


@dataclass(frozen=True)
class StartResult:
    status: StartStatus
    chat: OpenChat | None = None


class ChooseResult(Enum):
    PASTED = "pasted"
    COPIED = "copied"
    COPIED_CHAT_CHANGED = "copied_chat_changed"
    COPIED_FOCUS_FAILED = "copied_focus_failed"


class Session:
    def __init__(self, ports: Ports, settings: Settings) -> None:
        self._ports = ports
        self._settings = settings
        self._chat: ChatRef | None = None
        self._bounds: Rect | None = None
        self._image: bytes | None = None
        self._reply: SuggestionReply | None = None
        self._recheck = False

    @property
    def chat(self) -> ChatRef | None:
        return self._chat

    def start(self) -> StartResult:
        window = self._ports.foreground_window()
        if window is None or not is_allowed_window(window, self._settings.allowed_windows):
            return StartResult(StartStatus.NOT_WHATSAPP)
        # The title is read locally first: it decides whether anything else may be read.
        chat = self._ports.read_open_chat(window.hwnd)
        decision = check_before_send(chat, self._settings.private_contacts)
        if decision is not SendDecision.ALLOW:
            logger.info("start refused reason=%s", decision.value)
            return StartResult(StartStatus(decision.value))
        self._chat = ChatRef(window.hwnd, chat.title)
        self._bounds = chat.bounds
        logger.info("start ok")
        return StartResult(StartStatus.STARTED, chat)

    def suggest(self) -> SuggestionReply:
        """Capture the chat once, then ask the model."""
        chat = self._open_chat()
        if self._image is None:
            if self._recheck:
                self._check_chat_again(chat)
            self._recheck = True  # until a capture succeeds
            self._image = self._ports.capture_chat(chat.hwnd, self._bounds)
            self._recheck = False
        request = SuggestRequest(self._image, self._settings.suggestion_count)
        return self._ask("suggest", request)

    def regenerate(self, selected_ids: Sequence[int], instruction: str) -> SuggestionReply:
        """Ask again with the image and the reply already in memory. The screen is not read."""
        self._open_chat()
        if self._image is None or self._reply is None:
            raise RuntimeError("nothing to regenerate from")
        request = SuggestRequest(
            self._image,
            self._settings.suggestion_count,
            previous=self._reply,
            selected_ids=tuple(selected_ids),
            instruction=instruction,
        )
        return self._ask("regenerate", request)

    def choose(self, index: int) -> ChooseResult:
        captured = self._open_chat()
        if self._reply is None:
            raise RuntimeError("no reply to choose from")
        text = self._reply.suggestions[index].text
        try:
            # Read again: the window may be gone or showing another chat by now.
            current = None
            if self._ports.window_info(captured.hwnd) is not None:
                chat = self._ports.read_open_chat(captured.hwnd)
                if chat is not None:
                    current = ChatRef(captured.hwnd, chat.title)
            decision = check_before_paste(captured, current, self._settings.insert_mode)
            if decision is PasteDecision.PASTE:
                pasted = self._ports.insert_text(captured.hwnd, text)
                result = ChooseResult.PASTED if pasted else ChooseResult.COPIED_FOCUS_FAILED
            else:
                self._ports.copy_text(text)
                copy_mode = self._settings.insert_mode == "copy"
                result = ChooseResult.COPIED if copy_mode else ChooseResult.COPIED_CHAT_CHANGED
        finally:
            self.close()
        logger.info("choose result=%s", result.value)
        return result

    def close(self) -> None:
        """Drop the screenshot and the reply. The session cannot be used afterwards."""
        self._image = None
        self._reply = None
        self._chat = None
        self._bounds = None

    def _check_chat_again(self, captured: ChatRef) -> None:
        """Before a retried capture: same window, same chat, still allowed, new bounds."""
        chat = None
        if self._ports.window_info(captured.hwnd) is not None:
            chat = self._ports.read_open_chat(captured.hwnd)
        current = ChatRef(captured.hwnd, chat.title) if chat is not None else None
        same = check_before_paste(captured, current, "paste") is PasteDecision.PASTE
        allowed = check_before_send(chat, self._settings.private_contacts)
        if not same or allowed is not SendDecision.ALLOW:
            logger.info("retry refused reason=chat_changed")
            raise CaptureError("the open chat changed")
        self._bounds = chat.bounds

    def _open_chat(self) -> ChatRef:
        if self._chat is None:
            raise RuntimeError("session is not started or is already closed")
        return self._chat

    def _ask(self, event: str, request: SuggestRequest) -> SuggestionReply:
        started = time.perf_counter()
        try:
            result = self._ports.suggest(request)
            reply = check_reply(result.reply, request.count)
        except LlmError as exc:
            ms = round((time.perf_counter() - started) * 1000)
            logger.info("%s failed code=%s ms=%d", event, exc.code, ms)
            raise
        self._reply = reply
        logger.info(
            "%s ok ms=%d model=%s tokens_in=%d tokens_out=%d",
            event,
            result.latency_ms,
            result.model,
            result.input_tokens,
            result.output_tokens,
        )
        return reply
