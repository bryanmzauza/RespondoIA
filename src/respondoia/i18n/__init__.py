"""Interface translations. Every text the user sees comes from here, by key.

The .ts files are written by hand, with the key as source text. Compile them after
an edit:

    uv run pyside6-lrelease src/respondoia/i18n/en.ts src/respondoia/i18n/pt_BR.ts

The .qm files are committed so the app runs from a clone without a build step.
"""

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTranslator

_HERE = Path(__file__).parent
_installed: QTranslator | None = None


def pick_language(setting: str, system_locale: str) -> str:
    """Map the config value and the system locale to "en" or "pt_BR"."""
    if setting == "pt-BR":
        return "pt_BR"
    if setting == "en":
        return "en"
    return "pt_BR" if system_locale.lower().startswith("pt") else "en"


def system_locale() -> str:
    """The Windows display language, for example "pt-BR".

    Not QLocale.system(): Qt also reads LANG, which terminals such as Git Bash set to
    en_US.UTF-8 whatever the Windows language is.
    """
    import ctypes

    kernel32 = ctypes.windll.kernel32
    buffer = ctypes.create_unicode_buffer(85)
    if kernel32.LCIDToLocaleName(kernel32.GetUserDefaultUILanguage(), buffer, len(buffer), 0):
        return buffer.value
    return ""


def install(app: QCoreApplication, language: str) -> None:
    global _installed
    if _installed is not None:
        app.removeTranslator(_installed)
    translator = QTranslator(app)
    if not translator.load(str(_HERE / f"{language}.qm")):
        raise FileNotFoundError(f"no translation file for {language}")
    app.installTranslator(translator)
    _installed = translator


def t(key: str, /, **values: object) -> str:
    # Positional-only, so a placeholder may itself be called "key".
    text = QCoreApplication.translate("ui", key)
    return text.format(**values) if values else text


# Built-in quick instructions and their keys. Any other entry is the user's own text.
_INSTRUCTION_KEYS = {
    "shorter": "instruction.shorter",
    "more_formal": "instruction.more_formal",
    "decline_politely": "instruction.decline_politely",
    "ask_for_time": "instruction.ask_for_time",
}


def instruction_label(entry: str) -> str:
    key = _INSTRUCTION_KEYS.get(entry)
    return t(key) if key else entry
