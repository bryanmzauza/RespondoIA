"""Load and save settings. API keys go to the Windows Credential Manager, not to disk."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

import keyring
import tomli_w

from respondoia.privacy import normalize_name

KEYRING_SERVICE = "RespondoIA"
KEYRING_USER = "anthropic-api-key"


class ConfigError(ValueError):
    def __init__(self, key: str) -> None:
        super().__init__(f"invalid value for {key}")
        self.key = key  # dotted key, for example "suggestions.count"


# Quick instructions that come with the app. Their labels come from the translations;
# any other entry in the list is text the user wrote.
DEFAULT_INSTRUCTIONS = ("shorter", "more_formal", "decline_politely", "ask_for_time")
PANEL_POSITIONS = ("bottom-right", "bottom-left", "top-right", "top-left")


@dataclass(frozen=True)
class Settings:
    hotkey: str = "ctrl+shift+space"
    insert_mode: str = "paste"  # "paste" or "copy"
    allowed_windows: tuple[str, ...] = ("WhatsApp",)
    language: str = "auto"  # "auto", "pt-BR" or "en"
    suggestion_count: int = 3  # 2 or 3
    model: str = "claude-haiku-5-5"
    max_image_side: int = 1300
    private_contacts: tuple[str, ...] = ()
    input: str = "text"  # "text" (screenshot as fallback) or "screenshot"
    mask_sensitive_data: bool = True
    # Pending photos from the contact go with the text request. Pixels cannot be masked.
    send_pictures: bool = True
    panel_position: str = "bottom-right"
    start_with_windows: bool = False
    quick_instructions: tuple[str, ...] = DEFAULT_INSTRUCTIONS
    # Off until the user turns it on: the first-run screen will ask.
    history_enabled: bool = False
    history_include_groups: bool = False
    snippets_per_suggestion: int = 5
    quick_load_screens: int = 15
    max_load_screens: int = 250
    max_load_months: int = 12  # 0: no limit by date
    retention_months: int = 12


def app_dir() -> Path:
    path = Path(os.environ["LOCALAPPDATA"]) / "RespondoIA"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return app_dir() / "config.toml"


def _is_str(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_str_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_bool(value: object) -> bool:
    return isinstance(value, bool)


def _in(value: object, low: int, high: int) -> bool:
    return _is_int(value) and low <= value <= high


# Settings field -> (table, key, validator). Other keys in the file belong to later stages.
_KEYS = {
    "hotkey": ("general", "hotkey", _is_str),
    "insert_mode": ("general", "insert_mode", lambda v: v in ("paste", "copy")),
    "allowed_windows": ("general", "allowed_windows", lambda v: _is_str_list(v) and bool(v)),
    "language": ("general", "language", lambda v: v in ("auto", "pt-BR", "en")),
    "suggestion_count": ("suggestions", "count", lambda v: _is_int(v) and v in (2, 3)),
    "model": ("ai", "model", _is_str),
    "max_image_side": ("ai", "max_image_side", lambda v: _is_int(v) and v > 0),
    "private_contacts": ("privacy", "private_contacts", _is_str_list),
    "input": ("ai", "input", lambda v: v in ("text", "screenshot")),
    "mask_sensitive_data": ("privacy", "mask_sensitive_data", _is_bool),
    "send_pictures": ("privacy", "send_pictures", _is_bool),
    "panel_position": ("general", "panel_position", lambda v: v in PANEL_POSITIONS),
    "start_with_windows": ("general", "start_with_windows", _is_bool),
    "quick_instructions": (
        "suggestions",
        "quick_instructions",
        lambda v: _is_str_list(v) and all(item.strip() for item in v),
    ),
    "history_enabled": ("history", "enabled", _is_bool),
    "history_include_groups": ("history", "include_groups", _is_bool),
    "snippets_per_suggestion": ("history", "snippets_per_suggestion", lambda v: _in(v, 0, 10)),
    "quick_load_screens": ("history", "quick_load_screens", lambda v: _in(v, 1, 80)),
    "max_load_screens": ("history", "max_load_screens", lambda v: _in(v, 1, 1000)),
    "max_load_months": ("history", "max_load_months", lambda v: _in(v, 0, 24)),
    "retention_months": ("history", "retention_months", lambda v: _in(v, 1, 120)),
}


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError("config.toml") from exc


def _settings_from(data: dict) -> Settings:
    values = {}
    for field, (table, key, valid) in _KEYS.items():
        section = data.get(table, {})
        if not isinstance(section, dict):
            raise ConfigError(table)
        if key not in section:
            continue
        value = section[key]
        if not valid(value):
            raise ConfigError(f"{table}.{key}")
        values[field] = tuple(value) if isinstance(value, list) else value
    return Settings(**values)


def load_settings(path: Path) -> Settings:
    return _settings_from(_read(path))


def _write(path: Path, data: dict) -> None:
    # Write to a temporary file first so a crash never leaves half a config.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(tomli_w.dumps(data), encoding="utf-8")
    os.replace(temporary, path)


def save_settings(path: Path, settings: Settings) -> None:
    """Write every key this stage knows. Other keys and tables in the file are kept."""
    data = _read(path)
    for field, (table, key, valid) in _KEYS.items():
        value = getattr(settings, field)
        value = list(value) if isinstance(value, tuple) else value
        if not valid(value):
            raise ConfigError(f"{table}.{key}")
        data.setdefault(table, {})[key] = value
    _write(path, data)


def add_private_contact(path: Path, name: str) -> Settings:
    data = _read(path)
    settings = _settings_from(data)
    wanted = normalize_name(name)
    if wanted and all(normalize_name(c) != wanted for c in settings.private_contacts):
        data.setdefault("privacy", {})["private_contacts"] = [
            *settings.private_contacts,
            name.strip(),
        ]
        _write(path, data)
    return _settings_from(data)


def get_api_key() -> str | None:
    return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)


def set_api_key(key: str) -> None:
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
