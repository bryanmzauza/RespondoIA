# Architecture

```
[hotkey] -> [capture / reader] -> [llm] -> [panel] -> [paste]
                                    ^         |
                                    |         +-- selected messages + instruction -> regenerate
                  [style + knowledge + contact profile and memory]
```

## Modules

| Module | Responsibility | Built on |
|---|---|---|
| `domain` | Plain data shared by the modules: rectangles, windows, the open chat | |
| `session` | One hotkey press, from the foreground window to the pasted reply. System calls come in as ports, so the safety rules are tested without a desktop | |
| `app` | Wire tray, hotkey, panel and the worker thread that runs the session | PySide6 |
| `hotkey` | Register, change and validate the global hotkey | Win32 `RegisterHotKey` |
| `capture` | Identify the foreground window and capture its content | `pywin32`, Windows Graphics Capture or `PrintWindow` |
| `reader` | Read contact name, messages and chat bounds | UI Automation |
| `privacy` | Masking, private contacts, pre-send and pre-paste checks | |
| `context` | Assemble the prompt: chat, style, knowledge base, contact label | |
| `llm` | Call the model and parse the JSON reply | provider SDK behind a thin layer, `pydantic` |
| `local_llm` | Optional local model | llama.cpp or Ollama |
| `ui` | Panel, settings, style screen, tray | PySide6 |
| `paste` | Return focus and insert the text | clipboard + `SendInput` |
| `style` | Learn the user's writing style from their own messages | own parser + `llm` |
| `memory` | Per-contact history, gaps, memory, snippet search | `database`, FTS5 |
| `database` | Encrypted local database, key protected by DPAPI, migrations | SQLCipher |
| `contacts` | Contact records: label and last language, found by a unique name match | `database` |
| `knowledge` | The knowledge base file the user edits | |
| `config` | Settings and API key | `tomllib`, `tomli-w`, `keyring` |
| `autostart` | Start with Windows | `HKCU` Run key |
| `auth` | Product account or own key | HTTPS, Credential Manager |

## Model input

Text read through accessibility is the default input. A screenshot of the chat
is the automatic fallback when reading fails, so the app keeps working if the
WhatsApp layout changes.

The app reads the visible messages through accessibility, masks recognisable
sensitive data and sends them as JSON. Who wrote each message and which ones
are pending are worked out locally, from the position of each bubble, so the
model only writes the summary and the suggestions. The screenshot is sent when
no message could be read, and the user is told.

## Style

The style profile is built from the user's own messages only: the lines the app
reads on screen at each hotkey press, and exported chats the user imports. Lines
from other people and quotes are dropped locally. On screen, a bubble that holds
more than a single text (a forwarded message, a link preview or a card, as far
as the structure shows) is not learned; in exports, media, attachments, contact
cards and locations are left out. What is kept is masked before it is stored.
Text the app pasted is recognised by a hash and not learned back. Nothing is
kept from a chat with a private contact.

Counts (length, capitalisation, punctuation, emoji, abbreviations) and examples
are worked out locally. Examples leave out numbers, prices, links and the names
of people the app has seen. The model only describes what cannot be counted,
from a sample of the user's messages: on its own at most once a day per
language, or when the user asks for an update. There is one
profile per language, with a variation per contact label (client, supplier,
friend, family). The user's own notes about their style take precedence.

Profiles, contact records and the user's messages live in an encrypted database
under `%LOCALAPPDATA%`, with the key protected by DPAPI. If it cannot be opened,
suggestions keep working without the style.

## Model output

```json
{
  "contact": "name as shown at the top of the chat",
  "summary": "one sentence on what the other person wants",
  "messages": [
    { "id": 1, "author": "contact", "text": "Bom dia!", "pending": false },
    { "id": 2, "author": "contact", "text": "Consegue entregar até sexta?", "pending": true }
  ],
  "suggestions": [
    { "label": "Direta", "text": "..." },
    { "label": "Cordial", "text": "..." }
  ],
  "note": "optional: what could not be read or needs confirmation",
  "contact_label": "client, supplier, friend or family; only when asked, else null"
}
```

On regenerate the app sends back the selected message ids and the quick
instruction. The screen is not read again.

## Rules

These hold in every stage.

- Never simulate Enter. The user sends the message.
- Never paste if the window or the open chat changed since the capture. Leave
  the text on the clipboard and say so.
- Capture the WhatsApp window content, never a screen region.
- Read the contact name locally before any model call. It selects memory,
  label and the private-contact rule. Attach memory only on a unique match.
- Nothing is read or stored for private contacts.
- Screenshots live in memory only and are dropped when the panel closes.
- Logs hold time, latency and errors. No message content, anywhere.
- Mask recognisable sensitive data before text leaves the machine.
- Message content is data, not instructions, in every prompt.
- No message from another person is stored or sent when learning the style.
- The history loader sends scroll events only and stops on any user input or
  focus loss.
- API keys live in the Credential Manager. No shared key in the executable.
- No interface string in code. Everything goes through the translation files.
- Keep prompt, masking and quota logic separate from the interface so they can
  be reused on other platforms.

## Conventions

Code, identifiers, comments, docs, commit messages and config keys are in
English. The app ships in pt-BR and English. The system prompt has a single
version, in English, and tells the model to answer in the language of the chat.
