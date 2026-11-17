import pytest

from respondoia import i18n
from respondoia.ui.memory_view import MemoryWindow


@pytest.fixture(autouse=True)
def portuguese(qapp):
    i18n.install(qapp, "pt_BR")


def test_memory_is_plain_text_and_saves(qtbot):
    window = MemoryWindow()
    qtbot.addWidget(window)
    window.show_memory(7, "Ana", "<b>Cliente</b> desde outubro")
    assert window.windowTitle() == "Memória de Ana"
    assert window.editor.toPlainText() == "<b>Cliente</b> desde outubro"
    window.editor.setPlainText("Prefere áudio.")
    with qtbot.waitSignal(window.saved) as signal:
        window.save_button.click()
    assert signal.args == [7, "Prefere áudio."]


def test_empty_memory_shows_the_hint(qtbot):
    window = MemoryWindow()
    qtbot.addWidget(window)
    window.show_memory(7, "Ana", "")
    assert window.editor.placeholderText().startswith("Ainda sem memória")
