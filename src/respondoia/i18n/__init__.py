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


def install(app: QCoreApplication, language: str) -> None:
    global _installed
    if _installed is not None:
        app.removeTranslator(_installed)
    translator = QTranslator(app)
    if not translator.load(str(_HERE / f"{language}.qm")):
        raise FileNotFoundError(f"no translation file for {language}")
    app.installTranslator(translator)
    _installed = translator


def t(key: str, **values: object) -> str:
    text = QCoreApplication.translate("ui", key)
    return text.format(**values) if values else text
