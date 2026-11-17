import pytest

from respondoia import autostart


def test_command_line_runs_the_package_without_a_console():
    command = autostart.command_line()
    assert command.endswith(" -m respondoia") and command.startswith('"')
    assert "pythonw.exe" in command.lower() or "python.exe" in command.lower()


@pytest.mark.desktop
def test_autostart_round_trip():
    import winreg

    key = r"Software\RespondoIA-test\Run"
    try:
        assert not autostart.is_enabled(key)
        autostart.set_enabled(True, key)
        assert autostart.is_enabled(key)
        autostart.set_enabled(False, key)
        assert not autostart.is_enabled(key)
        autostart.set_enabled(False, key)  # turning off twice is fine
    finally:
        for path in (key, r"Software\RespondoIA-test"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
            except FileNotFoundError:
                pass
