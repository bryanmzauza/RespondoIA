from respondoia.domain import ChatLine
from respondoia.style.parser import ExportChat, ExportMessage
from respondoia.style.sampler import (
    OwnMessage,
    from_export,
    from_screen,
    is_learnable,
    pick_examples,
    sample,
    stats,
    text_hash,
)


def user(text, time="09:01"):
    return ChatLine("user", "", time, "text", text, "")


def contact(text, sender=""):
    return ChatLine("contact", sender, "09:00", "text", text, "")


def as_messages(texts, chat_key="c", runs=None):
    runs = runs or list(range(len(texts)))
    return [OwnMessage(t, "pt", chat_key, "", "", r, None) for t, r in zip(texts, runs)]


def test_own_lines_never_keep_contact_text_or_quotes():
    lines = (
        ChatLine("contact", "ZZ-SENDER", "09:00", "text", "ZZ-CONTACT-TEXT vamos", ""),
        ChatLine("user", "", "09:01", "text", "beleza, vou sim", "ZZ-QUOTED"),
        ChatLine("user", "", "09:02", "image", "ZZ-NOT-TEXT-KIND", ""),
    )
    learned = from_screen(lines, "contact:1", "pt", set())
    assert "ZZ-" not in repr(learned)
    assert [m.text for m in learned] == ["beleza, vou sim"]


def test_pasted_text_is_skipped():
    line = user("Consigo sim, sexta de manhã")
    assert (
        from_screen((line,), "contact:1", "pt", {text_hash("consigo sim,  sexta de manhã")}) == ()
    )


def test_messages_are_masked():
    line = user("meu cpf é 529.982.247-25 blz")
    assert from_screen((line,), "contact:1", "pt", set())[0].text == "meu cpf é [CPF] blz"


def test_language_comes_from_the_message_or_the_chat():
    lines = (user("are you coming over tonight? I am at home"), user("ok blz"))
    learned = from_screen(lines, "contact:1", "pt", set())
    assert [m.language for m in learned] == ["en", "pt"]
    assert from_screen((user("ok blz"),), "contact:1", None, set()) == ()


def test_runs_follow_the_turns():
    lines = (user("oi"), user("tudo bem?"), contact("tudo"), user("que bom"))
    assert [m.run for m in from_screen(lines, "contact:1", "pt", set())] == [0, 0, 1]


def test_from_export_keeps_only_the_chosen_author():
    chat = ExportChat(
        (
            ExportMessage("2026-11-16", "09:00", "Marina", "ZZ-OTHER oi tudo bem com vc?", "text"),
            ExportMessage("2026-11-16", "09:01", "Rafa", "tudo sim e vc? to aqui em casa", "text"),
            ExportMessage("2026-11-16", "09:02", "Rafa", "<Mídia oculta>", "media"),
            ExportMessage("2026-11-16", "09:03", "Marina", "ZZ-OTHER que bom", "text"),
            ExportMessage("2026-11-16", "09:04", "Rafa", "bora", "text"),
        ),
        (("Rafa", 3), ("Marina", 2)),
    )
    learned = from_export(chat, "Rafa", "friend", set())
    assert "ZZ-OTHER" not in repr(learned) and "Marina" not in repr(learned)
    assert [(m.text, m.run, m.label, m.sent_on) for m in learned] == [
        ("tudo sim e vc? to aqui em casa", 0, "friend", "2026-11-16"),
        ("bora", 1, "friend", "2026-11-16"),
    ]
    assert learned[0].chat_key.startswith("import:") and all(m.language == "pt" for m in learned)


def test_media_links_and_deleted_are_not_learned():
    for text in ("https://exemplo.com/x", "  ", "👍👍", "www.exemplo.com", "x" * 1001):
        assert not is_learnable(text)
    assert is_learnable("olha isso https://exemplo.com")


