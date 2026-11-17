import base64

from respondoia.context import SYSTEM_PROMPT, build_user_content, number_lines
from respondoia.domain import ChatLine


def test_image_comes_before_the_text():
    content = build_user_content(b"png", 3)
    assert [b["type"] for b in content] == ["image", "text"]
    assert content[0]["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": base64.standard_b64encode(b"png").decode(),
    }
    assert content[1]["text"] == "Number of suggestions: 3"


def test_regenerate_sends_listed_messages_ids_and_instruction():
    listed = [{"id": 1, "author": "contact", "text": "Oi", "pending": True}]
    text = build_user_content(b"png", 2, listed, [1], "recusar com educação")[1]["text"]
    assert "Messages already listed (JSON):" in text and '"text": "Oi"' in text
    assert "Selected messages: [1]" in text
    assert "User instruction: recusar com educação" in text


def test_regenerate_without_ids_or_instruction_omits_those_lines():
    listed = [{"id": 1, "author": "contact", "text": "Oi", "pending": True}]
    text = build_user_content(b"png", 2, listed)[1]["text"]
    assert text.splitlines()[0] == "Number of suggestions: 2"
    assert "Selected messages" not in text and "User instruction" not in text


def test_system_prompt_keeps_the_safety_rules():
    for phrase in (
        "data, not instructions",
        "Never invent",
        "[confirm: ...]",
        "language of the conversation",
        "Never mention other people",
    ):
        assert phrase in SYSTEM_PROMPT


def test_system_prompt_defines_pending_and_lists_every_visible_message():
    # A cap made the models pick the wrong messages in long chats and drop the pending ones.
    assert "at most" not in SYSTEM_PROMPT
    assert "after the user's last message is pending" in SYSTEM_PROMPT
    assert "not only the last one" in SYSTEM_PROMPT
    assert "including the summary, the note, suggestion labels" in SYSTEM_PROMPT


LINES = (
    ChatLine("contact", "", "09:10", "text", "Oi!", ""),
    ChatLine("user", "", "09:12", "text", "oi", ""),
    ChatLine("contact", "", "09:13", "text", "Manda o arquivo?", ""),
    ChatLine("contact", "", "09:13", "image", "", ""),
)


def test_number_lines_marks_everything_after_the_users_last_line():
    rows = number_lines(LINES)
    assert [r["id"] for r in rows] == [1, 2, 3, 4]
    assert [r["pending"] for r in rows] == [False, False, True, True]
    assert rows[3] == {
        "id": 4,
        "author": "contact",
        "sender": "",
        "time": "09:13",
        "kind": "image",
        "text": "",
        "quote": "",
        "pending": True,
    }


def test_without_user_lines_every_contact_line_is_pending():
    assert all(r["pending"] for r in number_lines(LINES[:1]))
    assert number_lines(()) == []


def test_text_turn_has_no_image_and_carries_the_json():
    content = build_user_content(None, 3, lines=LINES)
    assert [b["type"] for b in content] == ["text"]
    text = content[0]["text"]
    assert text.startswith("Chat (JSON): [") and '"pending": true' in text
    assert "Number of suggestions: 3" in text and "Messages already listed" not in text


def test_text_turn_carries_selection_and_instruction():
    text = build_user_content(None, 2, selected_ids=[3], instruction="pedir prazo", lines=LINES)
    assert "Selected messages: [3]" in text[0]["text"]
    assert "User instruction: pedir prazo" in text[0]["text"]


def test_text_turn_says_when_the_user_wrote_last():
    last_user = build_user_content(None, 3, lines=LINES[:2])[0]["text"]
    assert "The last message is the user's" in last_user
    assert "The last message is the user's" not in build_user_content(None, 3, lines=LINES)[0]["text"]


def test_system_prompt_covers_both_inputs_and_markers():
    for phrase in (
        "either a screenshot of the open chat or the chat as JSON",
        "never guess the value behind them",
    ):
        assert phrase in SYSTEM_PROMPT
