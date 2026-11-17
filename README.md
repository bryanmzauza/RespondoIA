# RespondoIA

Tray app for Windows that suggests replies while you chat on WhatsApp Desktop.
Press a hotkey with a chat open, pick one of the suggestions and it lands in
the message box. You read it and press Enter yourself.

The app does not connect to WhatsApp, inject code into it or use unofficial
libraries. It reads what is on screen and pastes text. It never sends a
message for you.

**Status:** early development. Stage 0.2 is implemented and under test: the
chat is read as text and masked before it is sent, with a screenshot as the
fallback, and there is a settings window. It needs your own API key. The plan
is in [docs/ROADMAP.md](docs/ROADMAP.md).

## What it is meant to do

- Suggest two or three replies for the open chat in under 5 seconds.
- Write the way you write, with a style profile learned from your own messages.
- Remember context per contact, if you turn history on. It stays encrypted on
  your computer.
- Offer a local mode where nothing leaves the machine.
- Run in Portuguese (pt-BR) and English. Suggestions follow the language of
  the chat, not of the interface.

## Daily use

1. Open a chat in WhatsApp Desktop or WhatsApp Web.
2. Press the hotkey (default `Ctrl+Shift+Space`).
3. Press `1`, `2` or `3` to pick a suggestion.
4. Review the text and press Enter.

`Esc` closes the panel without pasting anything.

## Development

Requires Windows 10 or 11, Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```
uv sync
uv run pytest
```

`uv.lock` pins the version of every dependency.

To run the app, store your model API key and start it:

```
uv run python -m respondoia --set-api-key
uv run python -m respondoia
```

The key goes to the Windows Credential Manager, never to a file. The app sits
in the tray; right-click the icon and choose Settings to change the hotkey,
the model, the key and the rest. Set a monthly spend limit on the provider
account before use.

`config.example.toml` lists every setting. Module responsibilities and the
rules the code must follow are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

Test fixtures must be fictional. Do not commit screenshots or exports of real
conversations, a real `config.toml`, a real `context/knowledge.md` or any
database file.

## Repository layout

```
src/respondoia/   application package
  style/          style learning: export parser, sampling, profile
  memory/         per-contact history and memory
  ui/             panel, settings, style screen, tray
  i18n/           interface translations
context/          example knowledge base
docs/             roadmap and architecture
evals/            model comparison scripts
server/           relay server for product accounts
tests/            tests and fictional fixtures
```

## Disclaimer

RespondoIA is an independent project. It is not affiliated with, endorsed by
or connected to WhatsApp or Meta.
