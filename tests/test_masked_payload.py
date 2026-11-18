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


def test_transcript_and_preview_are_masked():
    from respondoia.domain import ChatLine

    chat = (
        ChatLine("contact", "", "09:00", "audio", "meu cpf é 529.982.247-25", "", transcript=True),
        ChatLine("contact", "", "09:01", "link", "Pague 529.982.247-25 pix.example", "", site="pix.example"),
    )
    lines, found = mask_lines(chat)
    params = build_params(SuggestRequest(None, 3, lines=lines), "claude-haiku-5-5")
    payload = json.dumps(params, default=str, ensure_ascii=False)
    assert "529.982.247-25" not in payload and "[CPF]" in payload
    assert found.count("CPF") == 2
    assert '"transcript": true' in params["messages"][0]["content"][-1]["text"]
