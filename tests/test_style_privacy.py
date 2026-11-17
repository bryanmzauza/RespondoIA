"""No message from another person reaches the model or the saved style. No API call."""

import json
import logging
from pathlib import Path

import pytest

from respondoia.domain import ChatLine
from respondoia.llm import ExtractResult
from respondoia.style.parser import parse_export
from respondoia.style.profile import StyleTraits
from respondoia.style.service import StyleService

HERE = Path(__file__).parent
PERSONAS = json.loads((HERE / "style" / "personas.json").read_text(encoding="utf-8"))
LANGUAGE = {"pt-BR": "pt", "en": "en"}
TRAITS = StyleTraits(
    formality="x", greetings=[], farewells=[], expressions=[], avoids=[], habits=[], by_label=[]
)


class RecordingClient:
    def __init__(self):
        self.texts = []

    def extract(self, system, text, schema):
        self.texts.append(system + text)
        return ExtractResult(TRAITS, "claude-haiku-5-5", 1, 1, 1)


def dump(db):
    with db.transaction() as conn:
        rows = []
        for table in ("contact", "style_sample", "style_profile", "pasted"):
            rows += [repr(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
    return "\n".join(rows)


@pytest.mark.parametrize("lang", ["pt-BR", "en"])
def test_style_privacy_end_to_end(lang, db, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="respondoia")
    persona = PERSONAS[lang]
    client = RecordingClient()
    service = StyleService(db, lambda: client, tmp_path / "knowledge.md")
    for label, name in persona["label_files"].items():
        chat = parse_export((HERE / "exports" / name).read_bytes(), name)
        result = service.import_export(chat, persona["author"], label)
        assert result.added > 0
    service.refresh(LANGUAGE[lang])
    screen = (
        ChatLine("contact", "ZZ-OTHER-SENDER", "09:00", "text", "ZZ-OTHER-SCREEN tudo bem?", ""),
        ChatLine("user", "", "09:01", "text", persona["reference"][0], "ZZ-OTHER-QUOTE"),
        ChatLine("user", "", "09:02", "text", "ZZ-OTHER-FORWARDED", "", plain=False),
    )
    service.learn("Grupo da família", screen, is_group=True)

    stored = dump(db)
    assert client.texts and "ZZ-OTHER" not in "".join(client.texts)
    assert "ZZ-OTHER" not in stored and "ZZ-OTHER" not in caplog.text
    for reference in persona["reference"]:
        assert reference in stored
