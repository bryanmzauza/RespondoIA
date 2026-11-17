"""Start with Windows, through the current user's Run key."""

import sys
import winreg
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "RespondoIA"


def command_line() -> str:
    """The command Windows runs at sign-in: the package, without a console window."""
    python = Path(sys.executable)
    windowless = python.with_name("pythonw.exe")
    runner = windowless if windowless.exists() else python
    return f'"{runner}" -m respondoia'


def is_enabled(key_path: str = RUN_KEY) -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
    except FileNotFoundError:
        return False
    return True


def set_enabled(enabled: bool, key_path: str = RUN_KEY) -> None:
    if enabled:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command_line())
        return
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass
