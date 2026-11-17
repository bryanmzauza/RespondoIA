from dataclasses import replace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit

from respondoia import i18n
from respondoia.config import Settings
from respondoia.i18n import t
from respondoia.ui.settings import SettingsWindow


@pytest.fixture(autouse=True)
def portuguese(qapp):
    i18n.install(qapp, "pt_BR")


def make(qtbot, settings=None, has_api_key=False):
    window = SettingsWindow(settings or Settings(), has_api_key)
    qtbot.addWidget(window)
    window.show()
    return window


def choose(box, value):
    box.setCurrentIndex(box.findData(value))


def stored(listing):
    return [listing.item(i).data(Qt.ItemDataRole.UserRole) for i in range(listing.count())]


def test_recorder_writes_the_combination(qtbot):
    window = make(qtbot)
    edit = window.hotkey_edit
    with qtbot.waitSignal(edit.hotkey_changed) as sig:
        qtbot.keyClick(edit, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
    assert sig.args == ["ctrl+alt+r"]
    assert edit.text() == "Ctrl+Alt+R"


def test_recorder_rejects_a_key_without_modifier(qtbot):
    window = make(qtbot)
    edit = window.hotkey_edit
    with qtbot.assertNotEmitted(edit.hotkey_changed):
        qtbot.keyClick(edit, Qt.Key.Key_A)
    assert window.error_label.text() == t("settings.hotkey_no_modifier")
    assert edit.value == "ctrl+shift+space"


def test_save_emits_the_new_settings(qtbot):
    window = make(qtbot)
    choose(window.count_box, 2)
    choose(window.input_box, "screenshot")
    choose(window.position_box, "top-left")
    window.mask_check.setChecked(False)
    with qtbot.waitSignal(window.saved) as sig:
        qtbot.mouseClick(window.save_button, Qt.MouseButton.LeftButton)
    assert sig.args[0] == replace(
        Settings(),
        suggestion_count=2,
        input="screenshot",
        panel_position="top-left",
        mask_sensitive_data=False,
    )


def test_unchanged_window_saves_the_same_settings(qtbot):
    settings = Settings(language="en", model="claude-sonnet-5-5", start_with_windows=True)
    window = make(qtbot, settings)
    with qtbot.waitSignal(window.saved) as sig:
        qtbot.mouseClick(window.save_button, Qt.MouseButton.LeftButton)
    assert sig.args[0] == settings


def test_cancel_emits_nothing(qtbot):
    window = make(qtbot)
    with qtbot.assertNotEmitted(window.saved):
        qtbot.mouseClick(window.cancel_button, Qt.MouseButton.LeftButton)
    assert not window.isVisible()


def test_instructions_can_be_added_renamed_and_removed(qtbot):
    window = make(qtbot)
    listing = window.instructions_list
    assert listing.item(0).text() == "Mais curto" and stored(listing)[0] == "shorter"
    window.instruction_edit.setText("Responder com carinho")
    qtbot.mouseClick(window.instruction_add, Qt.MouseButton.LeftButton)
    listing.item(0).setText("Mais curtinho")  # what an in-place edit does
    listing.setCurrentRow(1)
    qtbot.mouseClick(window.instruction_remove, Qt.MouseButton.LeftButton)
    with qtbot.waitSignal(window.saved) as sig:
        qtbot.mouseClick(window.save_button, Qt.MouseButton.LeftButton)
    assert sig.args[0].quick_instructions == (
        "Mais curtinho",
        "decline_politely",
        "ask_for_time",
        "Responder com carinho",
    )


def test_private_contact_can_be_removed(qtbot):
    window = make(qtbot, Settings(private_contacts=("Marina Duarte", "Caio Lima")))
    window.private_list.setCurrentRow(0)
    qtbot.mouseClick(window.private_remove, Qt.MouseButton.LeftButton)
    with qtbot.waitSignal(window.saved) as sig:
        qtbot.mouseClick(window.save_button, Qt.MouseButton.LeftButton)
    assert sig.args[0].private_contacts == ("Caio Lima",)


def test_api_key_is_never_shown(qtbot):
    window = make(qtbot, has_api_key=True)
    assert window.api_key_edit.text() == ""
    assert window.api_key_edit.echoMode() == QLineEdit.EchoMode.Password
    assert window.api_key_state.text() == t("settings.api_key_set")
    with qtbot.assertNotEmitted(window.api_key_entered):
        qtbot.mouseClick(window.save_button, Qt.MouseButton.LeftButton)
    window.api_key_edit.setText("  sk-test-not-a-real-key ")
    with qtbot.waitSignal(window.api_key_entered) as sig:
        qtbot.mouseClick(window.save_button, Qt.MouseButton.LeftButton)
    assert sig.args == ["sk-test-not-a-real-key"]


def test_show_error_fills_the_placeholder(qtbot):
    window = make(qtbot)
    window.show_error("settings.hotkey_in_use", hotkey="Ctrl+Alt+R")
    assert window.error_label.text() == "O atalho Ctrl+Alt+R já está em uso por outro programa."


def test_knowledge_button_emits(qtbot):
    window = make(qtbot)
    with qtbot.waitSignal(window.knowledge_requested):
        window.knowledge_button.click()
