from respondoia.domain import ChatRef, OpenChat, Rect
from respondoia.privacy import (
    PasteDecision,
    SendDecision,
    check_before_paste,
    check_before_send,
    is_private,
    normalize_name,
)


def test_normalize_ignores_case_and_extra_spaces():
    assert normalize_name("  Ana   Paula ") == normalize_name("ana paula")


def test_private_needs_the_whole_name():
    assert is_private("Ana Paula", ["ana paula"])
    assert not is_private("Ana", ["Ana Paula"])
    assert not is_private("Ana Paula Souza", ["Ana Paula"])


def test_accents_and_emoji_are_kept():
    assert not is_private("Jose", ["José"])
    assert is_private("Bia 🌻", ["bia 🌻"])


def test_send_decisions():
    chat = OpenChat("Marina Duarte", Rect(400, 0, 1200, 800))
    assert check_before_send(chat, []) is SendDecision.ALLOW
    assert check_before_send(chat, ["marina duarte"]) is SendDecision.PRIVATE
    assert check_before_send(None, []) is SendDecision.UNKNOWN_CHAT
    assert check_before_send(OpenChat("  ", Rect(0, 0, 1, 1)), []) is SendDecision.UNKNOWN_CHAT


def test_paste_only_into_the_same_window_and_chat():
    captured = ChatRef(100, "Marina Duarte")
    same = ChatRef(100, "marina duarte")
    assert check_before_paste(captured, same, "paste") is PasteDecision.PASTE
    assert check_before_paste(captured, ChatRef(100, "Caio Lima"), "paste") is PasteDecision.COPY_ONLY
    other_window = ChatRef(200, "Marina Duarte")
    assert check_before_paste(captured, other_window, "paste") is PasteDecision.COPY_ONLY
    assert check_before_paste(captured, None, "paste") is PasteDecision.COPY_ONLY
    exact = ChatRef(100, "Marina Duarte")
    assert check_before_paste(captured, exact, "copy") is PasteDecision.COPY_ONLY
