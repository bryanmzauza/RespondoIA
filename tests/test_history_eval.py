"""The fictional history cases and how the evaluation builds their context, without the API."""

import pytest

from evals.checks import check_case
from evals.history import case_lines, history_context, load_cases
from respondoia.llm import ChatMessage, Suggestion, SuggestionReply

CASES = {case["id"]: case for case in load_cases()}
VALUE_CASES = [cid for cid, case in CASES.items() if case["expect"]["history_value"]]
NAMESAKES = [cid for cid, case in CASES.items() if case["expect"]["absent"]]


def reply(case, texts):
    messages = [
        ChatMessage(
            id=i, author=m["author"], text=m["text"], pending=i in case["expect"]["pending"]
        )
        for i, m in enumerate(case["messages"], 1)
    ]
    return SuggestionReply(
        summary="resumo",
        suggestions=[Suggestion(label=f"o{i}", text=t) for i, t in enumerate(texts)],
        contact=case["contact"],
        messages=messages,
    )


def test_history_cases_follow_the_schema():
    assert len(CASES) == 10 and len(VALUE_CASES) == 6 and len(NAMESAKES) == 2
    for case in CASES.values():
        assert len(case["history"]) >= 120
        assert {"pending", "needs_confirm", "note_expected", "canary"} <= set(case["expect"])
        value = case["expect"]["history_value"] or case["expect"]["absent"]
        if value:
            assert any(value in row["text"] for row in case["history"]), case["id"]
            assert not any(value in m["text"] for m in case["messages"]), case["id"]


@pytest.mark.parametrize("case_id", VALUE_CASES)
def test_history_set_builds_snippets_locally(case_id):
    case = CASES[case_id]
    context, known = history_context(case, memory="ZZ-MEMORY")
    texts = " ".join(row["text"] for row in context.snippets)
    assert case["expect"]["history_value"] in texts
    assert context.memory == "ZZ-MEMORY" and case["expect"]["history_value"] in known


@pytest.mark.parametrize("case_id", NAMESAKES)
def test_wrong_contact_case_attaches_nothing_from_the_namesake(case_id):
    case = CASES[case_id]
    context, known = history_context(case, memory="kit festa: " + case["expect"]["absent"])
    assert context.memory == "" and context.snippets == ()
    assert case["expect"]["absent"] not in str(context) and known == ""


def test_without_history_nothing_is_attached():
    case = CASES[VALUE_CASES[0]]
    context, known = history_context(case, memory="ZZ-MEMORY", use_history=False)
    assert context.memory == "" and context.snippets == () and known == ""


def test_case_lines_carry_the_day():
    case = CASES[VALUE_CASES[0]]
    lines = case_lines(case["history"])
    assert lines[0].day == case["history"][0]["day"] and lines[0].kind == "text"


def test_runner_loads_the_history_set(monkeypatch, capsys, tmp_path):
    from evals import run

    assert len(run.load_chats("history")) == 10
    monkeypatch.setattr(
        "sys.argv",
        [
            "run.py",
            "--models",
            "m",
            "--out",
            str(tmp_path),
            "--input",
            "text",
            "--set",
            "history",
            "--dry-run",
        ],
    )
    assert run.main() == 0 and "10 calls planned" in capsys.readouterr().out


def test_runner_builds_the_history_context(tmp_path):
    from evals import run

    case = CASES[VALUE_CASES[0]]
    (tmp_path / f"{case['id'].split('/')[-1]}.memory.txt").write_text("ZZ-MEM", encoding="utf-8")
    context, sent = run.history_case_context(case, tmp_path, use_history=True)
    assert context.memory == "ZZ-MEM" and "ZZ-MEM" in sent
    empty, nothing = run.history_case_context(case, tmp_path, use_history=False)
    assert empty.memory == "" and nothing == ""


def test_history_used_check():
    case = CASES[VALUE_CASES[0]]
    value = case["expect"]["history_value"]
    good = check_case(case, reply(case, [f"fica {value}", f"{value} sim", "oi"]), 3, history=value)
    assert good["history_used"] and good["no_invented_numbers"]
    bad = check_case(case, reply(case, [f"fica {value}", "oi", "oi"]), 3, history=value)
    assert not bad["history_used"]


def test_wrong_contact_check():
    case = CASES[NAMESAKES[0]]
    absent = case["expect"]["absent"]
    leaked = check_case(case, reply(case, [f"fica {absent}", "oi", "oi"]), 3)
    assert not leaked["wrong_contact"]
    clean = check_case(case, reply(case, ["[confirmar: valor]", "oi", "oi"]), 3)
    assert clean["wrong_contact"] and clean["confirm_marker"]
