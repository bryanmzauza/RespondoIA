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
}


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


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


def add_private_contact(path: Path, name: str) -> Settings:
    data = _read(path)
    settings = _settings_from(data)
    wanted = normalize_name(name)
    if wanted and all(normalize_name(c) != wanted for c in settings.private_contacts):
        data.setdefault("privacy", {})["private_contacts"] = [
            *settings.private_contacts,
            name.strip(),
        ]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tomli_w.dumps(data), encoding="utf-8")
    return _settings_from(data)


def get_api_key() -> str | None:
    return keyring.get_password(KEYRING_SERVICE, KEYRING_USER)


def set_api_key(key: str) -> None:
    keyring.set_password(KEYRING_SERVICE, KEYRING_USER, key)
