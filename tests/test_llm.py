from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from respondoia.context import SYSTEM_PROMPT
from respondoia.llm import (
    AnthropicClient,
    ChatMessage,
    LlmError,
    Suggestion,
    SuggestionReply,
    SuggestRequest,
    build_params,
    check_reply,
    read_preview,
)


def make_reply(texts=("Oi!", "Olá, tudo bem?", "Oi, já te respondo"), ids=(1, 2)):
    return SuggestionReply(
        contact="Marina Duarte",
        summary="Quer saber o prazo",
        messages=[
            ChatMessage(id=i, author="contact", text=f"mensagem {i}", pending=True) for i in ids
        ],
        suggestions=[Suggestion(label=f"Opção {n}", text=t) for n, t in enumerate(texts, 1)],
    )


def test_build_params_has_no_sampling_or_thinking_keys():
    params = build_params(SuggestRequest(b"png", 3), "claude-haiku-5-5")
    assert set(params) == {
        "model",
        "max_tokens",
        "system",
        "messages",
        "output_config",
        "output_format",
    }
    assert params["model"] == "claude-haiku-5-5" and params["max_tokens"] == 8000
    assert params["system"] == SYSTEM_PROMPT
    assert params["output_config"] == {"effort": "low"}
    assert params["output_format"] is SuggestionReply
    assert [m["role"] for m in params["messages"]] == ["user"]


def test_build_params_sends_the_previous_messages_on_regenerate():
    request = SuggestRequest(b"png", 3, previous=make_reply(), selected_ids=(2,), instruction="x")
    text = build_params(request, "claude-haiku-5-5")["messages"][0]["content"][1]["text"]
    assert '"text": "mensagem 2"' in text
    assert "Selected messages: [2]" in text and "User instruction: x" in text


def test_check_reply_rejects_wrong_count():
    with pytest.raises(LlmError) as err:
        check_reply(make_reply(texts=("a", "b")), 3)
    assert err.value.code == "invalid_reply"


def test_check_reply_rejects_empty_text():
    with pytest.raises(LlmError) as err:
        check_reply(make_reply(texts=("a", "   ", "c")), 3)
    assert err.value.code == "invalid_reply"


def test_check_reply_rejects_duplicate_ids():
    with pytest.raises(LlmError) as err:
        check_reply(make_reply(ids=(1, 1)), 3)
    assert err.value.code == "invalid_reply"


def test_check_reply_accepts_a_valid_reply():
    reply = make_reply()
    assert check_reply(reply, 3) is reply


def fake_response(stop_reason="end_turn", text=None):
    # A thinking block comes first, as it does with adaptive thinking on.
    content = [SimpleNamespace(type="thinking", thinking="")]
    if text is not None:
        content.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=content,
        model="claude-haiku-5-5",
        usage=SimpleNamespace(input_tokens=2100, output_tokens=640),
    )


class FakeStream:
    """Stands in for the SDK's MessageStream: text deltas, then the final message."""

    def __init__(self, response, chunk=7, error_after=None):
        text = next((b.text for b in response.content if b.type == "text"), "")
        self.chunks = [text[i : i + chunk] for i in range(0, len(text), chunk)]
        self.response = response
        self.error_after = error_after
        self.handed = 0  # chunks given out so far
        self.text_stream = self._deltas()

    def _deltas(self):
        for index, piece in enumerate(self.chunks):
            if self.error_after is not None and index == self.error_after:
                raise self.error_after_exc
            self.handed = index + 1
            yield piece

    def get_final_message(self):
        return self.response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def status_error(cls, status):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("failed", response=httpx2.Response(status, request=request), body=None)


def client_returning(monkeypatch, outcome, seen=None):
    client = AnthropicClient("sk-test-not-a-real-key", "claude-haiku-5-5")

    def stream(**params):
        if seen is not None:
            seen.update(params)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome if isinstance(outcome, FakeStream) else FakeStream(outcome)

    monkeypatch.setattr(client._client.messages, "stream", stream)
    return client


def test_suggest_returns_reply_usage_and_latency(monkeypatch):
    reply = make_reply()
    client = client_returning(monkeypatch, fake_response(text=reply.model_dump_json()))
    result = client.suggest(SuggestRequest(b"png", 3))
    assert result.reply == reply
    assert (result.model, result.input_tokens, result.output_tokens) == (
        "claude-haiku-5-5",
        2100,
        640,
    )
    assert 0 <= result.preview_ms <= result.latency_ms