def test_sample_spreads_across_chats():
    messages = (
        as_messages(["a"] * 500, "one")
        + as_messages(["b"] * 20, "two")
        + as_messages(["c"] * 20, "three")
    )
    picked = sample(messages, limit=60)
    assert len(picked) == 60
    assert {k: sum(m.chat_key == k for m in picked) for k in ("one", "two", "three")} == {
        "one": 20,
        "two": 20,
        "three": 20,
    }


def test_sample_spreads_in_time_inside_a_chat():
    messages = as_messages([str(i) for i in range(100)], "one")
    assert [m.text for m in sample(messages, limit=4)] == ["0", "25", "50", "75"]


def test_stats_of_a_known_set():
    texts = [
        "oi tudo bem?",
        "vc vem amanhã",
        "Bom dia.",
        "kkkk boa 😅",
        "blz",
        "tmj 🙏",
        "Combinado.",
        "vc viu?",
        "kkk",
        "até mais 😅",
    ]
    result = stats(as_messages(texts, runs=[0, 0, 1, 2, 3, 3, 4, 5, 5, 6]))
    assert result.messages == 10
    assert result.median_length == 9
    assert result.burst_share == 0.43
    assert result.lowercase_start == 0.8
    assert result.final_period == 0.2
    assert result.emoji_share == 0.3
    assert result.top_emoji == ["😅", "🙏"]
    assert result.emoji_position == "end"
    assert result.abbreviations == ["vc", "blz", "tmj"]
    assert result.laughter == "kkk"


def test_stats_of_nothing_is_empty():
    assert stats([]).messages == 0


def test_examples_skip_markers_numbers_and_links():
    texts = [
        "manda pro 98765 o endereço",
        "meu [CPF] tá aí",
        "olha https://x.com",
        "fala com @ana",
        "ok",
        "fechado então, até amanhã",
    ]
    assert pick_examples(as_messages(texts)) == ["fechado então, até amanhã"]


def test_examples_are_spread_and_distinct():
    texts = as_messages(["primeira mensagem", "primeira mensagem", "segunda mensagem"], "one")
    texts += as_messages(["outra conversa aqui"], "two")
    assert pick_examples(texts, count=3) == [
        "primeira mensagem",
        "outra conversa aqui",
        "segunda mensagem",
    ]


def test_excluded_examples_are_not_picked():
    texts = as_messages(["fechado então, até amanhã", "beleza, combinado assim"])
    assert pick_examples(texts, excluded={"fechado então, até amanhã"}) == [
        "beleza, combinado assim"
    ]


def test_lines_that_are_not_plain_are_not_learned():
    forwarded = ChatLine("user", "", "09:01", "text", "Encaminhada\nZZ-SOMEONE-ELSE", "", plain=False)
    assert from_screen((forwarded, user("bora sim, combinado")), "contact:1", "pt", set())[0].text == (
        "bora sim, combinado"
    )
    assert len(from_screen((forwarded,), "contact:1", "pt", set())) == 0


def test_examples_skip_names_places_prices_and_any_digit():
    texts = ["fica 50 a diária", "passa na Rua Augusta amanhã", "fala com a Ana depois",
             "custa R$ ou € tanto faz", "Combinado então, até amanhã", "beleza. Até mais tarde"]
    assert pick_examples(as_messages(texts)) == ["Combinado então, até amanhã", "beleza. Até mais tarde"]


def test_hash_ignores_punctuation_case_and_emoji():
    assert text_hash("Consigo sim, sexta de manhã 😅") == text_hash("consigo sim sexta de manhã!")
    assert text_hash("consigo sim") != text_hash("consigo não")


def test_examples_skip_known_names_and_places():
    from respondoia.style.sampler import name_hashes

    names = name_hashes("Ana Souza") | name_hashes("Marcos")
    texts = ["passa na casa da ana amanha cedo", "Tudo certo! Marcos leva o bolo",
             "manda o endereço da rua das flores", "beleza, combinado assim"]
    assert pick_examples(as_messages(texts), names=names) == ["beleza, combinado assim"]
