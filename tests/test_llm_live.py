from pathlib import Path

import pytest

from respondoia.config import Settings, get_api_key
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
