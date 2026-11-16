import re
import xml.etree.ElementTree as ET
from pathlib import Path

from respondoia.i18n import install, pick_language, t

SRC = Path(__file__).parents[1] / "src" / "respondoia"
LANGUAGES = ["en", "pt_BR"]
LLM_ERROR_CODES = [
    "auth",
    "rate_limit",
    "network",
    "server",
    "refused",
    "truncated",
    "invalid_reply",
]


def texts(language):
    root = ET.parse(SRC / "i18n" / f"{language}.ts").getroot()
    assert [c.findtext("name") for c in root.findall("context")] == ["ui"]
    return {m.findtext("source"): m.findtext("translation") for m in root.iter("message")}


def test_pick_language():
    assert pick_language("auto", "pt_BR") == "pt_BR" and pick_language("auto", "pt_PT") == "pt_BR"
    assert pick_language("auto", "de_DE") == "en"
    assert pick_language("pt-BR", "en_US") == "pt_BR" and pick_language("en", "pt_BR") == "en"


def test_both_files_have_the_same_keys_and_no_empty_text():
    en, pt = texts("en"), texts("pt_BR")
    assert set(en) == set(pt) and len(en) >= 30
    for language in (en, pt):
        assert all(text and text.strip() for text in language.values())


def test_placeholders_match_between_languages():
    en, pt = texts("en"), texts("pt_BR")
    for key in en:
        assert set(re.findall(r"\{(\w+)\}", en[key])) == set(re.findall(r"\{(\w+)\}", pt[key])), key


def test_every_key_used_in_code_exists():
    keys = set(texts("en"))
    used = set()
    for path in SRC.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        used |= set(re.findall(r"""["']((?:panel|tray|notice|error)\.[a-z_]+)["']""", source))
    assert used <= keys, used - keys
    assert {f"error.{code}" for code in LLM_ERROR_CODES} <= keys
    assert {"error.capture", "error.config", "error.no_api_key"} <= keys


def test_compiled_files_match_the_sources(qapp):
    for language in LANGUAGES:
        install(qapp, language)
        for key, text in texts(language).items():
            assert t(key) == text, (language, key)


def test_placeholders_are_filled(qapp):
    install(qapp, "pt_BR")
    assert (
        t("notice.started", hotkey="Ctrl+Shift+Space")
        == "RespondoIA está ativo. Atalho: Ctrl+Shift+Space"
    )


def test_switching_language_replaces_the_previous_one(qapp):
    install(qapp, "pt_BR")
    install(qapp, "en")
    assert t("tray.quit") == "Quit"


def test_a_missing_key_comes_back_as_the_key(qapp):
    install(qapp, "en")
    assert t("panel.not_a_key") == "panel.not_a_key"


def test_no_literal_text_in_widgets():
    pattern = (
        r"(setText|setToolTip|setPlaceholderText|setWindowTitle|QLabel|QPushButton|QAction"
        r"|addAction|showMessage)\(\s*f?[\"']"
    )
    for path in (SRC / "ui").glob("*.py"):
        assert not re.search(pattern, path.read_text(encoding="utf-8")), path.name


def test_a_placeholder_named_key_is_filled(qapp):
    install(qapp, "en")
    assert t("error.config", key="suggestions.count") == (
        "config.toml has an invalid value: suggestions.count"
    )
