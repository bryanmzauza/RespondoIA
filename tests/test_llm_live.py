from pathlib import Path

import pytest

from respondoia.config import Settings, get_api_key
from respondoia.domain import ChatLine, UserContext
from respondoia.llm import AnthropicClient, SuggestRequest, check_reply

SHOT = Path(__file__).parent / "screenshots" / "pt-BR" / "01-short.png"


@pytest.mark.live
def test_real_model_returns_three_suggestions():
    key = get_api_key()
    assert key, "store a key first: uv run python -m respondoia --set-api-key"
    client = AnthropicClient(key, Settings().model)
    result = client.suggest(SuggestRequest(SHOT.read_bytes(), 3))
    reply = check_reply(result.reply, 3)
    assert all(s.text.strip() for s in reply.suggestions)
    assert result.latency_ms > 0


@pytest.mark.live
def test_live_text_request_with_context():
    key = get_api_key()
    assert key, "store a key first: uv run python -m respondoia --set-api-key"
    lines = (
        ChatLine("contact", "", "09:10", "text", "Oi! Quanto custa o pão de fermentação natural?", ""),
    )
    context = UserContext(
        "pt",
        "client",
        True,
        False,
        "Starts with a lowercase letter in 90% of messages. Ends with a full stop in 0%.",
        "- Pão de fermentação natural: R$ 18",
    )
    client = AnthropicClient(key, Settings().model)
    reply = check_reply(client.suggest(SuggestRequest(None, 3, lines=lines, context=context)).reply, 3)
    assert any("18" in s.text for s in reply.suggestions)
