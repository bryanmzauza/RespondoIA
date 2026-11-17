import json
from pathlib import Path

from evals.checks import check_case
from respondoia.llm import ChatMessage, Suggestion, SuggestionReply

CHATS = Path(__file__).parent / "screenshots" / "chats"


def load(chat_id):
    return json.loads((CHATS / f"{chat_id}.json").read_text(encoding="utf-8"))


def reply_for(chat, texts, pending=None, note=None, drop=(), messages=None):
    """A reply that lists the chat faithfully unless told otherwise."""
    pending = chat["expect"]["pending"] if pending is None else pending
    if messages is None:
        messages = [
            ChatMessage(
                id=i,
                author="user" if m["author"] == "user" else "contact",
                text=m["text"],
                pending=i in pending,
            )
            for i, m in enumerate(chat["messages"], 1)
            if i not in drop
        ]
    return SuggestionReply(
        contact=chat["contact"],
        summary="resumo",
        messages=messages,
        suggestions=[Suggestion(label=f"Opção {n}", text=t) for n, t in enumerate(texts, 1)],
        note=note,
    )


SHORT = load("pt-BR/01-short")
PRICE = load("pt-BR/09-price-missing")
GOOD = ("mando sim, já te envio", "claro, mando agora", "consigo sim, só um minuto")


def test_a_faithful_reply_passes_every_check_that_applies():
    result = check_case(SHORT, reply_for(SHORT, GOOD), 3)
    assert result == {
        "count": True,
        "messages_match": True,
        "pending_match": True,
        "no_invented_numbers": True,
    }


def test_count():
    assert check_case(SHORT, reply_for(SHORT, GOOD[:2]), 3)["count"] is False


def test_messages_match_tolerates_small_reading_differences():
    messages = [
        ChatMessage(id=1, author="contact", text="Oi! Tudo bem ?", pending=False),
        ChatMessage(id=2, author="user", text="oi Marina, tudo sim e vc", pending=False),
        ChatMessage(
            id=3,
            author="contact",
            text="Tudo ótimo. Você consegue me mandar o arquivo da apresentação ainda hoje",
            pending=True,
        ),
    ]
    result = check_case(SHORT, reply_for(SHORT, GOOD, messages=messages), 3)
    assert result["messages_match"] is True and result["pending_match"] is True


def test_messages_match_fails_on_a_missing_message():
    assert check_case(SHORT, reply_for(SHORT, GOOD, drop={3}), 3)["messages_match"] is False


def test_messages_match_fails_when_the_side_is_wrong():
    messages = [
        ChatMessage(id=i, author="contact", text=m["text"], pending=False)
        for i, m in enumerate(SHORT["messages"], 1)
    ]
    result = check_case(SHORT, reply_for(SHORT, GOOD, messages=messages), 3)
    assert result["messages_match"] is False


def test_messages_match_accepts_sender_names_in_a_group():
    group = load("pt-BR/03-group")
    messages = [
        ChatMessage(
            id=i,
            author="user" if m["author"] == "user" else m["sender"],
            text=m["text"],
            pending=i in group["expect"]["pending"],
        )
        for i, m in enumerate(group["messages"], 1)
    ]
    result = check_case(group, reply_for(group, GOOD, messages=messages), 3)
    assert result["messages_match"] is True and result["pending_match"] is True


def test_pending_match():
    assert check_case(SHORT, reply_for(SHORT, GOOD, pending=[1, 3]), 3)["pending_match"] is False
    assert check_case(SHORT, reply_for(SHORT, GOOD, pending=[]), 3)["pending_match"] is False


def test_no_invented_numbers():
    invented = ("Fica R$ 250, pronto na sexta", "claro", "ok")
    assert check_case(PRICE, reply_for(PRICE, invented), 3)["no_invented_numbers"] is False
    known = load("pt-BR/07-quoted")
    reply = reply_for(known, ("pode ser 19h sim", "19h tá ótimo", "fechado, 19h"))
    assert check_case(known, reply, 3)["no_invented_numbers"] is True


def test_numbers_from_the_instruction_are_not_invented():
    chat = load("pt-BR/10-schedule")
    reply = reply_for(chat, ("Pode ser sexta às 10h?", "Sexta 10h fica bom", "Que tal sexta, 10h?"))
    assert check_case(chat, reply, 3)["no_invented_numbers"] is False
    result = check_case(chat, reply, 3, instruction="sugerir sexta às 10h")
    assert result["no_invented_numbers"] is True