def test_suggest_asks_for_the_reply_schema_with_low_effort(monkeypatch):
    seen = {}
    reply = make_reply()
    client = client_returning(monkeypatch, fake_response(text=reply.model_dump_json()), seen)
    client.suggest(SuggestRequest(b"png", 3))
    assert seen["output_config"]["effort"] == "low"
    assert seen["output_config"]["format"]["type"] == "json_schema"
    assert "suggestions" in seen["output_config"]["format"]["schema"]["properties"]
    assert not {"output_format", "thinking", "temperature", "top_p", "top_k"} & set(seen)


def test_suggestions_come_before_the_message_list():
    order = list(SuggestionReply.model_json_schema()["properties"])
    assert order.index("summary") < order.index("suggestions") < order.index("messages")


def test_preview_arrives_once_before_the_message_list(monkeypatch):
    reply = make_reply()
    stream = FakeStream(fake_response(text=reply.model_dump_json()))
    client = client_returning(monkeypatch, stream)
    previews = []

    def on_preview(preview):
        consumed = sum(len(c) for c in stream.chunks[: stream.handed])
        previews.append((preview, consumed))

    result = client.suggest(SuggestRequest(b"png", 3), on_preview)
    assert len(previews) == 1
    preview, consumed = previews[0]
    assert preview.summary == reply.summary and preview.suggestions == reply.suggestions
    full = reply.model_dump_json()
    assert consumed < full.index('"messages"')  # shown before the list was written
    assert result.reply == reply


def test_read_preview_needs_every_suggestion_complete():
    full = make_reply().model_dump_json()
    cut = full[: full.index("Olá, tudo bem?") + 3]  # the second text is still being written
    assert read_preview(cut, 3) is None
    until_third = full[: full.index('"messages"')]
    preview = read_preview(until_third, 3)
    assert [s.text for s in preview.suggestions] == ["Oi!", "Olá, tudo bem?", "Oi, já te respondo"]
    assert read_preview(until_third, 2) is None  # a third one appeared: count is wrong
    assert read_preview("not json", 3) is None and read_preview("", 3) is None


def test_read_preview_rejects_an_empty_suggestion():
    reply = make_reply(texts=("a", "   ", "c"))
    text = reply.model_dump_json()
    assert read_preview(text[: text.index('"messages"')], 3) is None


def test_refusal_after_the_preview_is_still_refused(monkeypatch):
    text = make_reply().model_dump_json()
    client = client_returning(monkeypatch, fake_response(stop_reason="refusal", text=text))
    previews = []
    with pytest.raises(LlmError) as err:
        client.suggest(SuggestRequest(b"png", 3), previews.append)
    assert err.value.code == "refused" and len(previews) == 1


def test_network_error_in_the_middle_of_the_stream(monkeypatch):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    stream = FakeStream(fake_response(text=make_reply().model_dump_json()), error_after=3)
    stream.error_after_exc = anthropic.APIConnectionError(request=request)
    client = client_returning(monkeypatch, stream)
    with pytest.raises(LlmError) as err:
        client.suggest(SuggestRequest(b"png", 3))
    assert err.value.code == "network"


def test_stop_reason_and_errors_map_to_codes(monkeypatch):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    cut = make_reply().model_dump_json()[:40]
    cases = [
        (fake_response(stop_reason="refusal"), "refused"),
        (fake_response(stop_reason="refusal", text="I cannot"), "refused"),
        (fake_response(stop_reason="max_tokens", text=cut), "truncated"),
        (fake_response(text=None), "invalid_reply"),
        (fake_response(text="not json"), "invalid_reply"),
        (anthropic.APIConnectionError(request=request), "network"),
        (anthropic.APITimeoutError(request=request), "network"),
        (status_error(anthropic.AuthenticationError, 401), "auth"),
        (status_error(anthropic.PermissionDeniedError, 403), "auth"),
        (status_error(anthropic.RateLimitError, 429), "rate_limit"),
        (status_error(anthropic.InternalServerError, 500), "server"),
    ]
    for outcome, code in cases:
        client = client_returning(monkeypatch, outcome)
        with pytest.raises(LlmError) as err:
            client.suggest(SuggestRequest(b"png", 3))
        assert err.value.code == code
