import tomllib
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend

from respondoia.__main__ import main
from respondoia.config import (
    DEFAULT_INSTRUCTIONS,
    ConfigError,
    Settings,
    add_private_contact,
    get_api_key,
    load_settings,
    save_settings,
    set_api_key,
)

EXAMPLE = Path(__file__).parents[1] / "config.example.toml"


class MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        super().__init__()
        self.store = {}

    def get_password(self, service, username):
        return self.store.get((service, username))

    def set_password(self, service, username, password):
        self.store[(service, username)] = password

    def delete_password(self, service, username):
        del self.store[(service, username)]


@pytest.fixture
def memory_keyring():
    previous = keyring.get_keyring()
    keyring.set_keyring(MemoryKeyring())
    yield
    keyring.set_keyring(previous)


def test_missing_file_gives_defaults(tmp_path):
    assert load_settings(tmp_path / "config.toml") == Settings()


def test_example_file_matches_defaults():
    assert load_settings(EXAMPLE) == Settings()


def test_reads_the_keys_used_in_this_stage(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[general]\nhotkey = "ctrl+alt+r"\ninsert_mode = "copy"\n'
        '[suggestions]\ncount = 2\n[ai]\nmodel = "claude-sonnet-5-5"\n'
        '[privacy]\nprivate_contacts = ["Marina Duarte"]\n',
        encoding="utf-8",
    )
    s = load_settings(path)
    assert (s.hotkey, s.insert_mode, s.suggestion_count, s.model) == (
        "ctrl+alt+r",
        "copy",
        2,
        "claude-sonnet-5-5",
    )
    assert s.private_contacts == ("Marina Duarte",)


@pytest.mark.parametrize(
    "body,key",
    [
        ("[suggestions]\ncount = 4\n", "suggestions.count"),
        ('[general]\ninsert_mode = "type"\n', "general.insert_mode"),
        ("[ai]\nmax_image_side = 0\n", "ai.max_image_side"),
    ],
)
def test_invalid_value_names_the_key(tmp_path, body, key):
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError) as err:
        load_settings(path)
    assert err.value.key == key


def test_add_private_contact_keeps_other_tables_and_avoids_duplicates(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[history]\nenabled = true\n", encoding="utf-8")
    add_private_contact(path, "Marina Duarte")
    s = add_private_contact(path, "  marina   duarte ")
    assert s.private_contacts == ("Marina Duarte",)
    assert tomllib.loads(path.read_text(encoding="utf-8"))["history"]["enabled"] is True


def test_api_key_goes_to_the_keyring_and_never_to_the_file(tmp_path, memory_keyring):
    set_api_key("sk-test-not-a-real-key")
    add_private_contact(tmp_path / "config.toml", "Caio Lima")
    assert get_api_key() == "sk-test-not-a-real-key"
    assert "sk-test" not in (tmp_path / "config.toml").read_text(encoding="utf-8")


def test_set_api_key_flag_stores_the_typed_key(monkeypatch, memory_keyring):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": " sk-test-typed ")
    assert main(["--set-api-key"]) == 0
    assert get_api_key() == "sk-test-typed"


def test_a_file_that_is_not_valid_toml_is_a_config_error(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[general\nhotkey = ", encoding="utf-8")
    with pytest.raises(ConfigError) as err:
        load_settings(path)
    assert err.value.key == "config.toml"
    with pytest.raises(ConfigError):
        add_private_contact(path, "Marina Duarte")
    assert path.read_text(encoding="utf-8") == "[general\nhotkey = "


def test_defaults_of_this_stage():
    s = Settings()
    assert (s.input, s.mask_sensitive_data, s.panel_position, s.start_with_windows) == (
        "text",
        True,
        "bottom-right",
        False,
    )
    assert s.quick_instructions == DEFAULT_INSTRUCTIONS == (
        "shorter",
        "more_formal",
        "decline_politely",
        "ask_for_time",
    )


def test_reads_input_masking_and_panel_keys(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[general]\npanel_position = "top-left"\nstart_with_windows = true\n'
        '[suggestions]\nquick_instructions = ["shorter", "Responder com carinho"]\n'
        '[ai]\ninput = "screenshot"\n[privacy]\nmask_sensitive_data = false\n',
        encoding="utf-8",
    )
    s = load_settings(path)
    assert (s.input, s.mask_sensitive_data, s.panel_position, s.start_with_windows) == (
        "screenshot",
        False,
        "top-left",
        True,
    )
    assert s.quick_instructions == ("shorter", "Responder com carinho")


@pytest.mark.parametrize(
    "body,key",
    [
        ('[ai]\ninput = "audio"\n', "ai.input"),
        ("[privacy]\nmask_sensitive_data = 1\n", "privacy.mask_sensitive_data"),
        ('[general]\npanel_position = "middle"\n', "general.panel_position"),
        ('[general]\nstart_with_windows = "yes"\n', "general.start_with_windows"),
        ('[suggestions]\nquick_instructions = [""]\n', "suggestions.quick_instructions"),
    ],
)
def test_invalid_new_values_name_the_key(tmp_path, body, key):
    path = tmp_path / "config.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(ConfigError) as err:
        load_settings(path)
    assert err.value.key == key


def test_save_then_load_round_trips(tmp_path):
    s = Settings(
        hotkey="ctrl+alt+r",
        insert_mode="copy",
        language="pt-BR",
        suggestion_count=2,
        model="claude-sonnet-5-5",
        input="screenshot",
        mask_sensitive_data=False,
        panel_position="top-left",
        start_with_windows=True,
        quick_instructions=("shorter", "Responder com carinho"),
        private_contacts=("Marina Duarte",),
    )
    save_settings(tmp_path / "config.toml", s)
    assert load_settings(tmp_path / "config.toml") == s


def test_save_keeps_tables_of_later_stages(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[history]\nenabled = true\n[ai]\nmode = "hybrid"\nmodel = "claude-haiku-5-5"\n',
        encoding="utf-8",
    )
    save_settings(path, Settings(suggestion_count=2))
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["history"]["enabled"] is True and data["ai"]["mode"] == "hybrid"
    assert data["suggestions"]["count"] == 2


def test_file_from_0_1_loads_unchanged(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[general]\nhotkey = "ctrl+alt+r"\n[privacy]\nprivate_contacts = ["Caio Lima"]\n',
        encoding="utf-8",
    )
    assert load_settings(path) == Settings(hotkey="ctrl+alt+r", private_contacts=("Caio Lima",))


def test_save_refuses_invalid_settings(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[history]\nenabled = true\n", encoding="utf-8")
    with pytest.raises(ConfigError) as err:
        save_settings(path, Settings(suggestion_count=4))
    assert err.value.key == "suggestions.count"
    assert path.read_text(encoding="utf-8") == "[history]\nenabled = true\n"
