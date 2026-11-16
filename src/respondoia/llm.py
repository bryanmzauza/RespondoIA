"""Model client. Sends the chat and parses the JSON reply."""

import time
from dataclasses import dataclass
from typing import Protocol

import anthropic
import pydantic
from pydantic import BaseModel

from respondoia.context import SYSTEM_PROMPT, build_user_content


class ChatMessage(BaseModel):
    id: int
    author: str
    text: str
    pending: bool


class Suggestion(BaseModel):
    label: str
    text: str


class SuggestionReply(BaseModel):
    contact: str
    summary: str
    messages: list[ChatMessage]
    suggestions: list[Suggestion]
    note: str | None = None


@dataclass(frozen=True)
class SuggestRequest:
    image_png: bytes
    count: int
    previous: SuggestionReply | None = None
    selected_ids: tuple[int, ...] = ()
    instruction: str = ""


@dataclass(frozen=True)
class SuggestResult:
    reply: SuggestionReply
    model: str  # as reported by the API
    input_tokens: int
    output_tokens: int
    latency_ms: int


class LlmError(Exception):
    """code: auth, rate_limit, network, server, refused, truncated or invalid_reply."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ModelClient(Protocol):
    def suggest(self, request: SuggestRequest) -> SuggestResult: ...


def build_params(request: SuggestRequest, model: str) -> dict:
    listed = None
    if request.previous is not None:
        listed = [m.model_dump() for m in request.previous.messages]
    content = build_user_content(
        request.image_png, request.count, listed, request.selected_ids, request.instruction
    )
    # No thinking or sampling keys: current models reject non-default values for them.
    return {
        "model": model,
        "max_tokens": 8000,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": content}],
        "output_config": {"effort": "low"},
        "output_format": SuggestionReply,
    }


def check_reply(reply: SuggestionReply, count: int) -> SuggestionReply:
    ids = [m.id for m in reply.messages]
    if (
        len(reply.suggestions) != count
        or any(not s.text.strip() for s in reply.suggestions)
        or len(set(ids)) != len(ids)
    ):
        raise LlmError("invalid_reply")
    return reply


class AnthropicClient:
    def __init__(self, api_key: str, model: str, timeout_s: float = 20.0) -> None:
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s, max_retries=1)
        self._model = model

    def suggest(self, request: SuggestRequest) -> SuggestResult:
        params = build_params(request, self._model)
        # The schema goes in output_config and the reply is validated here, not by
        # messages.parse: that helper parses the text before the stop reason can be read.
        schema = anthropic.transform_schema(params.pop("output_format"))
        params["output_config"] = {
            **params["output_config"],
            "format": {"type": "json_schema", "schema": schema},
        }
        started = time.perf_counter()
        try:
            response = self._client.messages.create(**params)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise LlmError("auth") from exc
        except anthropic.RateLimitError as exc:
            raise LlmError("rate_limit") from exc
        except anthropic.APIConnectionError as exc:  # includes timeouts
            raise LlmError("network") from exc
        except anthropic.APIStatusError as exc:
            raise LlmError("server") from exc
        latency_ms = round((time.perf_counter() - started) * 1000)
        if response.stop_reason == "refusal":
            raise LlmError("refused")
        if response.stop_reason == "max_tokens":
            raise LlmError("truncated")
        text = next((block.text for block in response.content if block.type == "text"), None)
        try:
            reply = SuggestionReply.model_validate_json(text or "")
        except pydantic.ValidationError as exc:
            raise LlmError("invalid_reply") from exc
        return SuggestResult(
            reply=reply,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency_ms,
        )
