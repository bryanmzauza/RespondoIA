Screenshots of fictional chats, in pt-BR and English.

Never add a capture of a real conversation. Use made-up names, numbers and
messages.

The JSON files in `chats/` are the source. Each one holds the messages, the
expected pending messages and, for some scenarios, a regenerate request. The
PNGs are rendered from them with `template.html` and headless Edge:

```
uv run python tests/screenshots/render.py
uv run python tests/screenshots/render.py --only pt-BR/01-short
```

Edit the JSON, render again and commit both files.
