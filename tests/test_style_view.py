import pytest
from PySide6.QtWidgets import QMessageBox

from respondoia import i18n
from respondoia.style.profile import LabelStyle, StyleProfile, StyleTraits
from respondoia.style.sampler import StyleStats
from respondoia.style.service import LanguageView, StyleOverview
from respondoia.ui import style_view
from respondoia.ui.style_view import StyleWindow, describe

TRAITS = StyleTraits(
    formality="<b>informal</b> com amigos",
    greetings=["oi", "e aí"],
    farewells=["flw"],
    expressions=["bora"],
    avoids=["prezado"],
    habits=["manda várias curtas"],
    by_label=[LabelStyle(label="client", note="mais formal")],
)


def profile(traits=TRAITS):
    return StyleProfile(
        language="pt",
        messages=120,
        stats=StyleStats(
            messages=120,
            median_length=34,
            burst_share=0.4,
            lowercase_start=0.85,
            final_period=0.05,
            emoji_share=0.3,
            top_emoji=["😅"],
            emoji_position="end",
            abbreviations=["vc", "blz"],
            laughter="kkk",
        ),
        traits=traits,
        examples=["fechado então, até amanhã", "beleza, combinado assim"],
        updated_at="2026-11-17T10:00:00",
    )


def overview(*views):
    return StyleOverview(tuple(views))


@pytest.fixture(autouse=True)
def portuguese(qapp):
    i18n.install(qapp, "pt_BR")


@pytest.fixture
def window(qtbot):
    win = StyleWindow()
    qtbot.addWidget(win)
    return win


def test_overview_shows_counts_traits_and_examples(window):
    window.show_overview(overview(LanguageView("pt", 120, profile(), "nunca uso prezado", False)))
    assert window.language_combo.currentData() == "pt"
    assert window.language_combo.currentText() == "Português"
    assert window.status_label.text() == "120 mensagens suas analisadas"
    assert "Formalidade: <b>informal</b> com amigos" in window.noticed_label.text()
    assert "Abreviações: vc, blz" in window.noticed_label.text()
    assert [window.examples_list.item(i).text() for i in range(2)] == [
        "fechado então, até amanhã",
        "beleza, combinado assim",
    ]
    assert window.notes_edit.toPlainText() == "nunca uso prezado"
    assert window.small_label.isHidden()


def test_describe_translates_counts_and_traits():
    lines = describe(profile())
    assert (
        "Começa com letra minúscula em 85% das mensagens; termina com ponto final em 5%." in lines
    )
    assert "Com Cliente: mais formal" in lines
    assert "Ri com: kkk" in lines


def test_small_volume_shows_the_warning(window):
    window.show_overview(overview(LanguageView("pt", 12, profile(None), "", True)))
    assert not window.small_label.isHidden()
    assert window.status_label.text() == "Ainda aprendendo: 12 mensagens suas até agora"


def test_empty_overview_shows_the_hint(window):
    window.show_overview(overview())
    assert window.status_label.text().startswith("Ainda sem estilo.")
    assert not window.refresh_button.isEnabled() and window.examples_list.count() == 0


def test_dropping_a_txt_requests_an_import(window, qtbot):
    with qtbot.waitSignal(window.import_requested) as signal:
        assert window.drop_files(["C:/x/conversa.txt"])
    assert signal.args == ["C:/x/conversa.txt"]
    with qtbot.waitSignal(window.import_requested):
        window.drop_files(["C:/x/WhatsApp Chat.ZIP"])
    with qtbot.assertNotEmitted(window.import_requested):
        assert not window.drop_files(["C:/x/nota.pdf"])


def test_author_choice_emits_author_and_label(window, qtbot):
    window.ask_author((("Rafa Lima", 40), ("Marina Duarte", 30)))
    assert not window.author_box.isHidden()
    assert window.author_combo.itemText(0) == "Rafa Lima (40)"
    window.author_combo.setCurrentIndex(1)
    window.chat_label_combo.setCurrentIndex(window.chat_label_combo.findData("friend"))
    with qtbot.waitSignal(window.author_chosen) as signal:
        window.import_start_button.click()
    assert signal.args == ["Marina Duarte", "friend"]
    assert window.author_box.isHidden()


def test_cancel_after_reading_emits_only_the_cancel(window, qtbot):
    window.ask_author((("Rafa Lima", 40),))
    with qtbot.assertNotEmitted(window.author_chosen), qtbot.waitSignal(window.import_cancelled):
        window.cancel_button.click()
    assert window.author_box.isHidden()


def test_removing_an_example_emits_it(window, qtbot):
    window.show_overview(overview(LanguageView("pt", 120, profile(), "", False)))
    window.examples_list.setCurrentRow(1)
    with qtbot.waitSignal(window.example_removed) as signal:
        window.remove_button.click()
    assert signal.args == ["pt", "beleza, combinado assim"]


def test_notes_are_saved_for_the_language(window, qtbot):
    window.show_overview(overview(LanguageView("pt", 120, profile(), "", False)))
    window.notes_edit.setPlainText("nunca uso prezado")
    with qtbot.waitSignal(window.manual_saved) as signal:
        window.save_notes_button.click()
    assert signal.args == ["pt", "nunca uso prezado"]


def test_refresh_asks_for_the_shown_language(window, qtbot):
    window.show_overview(overview(LanguageView("pt", 120, profile(), "", False)))
    with qtbot.waitSignal(window.refresh_requested) as signal:
        window.refresh_button.click()
    assert signal.args == ["pt"]


@pytest.mark.parametrize(
    "answer, emitted",
    [(QMessageBox.StandardButton.No, False), (QMessageBox.StandardButton.Yes, True)],
)
def test_reset_asks_first(window, qtbot, monkeypatch, answer, emitted):
    monkeypatch.setattr(style_view.QMessageBox, "question", lambda *a, **k: answer)
    seen = []
    window.reset_requested.connect(lambda: seen.append(True))
    window.reset_button.click()
    assert bool(seen) == emitted


def test_errors_and_notices_are_translated(window):
    window.show_error("error.export_format")
    assert window.message_label.text() == "Este arquivo não é uma conversa exportada do WhatsApp."
    window.show_notice("style.imported", count=12)
    assert window.message_label.text().startswith("12 mensagens suas adicionadas.")
