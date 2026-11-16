import pytest
from PySide6.QtCore import Qt

from respondoia import i18n
from respondoia.domain import Rect
from respondoia.llm import ChatMessage, Suggestion, SuggestionReply
from respondoia.ui.panel import (
    COMPOSER_CLEARANCE,
    MARGIN,
    SuggestionPanel,
    panel_position,
    to_logical,
)

LONG = "Bom dia! Sobre o prazo, consigo entregar na sexta sim. " * 4
CHAT = Rect(400, 0, 1600, 900)


def make_reply(count, note=None):
    texts = ["Consigo sim, sexta de manhã", "Bom dia! Consigo sim", LONG.strip()][:count]
    return SuggestionReply(
        contact="Marina Duarte",
        summary="Quer saber prazo e valor do pedido",
        messages=[
            ChatMessage(id=1, author="contact", text="Bom dia!", pending=False),
            ChatMessage(id=2, author="contact", text="Consegue entregar até sexta?", pending=True),
            ChatMessage(id=3, author="contact", text="E o valor fica quanto?", pending=True),
        ],
        suggestions=[Suggestion(label=f"Opção {n}", text=t) for n, t in enumerate(texts, 1)],
        note=note,
    )


@pytest.fixture(autouse=True)
def portuguese(qapp):
    i18n.install(qapp, "pt_BR")


@pytest.fixture
def reply3():
    return make_reply(3)


@pytest.fixture
def reply2():
    return make_reply(2)


def loading_panel(qtbot):
    panel = SuggestionPanel()
    qtbot.addWidget(panel)
    panel.show_loading("Marina Duarte", CHAT)
    return panel


def make_panel(qtbot, reply):
    panel = loading_panel(qtbot)
    panel.show_reply(reply)
    return panel


def test_position_is_bottom_right_above_the_composer():
    assert panel_position(Rect(400, 0, 1600, 900), (420, 300), Rect(0, 0, 1920, 1080)) == (
        1600 - 420 - MARGIN,
        900 - COMPOSER_CLEARANCE - 300,
    )


def test_position_stays_on_screen():
    x, y = panel_position(Rect(400, 0, 1600, 200), (420, 300), Rect(0, 0, 1920, 1080))
    assert y >= 0 and x >= 0
    x, y = panel_position(Rect(1700, 600, 2400, 1300), (420, 300), Rect(0, 0, 1920, 1080))
    assert x + 420 <= 1920 and y + 300 <= 1080


def test_to_logical():
    assert to_logical(Rect(150, 300, 1500, 900), 1.5) == Rect(100, 200, 1000, 600)


def test_loading_shows_the_title_and_the_loading_text(qtbot):
    panel = loading_panel(qtbot)
    assert panel.isVisible()
    assert panel.title_label.text() == "Marina Duarte"
    assert panel.status_label.text() == "Lendo a conversa…"


def test_reply_shows_summary_and_numbered_options(qtbot, reply3):
    panel = make_panel(qtbot, reply3)
    assert "Quer saber prazo e valor do pedido" in panel.summary_label.text()
    assert panel.summary_label.text().startswith("Resumo")
    shown = [b for b in panel.option_buttons if not b.isHidden()]
    assert len(shown) == 3
    assert shown[0].text().startswith("1") and "Opção 1" in shown[0].text()
    assert panel.details.isHidden()


def test_number_key_chooses(qtbot, reply3):
    panel = make_panel(qtbot, reply3)
    with qtbot.waitSignal(panel.suggestion_chosen) as sig:
        qtbot.keyClick(panel, "2")
    assert sig.args == [1]


def test_clicking_an_option_chooses_it(qtbot, reply3):
    panel = make_panel(qtbot, reply3)
    with qtbot.waitSignal(panel.suggestion_chosen) as sig:
        qtbot.mouseClick(panel.option_buttons[2], Qt.MouseButton.LeftButton)
    assert sig.args == [2]


def test_number_typed_in_the_instruction_field_does_not_choose(qtbot, reply3):
    panel = make_panel(qtbot, reply3)
    qtbot.keyClick(panel, Qt.Key.Key_Tab)
    with qtbot.assertNotEmitted(panel.suggestion_chosen):
        qtbot.keyClicks(panel.instruction, "2")
    assert panel.instruction.text() == "2"


def test_key_3_is_ignored_with_two_suggestions(qtbot, reply2):
    panel = make_panel(qtbot, reply2)
    assert len([b for b in panel.option_buttons if not b.isHidden()]) == 2
    with qtbot.assertNotEmitted(panel.suggestion_chosen):
        qtbot.keyClick(panel, "3")


