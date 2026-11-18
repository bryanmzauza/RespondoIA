"""One hotkey press, from the foreground window to the pasted reply.

No Qt and no Win32 here: system calls come in as ports, so the safety rules can be
tested without a desktop. A session is used from one thread.
"""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from typing import Protocol

from respondoia.capture import CaptureError, is_allowed_window
from respondoia.config import Settings
from respondoia.domain import (
    LABELS,
    PICTURE_KINDS,
    ChatLine,
    ChatRef,
    ChatView,
    Look,
    OpenChat,
    Rect,
    UserContext,
    WindowInfo,
)
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
from respondoia.media import hashable, may_send_pictures, pending_pictures
from respondoia.memory.recall import snippet_rows
from respondoia.memory.service import HistoryContext
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


class StylePort(Protocol):
    """The style service as the session sees it. It never raises."""

    def learn(self, title: str, lines: Sequence[ChatLine], is_group: bool) -> str | None: ...
    def context(self, title: str, language: str | None, is_group: bool) -> UserContext: ...
    def guess_label(self, title: str, label: str) -> None: ...
    def set_label(self, title: str, label: str | None) -> None: ...
    def remember_pasted(self, text: str) -> None: ...
    def forget(self, title: str) -> None: ...


class NoStyle:
    """Style off: nothing learned, nothing added to the request."""

    def learn(self, title, lines, is_group):
        return None

    def context(self, title, language, is_group):
        return UserContext(language, None, False, False, "", "")

    def guess_label(self, title, label):
        pass

    def set_label(self, title, label):
        pass

    def remember_pasted(self, text):
        pass

    def forget(self, title):
        pass


NO_STYLE = NoStyle()


class HistoryPort(Protocol):
    """The history service as the session sees it. It never raises."""

    def observe(
        self,
        title: str,
        lines: Sequence[ChatLine],
        is_group: bool,
        avatar: str,
        looks: Sequence[str] = (),
    ) -> HistoryContext: ...
    def confirm(
        self, title: str, lines: Sequence[ChatLine], contact_id: int | None
    ) -> HistoryContext: ...


class NoHistory:
    """History off: nothing stored, nothing attached."""

    def observe(self, title, lines, is_group, avatar, looks=()):
        return HistoryContext("off")

    def confirm(self, title, lines, contact_id):
        return HistoryContext("off")


NO_HISTORY = NoHistory()


def _no_look(
    hwnd: int, avatar: Rect | None, crops: Sequence[Rect], looks: Sequence[Rect], area
) -> Look:
    return Look("", (None,) * len(crops), ("",) * len(looks))


