from respondoia import knowledge
from respondoia.knowledge import create_knowledge, read_knowledge


def test_missing_or_blank_is_empty(tmp_path):
    assert read_knowledge(tmp_path / "knowledge.md") == ""
    (tmp_path / "knowledge.md").write_text("  \n\n", encoding="utf-8")
    assert read_knowledge(tmp_path / "knowledge.md") == ""


def test_text_is_read_as_written(tmp_path):
    (tmp_path / "knowledge.md").write_text("# Padaria\n\n- Pão: R$ 18\n", encoding="utf-8")
    assert read_knowledge(tmp_path / "knowledge.md") == "# Padaria\n\n- Pão: R$ 18"


def test_long_file_is_cut(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "MAX_CHARS", 10)
    (tmp_path / "knowledge.md").write_text("0123456789abcdef", encoding="utf-8")
    assert read_knowledge(tmp_path / "knowledge.md") == "0123456789"


def test_create_never_overwrites(tmp_path):
    path = tmp_path / "knowledge.md"
    assert create_knowledge(path, "# Template")
    path.write_text("mine", encoding="utf-8")
    assert not create_knowledge(path, "# Template")
    assert path.read_text(encoding="utf-8") == "mine"