def test_keys_are_ignored_while_loading(qtbot, reply3):
    panel = loading_panel(qtbot)
    with qtbot.assertNotEmitted(panel.suggestion_chosen):
        qtbot.keyClick(panel, "1")
    panel.show_reply(reply3)
    panel.show_loading("Marina Duarte", CHAT)  # regenerating
    with qtbot.assertNotEmitted(panel.suggestion_chosen):
        qtbot.keyClick(panel, "1")


def test_escape_emits_closed(qtbot, reply3):
    panel = make_panel(qtbot, reply3)
    with qtbot.waitSignal(panel.closed):
        qtbot.keyClick(panel, Qt.Key.Key_Escape)
    assert not panel.isVisible()


def test_dismiss_hides_without_emitting_closed(qtbot, reply3):
    panel = make_panel(qtbot, reply3)
    with qtbot.assertNotEmitted(panel.closed):
        panel.dismiss()
    assert not panel.isVisible()


def test_tab_shows_messages_with_pending_checked(qtbot, reply3):
    panel = make_panel(qtbot, reply3)
    qtbot.keyClick(panel, Qt.Key.Key_Tab)
    assert not panel.details.isHidden()
    assert [box.isChecked() for box in panel.message_boxes] == [False, True, True]
    assert panel.message_boxes[1].text() == "Consegue entregar até sexta?"
    qtbot.keyClick(panel, Qt.Key.Key_Tab)
    assert panel.details.isHidden()


def test_adjust_button_shows_the_details(qtbot, reply3):
    panel = make_panel(qtbot, reply3)
    qtbot.mouseClick(panel.adjust_button, Qt.MouseButton.LeftButton)
    assert not panel.details.isHidden()


def test_enter_in_the_field_requests_regenerate_with_checked_ids(qtbot, reply3):
    panel = make_panel(qtbot, reply3)
    qtbot.keyClick(panel, Qt.Key.Key_Tab)
    panel.message_boxes[1].setChecked(False)
    qtbot.keyClicks(panel.instruction, "pedir prazo")
    with qtbot.waitSignal(panel.regenerate_requested) as sig:
        qtbot.keyClick(panel.instruction, Qt.Key.Key_Return)
    assert sig.args == [(3,), "pedir prazo"]


def test_regenerate_button_sends_the_same_request(qtbot, reply3):
    panel = make_panel(qtbot, reply3)
    qtbot.keyClick(panel, Qt.Key.Key_Tab)
    with qtbot.waitSignal(panel.regenerate_requested) as sig:
        qtbot.mouseClick(panel.regenerate_button, Qt.MouseButton.LeftButton)
    assert sig.args == [(2, 3), ""]


def test_mark_private_button_emits(qtbot, reply3):
    panel = make_panel(qtbot, reply3)
    qtbot.keyClick(panel, Qt.Key.Key_Tab)
    with qtbot.waitSignal(panel.mark_private_requested):
        qtbot.mouseClick(panel.private_button, Qt.MouseButton.LeftButton)


def test_full_text_is_in_the_tooltip(qtbot, reply3):
    panel = make_panel(qtbot, reply3)
    button = panel.option_buttons[2]
    assert button.toolTip() == LONG.strip()
    assert button.text() != LONG.strip() and button.text().endswith("…")
    assert "\n" not in button.text()


def test_error_shows_translated_text_and_retry(qtbot, reply3):
    panel = make_panel(qtbot, reply3)
    panel.show_error("network")
    assert panel.status_label.text() == "Sem conexão com o modelo."
    assert not panel.retry_button.isHidden()
    with qtbot.assertNotEmitted(panel.suggestion_chosen):
        qtbot.keyClick(panel, "1")
    with qtbot.waitSignal(panel.retry_requested):
        qtbot.mouseClick(panel.retry_button, Qt.MouseButton.LeftButton)


def test_note_is_shown_when_present(qtbot, reply3):
    panel = make_panel(qtbot, reply3)
    assert panel.note_label.isHidden()
    panel.show_reply(make_reply(3, note="Há um áudio que não consigo ouvir"))
    assert not panel.note_label.isHidden()
    assert panel.note_label.text().startswith("Observação")
    assert "Há um áudio que não consigo ouvir" in panel.note_label.text()


def test_notice_shows_the_translated_text(qtbot, reply3):
    panel = make_panel(qtbot, reply3)
    panel.show_notice("notice.copied")
    assert panel.status_label.text() == "Resposta copiada."
    assert not panel.status_label.isHidden()


def test_panel_sits_inside_the_chat_area_above_the_composer(qtbot, reply3):
    panel = make_panel(qtbot, reply3)
    screen = panel.screen().geometry()
    chat_bottom = min(CHAT.bottom, screen.y() + screen.height())
    assert panel.y() + panel.height() <= chat_bottom
    assert panel.x() >= 0 and panel.y() >= 0
