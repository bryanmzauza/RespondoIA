import json

import pytest

from evals.run import chat_lines, load_chats
from respondoia.llm import SuggestRequest, build_params
from respondoia.privacy import mask_lines


@pytest.mark.parametrize(
    "lang,kinds",
    [("pt-BR", {"CPF", "CARD", "PIX", "EMAIL"}), ("en", {"CARD", "IBAN", "EMAIL", "PHONE"})],
)
def test_sensitive_fixture_leaves_masked(lang, kinds):
    chat = next(c for c in load_chats() if c["id"] == f"{lang}/16-sensitive")
    lines, found = mask_lines(chat_lines(chat))
    params = build_params(SuggestRequest(None, 3, lines=lines), "claude-haiku-5-5")
    payload = json.dumps(params, default=str, ensure_ascii=False)
    for value in chat["expect"]["sensitive"]:
        assert value not in payload and value.replace(" ", "") not in payload, value
    assert kinds <= set(found)
