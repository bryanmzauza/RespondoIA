import pytest

from respondoia import i18n
from respondoia.ui.style_view import StyleWindow


@pytest.fixture(autouse=True)
def portuguese(qapp):
    i18n.install(qapp, "pt_BR")


def test_load_button_emits(qtbot):
    window = StyleWindow()
    qtbot.addWidget(window)
    assert window.load_hint.text().startswith("Abra uma conversa")
    with qtbot.waitSignal(window.load_requested):
        window.load_button.click()
