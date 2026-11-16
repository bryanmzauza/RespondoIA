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
| `hotkey` | Register, change and validate the global hotkey | Win32 `RegisterHotKey` |
| `capture` | Identify the foreground window and capture its content | `pywin32`, Windows Graphics Capture or `PrintWindow` |
| `reader` | Read contact name, messages and chat bounds | UI Automation |
| `privacy` | Masking, private contacts, pre-send and pre-paste checks | |
| `context` | Assemble the prompt | local database, `knowledge.md` |
| `llm` | Call the model and parse the JSON reply | provider SDK behind a thin layer, `pydantic` |
| `local_llm` | Optional local model | llama.cpp or Ollama |
| `ui` | Panel, settings, style screen, tray | PySide6 |
| `paste` | Return focus and insert the text | clipboard + `SendInput` |
| `style` | Learn the user's writing style | own parser + `llm` |
| `memory` | Per-contact history, gaps, memory, snippet search | encrypted SQLite, FTS5 |
| `config` | Settings and API key | `tomllib`, `tomli-w`, `keyring` |
| `auth` | Product account or own key | HTTPS, Credential Manager |

## Model input

Text read through accessibility is the default input. A screenshot of the chat
is the automatic fallback when reading fails, so the app keeps working if the
WhatsApp layout changes.

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
  "note": "optional: what could not be read or needs confirmation"
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
