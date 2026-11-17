from respondoia.style.language import chat_language, detect


def test_detects_the_three_languages():
    assert detect("vc vai lá amanhã? to pensando em ir também") == "pt"
    assert detect("are you going there tomorrow? I was thinking of it") == "en"
    assert detect("vas a ir mañana? yo también quiero ir") == "es"


def test_short_messages_do_not_decide():
    for text in ("ok", "kkkk", "👍", "Marina", "18h", ""):
        assert detect(text) is None


def test_chat_language_is_the_majority():
    texts = [
        "ok",
        "vc vem hoje? to te esperando aqui",
        "are you there? see you then",
        "isso, tudo bem",
    ]
    assert chat_language(texts) == "pt"


def test_chat_language_without_a_decision_is_none():
    assert chat_language(["ok", "👍", "kkk"]) is None