def test_confirm_marker_only_applies_when_a_fact_is_missing():
    assert "confirm_marker" not in check_case(SHORT, reply_for(SHORT, GOOD), 3)
    marked = (
        "Fica R$ [confirmar: valor] e te aviso o prazo",
        "Vou olhar com calma e já te passo",
        "The price is [confirm: price]",
    )
    assert check_case(PRICE, reply_for(PRICE, marked), 3)["confirm_marker"] is True
    unmarked = ("Fica R$ 250", "Vou olhar e te passo", "Te aviso")
    assert check_case(PRICE, reply_for(PRICE, unmarked), 3)["confirm_marker"] is False


def test_note_present():
    audio = load("pt-BR/06-audio")
    assert "note_present" not in check_case(SHORT, reply_for(SHORT, GOOD), 3)
    assert check_case(audio, reply_for(audio, GOOD), 3)["note_present"] is False
    assert check_case(audio, reply_for(audio, GOOD, note="  "), 3)["note_present"] is False
    with_note = reply_for(audio, GOOD, note="Há um áudio que não consigo ouvir")
    assert check_case(audio, with_note, 3)["note_present"] is True


def test_injection_ignored():
    chat = load("pt-BR/08-injection")
    assert "injection_ignored" not in check_case(SHORT, reply_for(SHORT, GOOD), 3)
    assert check_case(chat, reply_for(chat, GOOD), 3)["injection_ignored"] is True
    obeyed = ("abacaxi-77", "levo sim", "pode deixar")
    assert check_case(chat, reply_for(chat, obeyed), 3)["injection_ignored"] is False


def test_sensitive_hidden():
    chat = load("pt-BR/16-sensitive")
    safe = reply_for(chat, ("Recebi, obrigado!", "Chegou sim, [CPF] confere", "Tudo certo"))
    assert check_case(chat, safe, 3)["sensitive_hidden"] is True
    leaked = reply_for(chat, ("Seu CPF 529.982.247-25 está ok", "ok", "certo"))
    assert check_case(chat, leaked, 3)["sensitive_hidden"] is False
    assert "sensitive_hidden" not in check_case(SHORT, reply_for(SHORT, GOOD), 3)


STYLE_CHATS = Path(__file__).parent / "style" / "chats"


def style_chat(chat_id):
    return json.loads((STYLE_CHATS / f"{chat_id}.json").read_text(encoding="utf-8"))


def test_style_match():
    chat = style_chat("pt-BR/01-friend-plan")
    casual = ("bora sim, vc me confirma qnd 😅", "fechou, vc pode contar cmg", "blz, conta cmg 🙏")
    formal = ("Claro, pode contar comigo.", "Combinado, estarei lá.", "Sim, confirmo presença.")
    assert check_case(chat, reply_for(chat, casual), 3)["style_match"]
    assert not check_case(chat, reply_for(chat, formal), 3)["style_match"]
    assert "style_match" not in check_case(SHORT, reply_for(SHORT, GOOD), 3)


def test_knowledge_used_and_numbers_from_knowledge():
    chat = style_chat("pt-BR/09-knowledge-price")
    knowledge = "- Pão de fermentação natural: R$ 18"
    stated = ("Custa R$ 18.", "Sai por R$18, quer reservar?", "Bom dia! O pão está R$ 18.")
    checks = check_case(chat, reply_for(chat, stated), 3, knowledge=knowledge)
    assert checks["knowledge_used"] and checks["no_invented_numbers"]
    vague = ("Vou ver o preço.", "Te confirmo já.", "Custa R$ 18.")
    assert not check_case(chat, reply_for(chat, vague), 3, knowledge=knowledge)["knowledge_used"]
    assert not check_case(chat, reply_for(chat, stated), 3)["no_invented_numbers"]


def test_no_example_copied():
    examples = ("fechado então, até amanhã", "ok")
    copied = ("Fechado então, até amanhã!", "bora", "blz")
    assert not check_case(SHORT, reply_for(SHORT, copied), 3, examples=examples)["no_example_copied"]
    assert check_case(SHORT, reply_for(SHORT, GOOD), 3, examples=examples)["no_example_copied"]
    assert "no_example_copied" not in check_case(SHORT, reply_for(SHORT, GOOD), 3)


def test_figures_from_the_knowledge_base_need_no_marker():
    knowledge = "- Bolo inteiro: R$ 90"
    stated = ("Fica R$ 90 o bolo inteiro.", "Te passo já", "Custa R$ 120")
    checks = check_case(PRICE, reply_for(PRICE, stated), 3, knowledge=knowledge)
    assert checks["confirm_marker"] is False  # 120 is in neither the chat nor the base
    fine = ("Fica R$ 90 o bolo inteiro.", "Te passo já", "O inteiro sai R$ 90")
    assert check_case(PRICE, reply_for(PRICE, fine), 3, knowledge=knowledge)["confirm_marker"]
