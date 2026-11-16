"""Entry point: starts the tray icon and registers the global hotkey."""

import argparse
import getpass
import sys
from collections.abc import Sequence

from respondoia import config


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="respondoia")
    parser.add_argument(
        "--set-api-key",
        action="store_true",
        help="store the model API key in the Windows Credential Manager and exit",
    )
    args = parser.parse_args(argv)
    if args.set_api_key:
        key = getpass.getpass("API key: ").strip()
        if not key:
            print("No key entered. Nothing was stored.", file=sys.stderr)
            return 1
        config.set_api_key(key)
        print("API key stored in the Windows Credential Manager.")
        return 0
    # Imported here so that the command above does not load Qt.
    from respondoia import app

    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