@dataclass(frozen=True)
class Ports:
    foreground_window: Callable[[], WindowInfo | None]
    window_info: Callable[[int], WindowInfo | None]
    read_chat_view: Callable[[int], ChatView | None]  # title, bounds and visible lines
    capture_chat: Callable[[int, Rect], bytes]  # hwnd, chat bounds -> PNG bytes
    suggest: Callable[[SuggestRequest, PreviewCallback | None], SuggestResult]
    insert_text: Callable[[int, str], bool]
    copy_text: Callable[[str], None]
    style: StylePort = NO_STYLE
    history: HistoryPort = NO_HISTORY
    # One capture: hwnd, the contact's photo, photos to send, photos to hash, message
    # list. Pixels stay in memory.
    look: Callable[
        [int, Rect | None, Sequence[Rect], Sequence[Rect], Rect | None], Look
    ] = _no_look
    # The stored contact for this title is a group. Photos never go to a group.
    known_group: Callable[[str], bool] = lambda title: False


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
    context: UserContext | None = None
    history: HistoryContext | None = None
    hwnd: int = 0  # the WhatsApp window, for the history loader
    pictures: int = 0  # photos that will go with the request


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
        self._context: UserContext | None = None
        self._label_guessed = False
        self._history: HistoryContext | None = None
        # (line id, JPEG) of the pending photos, kept until close; never on disk.
        self._pictures: tuple[tuple[int, bytes], ...] = ()

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
        if decision is SendDecision.PRIVATE:
            # Anything kept from before the contact was made private goes now.
            self._ports.style.forget(chat.title)
        if decision is not SendDecision.ALLOW:
            logger.info("start refused reason=%s", decision.value)
            return StartResult(StartStatus(decision.value))
        self._chat = ChatRef(window.hwnd, chat.title)
        self._bounds = chat.bounds
        self._lines = view.lines
        is_group = any(line.sender for line in view.lines)
        language = self._ports.style.learn(chat.title, view.lines, is_group)
        self._context = self._ports.style.context(chat.title, language, is_group)
        avatar, looks = self._look(window.hwnd, chat, view.lines)
        self._use_history(
            self._ports.history.observe(chat.title, view.lines, is_group, avatar, looks)
        )
        logger.info(
            "start ok style=%d knowledge=%d label=%d",
            bool(self._context.style),
            bool(self._context.knowledge),
            bool(self._context.label),
        )
        return StartResult(
            StartStatus.STARTED,
            chat,
            self._context,
            self._history,
            window.hwnd,
            len(self._pictures),
        )

    def _look(
        self, hwnd: int, chat: OpenChat, lines: Sequence[ChatLine]
    ) -> tuple[str, tuple[str, ...]]:
        """One capture, only when something needs it. Keeps the photos to send; returns the
        contact's photo hash and the hashes lined up with `lines`."""
        try:
            known_group = self._ports.known_group(chat.title)
        except Exception:  # noqa: BLE001 - unsure: treat it as a group
            known_group = True
        allowed = may_send_pictures(self._settings, chat, lines, known_group)
        pending = pending_pictures(lines) if allowed else ()
        hashed = hashable(lines, chat.messages)
        if chat.avatar is None and not pending and not hashed:
            return "", ()
        started = time.perf_counter()
        try:
            look = self._ports.look(
                hwnd,
                chat.avatar,
                [lines[i].rect for i in pending],
                [lines[i].rect for i in hashed],
                chat.messages,
            )
        except Exception as exc:  # noqa: BLE001 - no capture only means no photos
            logger.info("look failed type=%s", type(exc).__name__)
            return "", ()
        self._pictures = tuple(
            (i + 1, jpeg) for i, jpeg in zip(pending, look.pictures, strict=False) if jpeg
        )
        looks = [""] * len(lines)
        for i, value in zip(hashed, look.looks, strict=False):
            looks[i] = value
        logger.info(
            "start pictures found=%d sent=%d ms=%d",
            len(pending),
            len(self._pictures),
            round((time.perf_counter() - started) * 1000),
        )
        return look.avatar, tuple(looks) if hashed else ()

    def confirm_identity(self, contact_id: int | None) -> HistoryContext:
        """The user's answer to "same person?". The next request uses the result."""
        chat = self._open_chat()
        history = self._ports.history.confirm(chat.title, self._lines, contact_id)
        self._use_history(history)
        return history

    def drop_history(self) -> None:
        """The stored history was deleted: the open session stops sending it."""
        self._use_history(HistoryContext("off"))

    def _use_history(self, history: HistoryContext) -> None:
        """Memory and earlier messages go out only for a contact told apart from others."""
        self._history = history
        if history.state == "ready" and self._context is not None:
            self._context = replace(
                self._context, memory=history.memory, snippets=snippet_rows(history.snippets)
            )
        elif self._context is not None and (self._context.memory or self._context.snippets):
            self._context = replace(self._context, memory="", snippets=())

    def suggest(self, on_preview: PreviewCallback | None = None) -> SuggestionReply:
        """Send the chat as masked text, or capture it once when there is no text."""
        chat = self._open_chat()
        has_text = any(line.text.strip() for line in self._lines)
        # Photos and voice messages alone still go as text: cheaper, and the text is masked.
        has_media = any(line.kind in (*PICTURE_KINDS, "audio") for line in self._lines)
        if self._settings.input == "text" and (has_text or has_media):
            if self._masked is None:
                found: tuple[str, ...] = ()
                masked = self._lines
                if self._settings.mask_sensitive_data:
                    masked, found = mask_lines(self._lines)
                # Set only once masking worked: a later regenerate reuses it as it is.
                self._masked = masked
                logger.info("suggest input=text lines=%d masked=%d", len(self._lines), len(found))
            request = SuggestRequest(
                None,
                self._settings.suggestion_count,
                lines=self._masked,
                context=self._context,
                pictures=self._pictures,
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
        request = SuggestRequest(
            self._image, self._settings.suggestion_count, context=self._context
        )
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
                context=self._context,
                pictures=self._pictures,
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
            context=self._context,
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
            # What the app wrote must not come back as the user's own style.
            self._ports.style.remember_pasted(text)
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
        self._context = None
        self._history = None
        self._pictures = ()

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

    def _save_label_guess(self, reply: SuggestionReply) -> None:
        context = self._context
        if (
            context is not None
            and context.ask_label
            and not self._label_guessed
            and reply.contact_label in LABELS
        ):
            self._label_guessed = True
            self._ports.style.guess_label(self._chat.title, reply.contact_label)

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
        self._save_label_guess(reply)
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
