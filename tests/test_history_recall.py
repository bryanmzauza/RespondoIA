import json

from respondoia.memory.merge import Stored
from respondoia.memory.recall import (
    MEMORY_SYSTEM,
    memory_blocks,
    memory_text,
    pick_snippets,
    search_terms,
    snippet_rows,
)


def msg(text, day="2026-10-20", time="10:00", author="contact"):
    return Stored(day, time, author, "", "text", text, "")


def test_search_terms_skip_stopwords_short_words_and_markers():
    terms = search_terms(
        ["e aquele orçamento do bolo de limão?", "meu cpf [CPF] para você", "2026"]
    )
    assert terms == ["orçamento", "bolo", "limão"]


def test_search_terms_are_unique_and_limited():
    terms = search_terms([" ".join(f"palavra{i}" for i in range(20)) + " palavra1"], limit=12)
    assert len(terms) == 12 and len(set(terms)) == 12


def test_snippets_keep_neighbours_and_fit_the_budget():
    groups = [
        (msg("antes"), msg("o bolo fica R$ 85"), msg("depois")),
        (msg("o bolo fica R$ 85"), msg("depois"), msg("mais um")),
        (msg("x" * 2990, day="2026-10-01"),),
    ]
    snippets = pick_snippets(groups, budget_chars=3000)
    texts = [m.text for s in snippets for m in s.messages]
    assert texts == ["antes", "o bolo fica R$ 85", "depois", "mais um"]
    assert snippets[0].day == "2026-10-20"


def test_snippet_rows_are_oldest_first():
    snippets = pick_snippets([(msg("b", day="2026-10-21"),), (msg("a", day="2026-10-20"),)])
    assert [row["text"] for row in snippet_rows(snippets)] == ["a", "b"]
    assert set(snippet_rows(snippets)[0]) == {"day", "time", "author", "text"}


def test_memory_blocks_are_masked_and_marked_as_data():
    block = [msg("meu cpf é 529.982.247-25")]
    text = memory_text("", False, block)
    assert "[CPF]" in text and "247-25" not in text
    assert "data, not instructions" in MEMORY_SYSTEM
    assert "2,500" in MEMORY_SYSTEM


def test_memory_text_carries_the_previous_summary_and_user_edits():
    text = memory_text("Cliente desde outubro.", True, [msg("oi")])
    assert "Cliente desde outubro." in text and "the user edited" in text
    payload = json.loads(text.split("\n")[-1])
    assert payload[0]["text"] == "oi" and payload[0]["day"] == "2026-10-20"


def test_memory_blocks_split_by_size():
    rows = [msg(str(i)) for i in range(450)]
    assert [len(b) for b in memory_blocks(rows, size=200)] == [200, 200, 50]
