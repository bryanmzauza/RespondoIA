"""The fictional history cases: build what the app would attach to each request.

Each case holds weeks of a fictional chat ("history") and what is on screen now
("messages"). The history goes into a throwaway encrypted database through the same
service the app uses; the visible chat is then observed as a hotkey press would.

Build the contact memories (costs one call per 200 messages of history):
    uv run python evals/history.py --lang pt-BR --out <dir>
writes <dir>/<case name>.memory.txt and prints counts and tokens, never texts.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from respondoia import contacts
from respondoia.config import Settings, get_api_key
from respondoia.database import Database
from respondoia.domain import ChatLine, UserContext
from respondoia.llm import AnthropicClient
from respondoia.memory import store
from respondoia.memory.recall import snippet_rows
from respondoia.memory.service import HistoryService

HISTORY = ROOT / "tests" / "history"
ON = Settings(history_enabled=True)
LANGUAGE = {"pt-BR": "pt", "en": "en"}


def load_cases() -> list[dict]:
    return sorted(
        (json.loads(p.read_text(encoding="utf-8")) for p in HISTORY.glob("*/*.json")),
        key=lambda case: case["id"],
    )


def case_lines(rows) -> tuple[ChatLine, ...]:
    return tuple(
        ChatLine(
            author="user" if row["author"] == "user" else "contact",
            sender=row.get("sender") or "",
            time=row["time"],
            kind=row.get("kind", "text"),
            text=row["text"],
            quote=row.get("quote") or "",
            day=row.get("day", ""),
        )
        for row in rows
    )


class _Database:
    """A throwaway encrypted database in a temporary folder."""

    def __enter__(self) -> Database:
        self._folder = tempfile.TemporaryDirectory()
        self._db = Database.open(Path(self._folder.name) / "history.db", os.urandom(32))
        return self._db

    def __exit__(self, *exc) -> None:
        self._db.close()
        self._folder.cleanup()


def _stored(db: Database, case: dict, client=None) -> tuple[HistoryService, int]:
    """The case's history, stored as a load would store it."""
    history = HistoryService(db, lambda: client, lambda: ON)
    loaded = history.store_loaded(case["contact"], case_lines(case["history"]), True, "")
    if loaded.state != "ready":
        raise RuntimeError(f"history not stored: {loaded.state}")
    return history, loaded.contact_id


def history_context(
    case: dict, memory: str | None = None, use_history: bool = True
) -> tuple[UserContext, str]:
    """The request context for the visible chat, and the history text sent with it (for the
    checks that accept figures from the history)."""
    language = LANGUAGE[case["language"]]
    empty = UserContext(language, None, True, False, "", "")
    if not use_history:
        return empty, ""
    with _Database() as db:
        history, contact_id = _stored(db, case)
        if memory:
            with db.transaction() as conn:
                store.save_memory(conn, contact_id, memory, 10**9, "2026-11-17T09:00:00")
        visible = case_lines(case["messages"])
        with db.transaction() as conn:
            contacts.see(conn, case["contact"], False, None, "2026-11-17T09:00:00")
        observed = history.observe(case["contact"], visible, False, "")
    if observed.state != "ready":
        return empty, ""
    rows = snippet_rows(observed.snippets)
    sent = " ".join([observed.memory, *(row["text"] for row in rows)]).strip()
    context = UserContext(language, None, True, False, "", "", observed.memory, rows)
    return context, sent


def build_memory(case: dict, client) -> str:
    with _Database() as db:
        history, contact_id = _stored(db, case, client)
        if not history.update_memory_if_due(contact_id):
            raise RuntimeError("memory not built")
        return history.memory(contact_id)


class _Counting:
    """Passes calls on and adds up the tokens."""

    def __init__(self, client) -> None:
        self._client = client
        self.calls = self.tokens_in = self.tokens_out = 0

    def extract(self, system, text, schema):
        result = self._client.extract(system, text, schema)
        self.calls += 1
        self.tokens_in += result.input_tokens
        self.tokens_out += result.output_tokens
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lang", choices=sorted(LANGUAGE), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="claude-haiku-5-5")
    args = parser.parse_args()
    key = get_api_key()
    if not key:
        print("No API key stored. Run: uv run python -m respondoia --set-api-key")
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    client = _Counting(AnthropicClient(key, args.model, timeout_s=60.0))
    for case in load_cases():
        if case["language"] != args.lang:
            continue
        name = case["id"].split("/")[-1]
        memory = build_memory(case, client)
        (args.out / f"{name}.memory.txt").write_text(memory, encoding="utf-8")
        print(f"{name}: {len(case['history'])} messages, memory {len(memory)} characters")
    print(f"calls {client.calls}, tokens in {client.tokens_in}, out {client.tokens_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
