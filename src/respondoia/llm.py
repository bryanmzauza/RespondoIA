"""Model client. Sends the chat and parses the JSON reply."""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Protocol

import anthropic
import pydantic
import pydantic_core
from pydantic import BaseModel, field_validator

from respondoia.context import SYSTEM_PROMPT, build_user_content, number_lines
from respondoia.domain import LABELS, ChatLine, UserContext


class ChatMessage(BaseModel):
    id: int
    author: str
    text: str
    pending: bool


class Suggestion(BaseModel):
    label: str
    text: str


def _known_label(value: str | None) -> str | None:
    return value if value in LABELS else None


class SuggestionReply(BaseModel):
    # Field order is the order the model writes them in. Summary and suggestions come
    # first so the panel can show them while the message list is still streaming.
    summary: str
    suggestions: list[Suggestion]
    note: str | None = None
    contact: str
    messages: list[ChatMessage]
    contact_label: str | None = None  # only when the request asked for a guess

    _label = field_validator("contact_label")(_known_label)


class TextReply(BaseModel):
    """What the model writes when the chat was sent as text: the list is already known."""

    summary: str
    suggestions: list[Suggestion]
    note: str | None = None
    contact_label: str | None = None

    _label = field_validator("contact_label")(_known_label)


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
    context: UserContext | None = None  # style, knowledge base and contact label
    pictures: tuple[tuple[int, bytes], ...] = ()  # (message id, JPEG), text input only


@dataclass(frozen=True)
class SuggestResult:
    reply: SuggestionReply
    model: str  # as reported by the API
    input_tokens: int
    output_tokens: int
    latency_ms: int
    preview_ms: int | None = None  # until summary and suggestions were complete


@dataclass(frozen=True)
class ExtractResult[T: BaseModel]:
    value: T
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


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

    def extract[T: BaseModel](
        self, system: str, text: str, schema: type[T]
    ) -> ExtractResult[T]: ...


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
        request.context,
        request.pictures,
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
        contact_label=short.contact_label,
    )


@contextmanager
def _sdk_errors() -> Iterator[None]:
    """Map what the SDK raises to the codes the app knows."""
    try:
        yield
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
        raise LlmError("auth") from exc
    except anthropic.RateLimitError as exc:
        raise LlmError("rate_limit") from exc
    except anthropic.APIConnectionError as exc:  # includes timeouts
        raise LlmError("network") from exc
    except anthropic.APIError as exc:  # status errors and anything else the SDK raises
        raise LlmError("server") from exc


def _check_stop(stop_reason: str | None) -> None:
    if stop_reason == "refusal":
        raise LlmError("refused")
    if stop_reason == "max_tokens":
        raise LlmError("truncated")


def _schema_format(schema: type[BaseModel]) -> dict:
    return {"type": "json_schema", "schema": anthropic.transform_schema(schema)}


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
        params["output_config"] = {
            **params["output_config"],
            "format": _schema_format(params.pop("output_format")),
        }
        started = time.perf_counter()
        preview_ms = None
        with _sdk_errors(), self._client.messages.stream(**params) as stream:
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
        latency_ms = round((time.perf_counter() - started) * 1000)
        _check_stop(response.stop_reason)
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

    def extract[T: BaseModel](
        self, system: str, text: str, schema: type[T]
    ) -> ExtractResult[T]:
        """One structured answer, not streamed: background work nobody watches."""
        params = {
            "model": self._model,
            "max_tokens": 4000,
            "system": system,
            "messages": [{"role": "user", "content": text}],
            "output_config": {"effort": "low", "format": _schema_format(schema)},
        }
        if self._model.startswith("claude-haiku"):
            params["thinking"] = {"type": "disabled"}
        started = time.perf_counter()
        with _sdk_errors():
            response = self._client.messages.create(**params, timeout=60.0)
        latency_ms = round((time.perf_counter() - started) * 1000)
        _check_stop(response.stop_reason)
        answer = next((block.text for block in response.content if block.type == "text"), None)
        try:
            value = schema.model_validate_json(answer or "")
        except pydantic.ValidationError as exc:
            raise LlmError("invalid_reply") from exc
        return ExtractResult(
            value=value,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency_ms,
        )
