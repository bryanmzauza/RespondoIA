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
from respondoia.domain import ChatLine, ChatRef, ChatView, OpenChat, Rect, WindowInfo
from respondoia.llm import (
    LlmError,
    PreviewCallback,
    Suggestion,
    SuggestionPreview,
    SuggestionReply,
    SuggestRequest,
    SuggestResult,
    check_reply,
)
from respondoia.privacy import (
    PasteDecision,
    SendDecision,
    check_before_paste,
    check_before_send,
    mask_lines,
)

# Log lines carry event names, durations, model ids and error codes. Never a chat
# title, a message or a suggestion.
logger = logging.getLogger("respondoia")


@dataclass(frozen=True)
class Ports:
    foreground_window: Callable[[], WindowInfo | None]
    window_info: Callable[[int], WindowInfo | None]
    read_chat_view: Callable[[int], ChatView | None]  # title, bounds and visible lines
    capture_chat: Callable[[int, Rect], bytes]  # hwnd, chat bounds -> PNG bytes
    suggest: Callable[[SuggestRequest, PreviewCallback | None], SuggestResult]
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
        self._reply: SuggestionReply | None = None  # last complete reply
        self._choices: list[Suggestion] | None = None  # what the panel shows now
        self._failure: str | None = None  # code of the last failed request
        self._recheck = False
        self._lines: tuple[ChatLine, ...] = ()  # read at start, never again
        self._masked: tuple[ChatLine, ...] | None = None  # what is sent in text mode
        self._fell_back = False

    @property
    def chat(self) -> ChatRef | None:
        return self._chat

    @property
    def fell_back(self) -> bool:
        """True when text was wanted but the screenshot had to be sent."""
        return self._fell_back

    def start(self) -> StartResult:
        window = self._ports.foreground_window()
        if window is None or not is_allowed_window(window, self._settings.allowed_windows):
            return StartResult(StartStatus.NOT_WHATSAPP)
        # The title is read locally first: it decides whether anything else may be read.
        view = self._ports.read_chat_view(window.hwnd)
        chat = view.chat if view is not None else None
        decision = check_before_send(chat, self._settings.private_contacts)
        if decision is not SendDecision.ALLOW:
            logger.info("start refused reason=%s", decision.value)
            return StartResult(StartStatus(decision.value))
        self._chat = ChatRef(window.hwnd, chat.title)
        self._bounds = chat.bounds
        self._lines = view.lines
        logger.info("start ok")
        return StartResult(StartStatus.STARTED, chat)

    def suggest(self, on_preview: PreviewCallback | None = None) -> SuggestionReply:
        """Send the chat as masked text, or capture it once when there is no text."""
        chat = self._open_chat()
        has_text = any(line.text.strip() for line in self._lines)
        if self._settings.input == "text" and has_text:
            if self._masked is None:
                found: tuple[str, ...] = ()
                self._masked = self._lines
                if self._settings.mask_sensitive_data:
                    self._masked, found = mask_lines(self._lines)
                logger.info("suggest input=text lines=%d masked=%d", len(self._lines), len(found))
            request = SuggestRequest(
                None, self._settings.suggestion_count, lines=self._masked
            )
            return self._ask("suggest", request, on_preview)
        self._fell_back = self._settings.input == "text"
        if self._image is None:
            logger.info("suggest input=screenshot fell_back=%s", self._fell_back)
            if self._recheck:
                self._check_chat_again(chat)
            self._recheck = True  # until a capture succeeds
            self._image = self._ports.capture_chat(chat.hwnd, self._bounds)
            self._recheck = False
        request = SuggestRequest(self._image, self._settings.suggestion_count)
        return self._ask("suggest", request, on_preview)

    def regenerate(
        self,
        selected_ids: Sequence[int],
        instruction: str,
        on_preview: PreviewCallback | None = None,
    ) -> SuggestionReply:
        """Ask again with what is already in memory. The screen is not read."""
        self._open_chat()
        if self._masked is not None:
            request = SuggestRequest(
                None,
                self._settings.suggestion_count,
                selected_ids=tuple(selected_ids),
                instruction=instruction,
                lines=self._masked,
            )
            return self._ask("regenerate", request, on_preview)
        if self._image is None or self._reply is None:
            raise RuntimeError("nothing to regenerate from")
        request = SuggestRequest(
            self._image,
            self._settings.suggestion_count,
            previous=self._reply,
            selected_ids=tuple(selected_ids),
            instruction=instruction,
        )
        return self._ask("regenerate", request, on_preview)

    def choose(self, index: int) -> ChooseResult:
        captured = self._open_chat()
        if self._choices is None:
            # Nothing was shown, or the model refused after the options were shown.
            if self._failure is not None:
                raise LlmError(self._failure)
            raise RuntimeError("no reply to choose from")
        text = self._choices[index].text
        try:
            # Read again: the window may be gone or showing another chat by now.
            current = None
            if self._ports.window_info(captured.hwnd) is not None:
                view = self._ports.read_chat_view(captured.hwnd)
                chat = view.chat if view is not None else None
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
        self._choices = None
        self._lines = ()
        self._masked = None
        self._chat = None
        self._bounds = None

    def _check_chat_again(self, captured: ChatRef) -> None:
        """Before a retried capture: same window, same chat, still allowed, new bounds."""
        chat = None
        if self._ports.window_info(captured.hwnd) is not None:
            view = self._ports.read_chat_view(captured.hwnd)
            chat = view.chat if view is not None else None
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

    def _ask(
        self, event: str, request: SuggestRequest, on_preview: PreviewCallback | None
    ) -> SuggestionReply:
        # The options of an earlier reply must never be pasted for this request.
        self._choices = None
        self._failure = None

        def preview(shown: SuggestionPreview) -> None:
            self._choices = shown.suggestions
            if on_preview is not None:
                on_preview(shown)

        started = time.perf_counter()
        try:
            result = self._ports.suggest(request, preview)
            reply = check_reply(result.reply, request.count)
        except LlmError as exc:
            ms = round((time.perf_counter() - started) * 1000)
            logger.info("%s failed code=%s ms=%d", event, exc.code, ms)
            self._failure = exc.code
            if exc.code == "refused":
                self._choices = None
            raise
        self._reply = reply
        self._choices = reply.suggestions
        logger.info(
            "%s ok ms=%d preview_ms=%s model=%s tokens_in=%d tokens_out=%d",
            event,
            result.latency_ms,
            result.preview_ms,
            result.model,
            result.input_tokens,
            result.output_tokens,
        )
        return reply
