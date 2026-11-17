"""The knowledge base: facts the user wants suggestions to use, in a file they edit.

It is the user's own text meant to be told to customers, so it is not masked.
"""

from pathlib import Path

from respondoia.config import app_dir

KNOWLEDGE_FILE = "knowledge.md"
MAX_CHARS = 20_000


def knowledge_path() -> Path:
    return app_dir() / KNOWLEDGE_FILE


def read_knowledge(path: Path) -> str:
    """"" if missing or blank; at most MAX_CHARS characters."""
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except FileNotFoundError:
        return ""
    return text.strip()[:MAX_CHARS]


def create_knowledge(path: Path, template: str) -> bool:
    """Write the template only when there is no file yet. True if written."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template, encoding="utf-8")
    return True
