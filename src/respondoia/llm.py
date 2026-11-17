"""Model client. Sends the chat and parses the JSON reply."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import anthropic
import pydantic
import pydantic_core
from pydantic import BaseModel

from respondoia.context import SYSTEM_PROMPT, build_user_content, number_lines
from respondoia.domain import ChatLine


class ChatMessage(BaseModel):
    id: int
    author: str
    text: str
    pending: bool


class Suggestion(BaseModel):
    label: str
    text: str


class SuggestionReply(BaseModel):
    # Field order is the order the model writes them in. Summary and suggestions come
    # first so the panel can show them while the message list is still streaming.
    summary: str
    suggestions: list[Suggestion]
    note: str | None = None
    contact: str
    messages: list[ChatMessage]


class TextReply(BaseModel):
    """What the model writes when the chat was sent as text: the list is already known."""

    summary: str
    suggestions: list[Suggestion]
    note: str | None = None


class SuggestionPreview(BaseModel):
    """The part of a reply the panel needs first."""

    summary: str
    suggestions: list[Suggestion]


@dataclass(frozen=True)
class SuggestRequest:
    image_png: bytes | None
    count: int
    previous: SuggestionReply | None = None
    selected_ids: tuple[int, ...] = ()
    instruction: str = ""
    lines: tuple[ChatLine, ...] = ()  # text input; when set, image_png is None


@dataclass(frozen=True)
class SuggestResult:
    reply: SuggestionReply
    model: str  # as reported by the API
    input_tokens: int
    output_tokens: int
    latency_ms: int
    preview_ms: int | None = None  # until summary and suggestions were complete


class LlmError(Exception):
    """code: auth, rate_limit, network, server, refused, truncated or invalid_reply."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


PreviewCallback = Callable[[SuggestionPreview], None]


class ModelClient(Protocol):
    def suggest(
        self, request: SuggestRequest, on_preview: PreviewCallback | None = None
    ) -> SuggestResult: ...


def build_params(request: SuggestRequest, model: str) -> dict:
    listed = None
    if request.previous is not None and not request.lines:
        listed = [m.model_dump() for m in request.previous.messages]
    content = build_user_content(
        request.image_png,
        request.count,
        listed,
        request.selected_ids,
        request.instruction,
        request.lines,
    )
    # No sampling keys: current models reject non-default values for them.
    params = {
        "model": model,
        "max_tokens": 8000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": content}],
        "output_config": {"effort": "low"},
        "output_format": TextReply if request.lines else SuggestionReply,
    }
    if model.startswith("claude-haiku"):
        # Haiku thinks before writing even at low effort, which can take over 10 s.
        # Sonnet 5.5 rejects "disabled", so the key is only sent to Haiku.
        params["thinking"] = {"type": "disabled"}
    return params


def check_reply(reply: SuggestionReply, count: int) -> SuggestionReply:
    ids = [m.id for m in reply.messages]
    if (
        len(reply.suggestions) != count
        or any(not s.text.strip() for s in reply.suggestions)
        or len(set(ids)) != len(ids)
    ):
        raise LlmError("invalid_reply")
    return reply


def read_preview(partial_json: str, count: int) -> SuggestionPreview | None:
    """Summary and suggestions from the start of a streamed reply, once all are complete."""
    try:
        # Unfinished strings are left out, so a text that is present is a finished text.
        data = pydantic_core.from_json(partial_json, allow_partial=True)
        preview = SuggestionPreview.model_validate(data)
    except ValueError:  # not JSON yet, or summary or suggestions still missing
        return None
    suggestions = preview.suggestions
    if len(suggestions) != count or any(not s.text.strip() for s in suggestions):
        return None
    return preview


def _with_local_messages(short: TextReply, lines: tuple[ChatLine, ...]) -> SuggestionReply:
    messages = [
        ChatMessage(
            id=row["id"],
            author=row["author"] if row["author"] == "user" else row["sender"] or "contact",
            text=row["text"],
            pending=row["pending"],
        )
        for row in number_lines(lines)
    ]
    return SuggestionReply(
        summary=short.summary,
        suggestions=short.suggestions,
        note=short.note,
        contact="",
        messages=messages,
    )


class AnthropicClient:
    def __init__(self, api_key: str, model: str, timeout_s: float = 20.0) -> None:
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=1)
        self._model = model

    def suggest(
        self, request: SuggestRequest, on_preview: PreviewCallback | None = None
    ) -> SuggestResult:
        params = build_params(request, self._model)
        # The schema goes in output_config and the reply is validated here, not by
        # messages.parse: that helper parses the text before the stop reason can be read.
        schema = anthropic.transform_schema(params.pop("output_format"))
        params["output_config"] = {
            **params["output_config"],
            "format": {"type": "json_schema", "schema": schema},
        }
        started = time.perf_counter()
        preview_ms = None
        try:
            with self._client.messages.stream(**params) as stream:
                text = ""
                for delta in stream.text_stream:
                    text += delta
                    if preview_ms is None:
                        preview = read_preview(text, request.count)
                        if preview is not None:
                            preview_ms = round((time.perf_counter() - started) * 1000)
                            if on_preview is not None:
                                on_preview(preview)
                response = stream.get_final_message()
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise LlmError("auth") from exc
        except anthropic.RateLimitError as exc:
            raise LlmError("rate_limit") from exc
        except anthropic.APIConnectionError as exc:  # includes timeouts
            raise LlmError("network") from exc
        except anthropic.APIStatusError as exc:
            raise LlmError("server") from exc
        except anthropic.APIError as exc:  # anything else the SDK raises
            raise LlmError("server") from exc
        latency_ms = round((time.perf_counter() - started) * 1000)
        if response.stop_reason == "refusal":
            raise LlmError("refused")
        if response.stop_reason == "max_tokens":
            raise LlmError("truncated")
        text = next((block.text for block in response.content if block.type == "text"), None)
        try:
            if request.lines:
                reply = _with_local_messages(
                    TextReply.model_validate_json(text or ""), request.lines
                )
            else:
                reply = SuggestionReply.model_validate_json(text or "")
        except pydantic.ValidationError as exc:
            raise LlmError("invalid_reply") from exc
        return SuggestResult(
            reply=reply,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency_ms,
            preview_ms=preview_ms,
        )
