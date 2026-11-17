import json

from evals.blind import load_results, summarize_blind
from evals.run import chat_lines, done_keys, load_chats, percentile, plan_calls, summarize

MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5"]


def test_full_plan_is_56_calls_per_model():
    calls = plan_calls(load_chats(), MODELS, limit=None)
    assert len(calls) == 112
    variants = ("base", "selected", "instruction")
    per_variant = {v: sum(1 for c in calls if c[2] == v) for v in variants}
    assert per_variant == {"base": 64, "selected": 24, "instruction": 24}


def test_limit_takes_the_first_cases_in_both_languages():
    calls = plan_calls(load_chats(), MODELS, limit=3)
    assert len(calls) == 6
    assert {c[0] for c in calls} == {"en/01-short", "pt-BR/01-short", "en/02-long"}


def test_done_keys_reads_only_complete_lines(tmp_path):
    path = tmp_path / "results.jsonl"
    line = {"case": "en/01-short", "model": MODELS[0], "variant": "base"}
    path.write_text(json.dumps(line) + "\n" + '{"case": "en/02-lo', encoding="utf-8")
    assert done_keys(path) == {("en/01-short", MODELS[0], "base")}
    assert done_keys(tmp_path / "missing.jsonl") == set()


def test_percentile():
    values = [100, 200, 300, 400, 1000]
    assert percentile(values, 50) == 300 and percentile(values, 95) == 1000
    assert percentile([], 50) == 0


def test_summary_has_pass_rates_latency_tokens_and_cost():
    rows = [
        {
            "model": "m",
            "latency_ms": 1000,
            "preview_ms": 400,
            "input_tokens": 2000,
            "output_tokens": 500,
            "checks": {"count": True, "note_present": False},
        },
        {
            "model": "m",
            "latency_ms": 3000,
            "preview_ms": 1200,
            "input_tokens": 2000,
            "output_tokens": 1500,
            "checks": {"count": True},
        },
    ]
    summary = summarize(rows, {"m": {"input": 1.0, "output": 10.0}})["m"]
    assert summary["calls"] == 2
    assert summary["pass_rate"] == {"count": 1.0, "note_present": 0.0}
    assert summary["p50_ms"] == 1000 and summary["p95_ms"] == 3000
    assert summary["preview_p50_ms"] == 400 and summary["preview_p95_ms"] == 1200
    assert (summary["input_tokens"], summary["output_tokens"]) == (4000, 2000)
    assert summary["cost"] == 4000 / 1e6 * 1.0 + 2000 / 1e6 * 10.0
    assert "cost" not in summarize(rows, {})["m"]


def test_blind_summary_names_the_models_behind_a_and_b():
    rows = [
        {"variant": "base", "a": "h", "b": "s", "better": "a", "broke": ["b"]},
        {"variant": "base", "a": "s", "b": "h", "better": "a", "broke": []},
        {"variant": "base", "a": "h", "b": "s", "better": "e", "broke": ["a", "b"]},
        {"variant": "selected", "a": "h", "b": "s", "complied": ["a"]},
    ]
    summary = summarize_blind(rows)
    assert summary["preferred"] == {"h": 1, "s": 1} and summary["ties"] == 1
    assert summary["broke_alone"] == {"h": 0, "s": 1}
    assert summary["complied"] == {"h": 1, "s": 0} and summary["variant_cases"] == 1


def test_blind_review_can_be_limited_to_one_language():
    from evals.blind import only_language

    pairs = [("en/01-short", "base"), ("pt-BR/01-short", "base"), ("pt-BR/09-price", "selected")]
    assert only_language(pairs, "pt-BR") == pairs[1:]
    assert only_language(pairs, None) == pairs


def chat(chat_id):
    return next(c for c in load_chats() if c["id"] == chat_id)


def test_chat_lines_follow_the_fixture():
    group = chat_lines(chat("pt-BR/03-group"))
    assert group[0].sender == "Bia Rocha" and group[0].author == "contact"
    assert {line.sender for line in group if line.author == "user"} == {""}
    image = chat_lines(chat("pt-BR/05-image"))
    assert [line.kind for line in image].count("image") == 1
    quoted = chat_lines(chat("pt-BR/07-quoted"))
    assert quoted[-1].quote == "passo sim, posso chegar aí umas 18h"
    assert all(line.time for line in quoted)


def test_blind_with_two_dirs_labels_sides_by_dir(tmp_path):
    for name, model in (("text", "claude-haiku-5-5"), ("shot", "claude-haiku-5-5")):
        folder = tmp_path / name
        folder.mkdir()
        row = {"case": "pt-BR/01-short", "model": model, "variant": "base", "reply": {}}
        (folder / "results.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    results, labels = load_results([tmp_path / "text", tmp_path / "shot"])
    assert labels == ["shot", "text"]
    assert set(results) == {("pt-BR/01-short", "base", "text"), ("pt-BR/01-short", "base", "shot")}


def test_blind_with_one_dir_labels_sides_by_model(tmp_path):
    rows = [
        {"case": "pt-BR/01-short", "model": m, "variant": "base", "reply": {}}
        for m in ("claude-haiku-5-5", "claude-sonnet-5-5")
    ]
    (tmp_path / "results.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    results, labels = load_results([tmp_path])
    assert labels == ["claude-haiku-5-5", "claude-sonnet-5-5"] and len(results) == 2


def test_style_set_loads_and_builds_the_context():
    from evals.run import style_context
    from respondoia.style.profile import StyleProfile
    from respondoia.style.sampler import StyleStats

    chats = load_chats("style")
    assert len(chats) == 20 and all(c["id"].split("/")[1][:2].isdigit() for c in chats)
    chat = next(c for c in chats if c["id"] == "pt-BR/09-knowledge-price")
    profile = StyleProfile(language="pt", messages=1, stats=StyleStats(messages=1),
                           examples=["fechado então"], updated_at="2026-11-17T10:00:00")
    context = style_context(chat, profile, "Pão: R$ 18")
    assert context.label == "client" and context.label_from_user and not context.ask_label
    assert "fechado então" in context.style and context.knowledge == "Pão: R$ 18"


def test_without_profile_the_style_is_empty():
    from evals.run import style_context

    chat = next(c for c in load_chats("style") if c["id"] == "pt-BR/01-friend-plan")
    context = style_context(chat, None, "")
    assert context.style == "" and context.knowledge == "" and context.label == "friend"


def test_only_filters_cases():
    from evals.run import only_cases

    chats = load_chats()
    assert [c["id"] for c in only_cases(chats, ["en/14-nothing-pending"])] == ["en/14-nothing-pending"]
    assert only_cases(chats, None) == chats


def test_blind_finds_style_chats():
    from evals.blind import chat_file

    assert chat_file("pt-BR/01-friend-plan").parent.name == "pt-BR"
    assert chat_file("pt-BR/01-short").exists()


def test_persona_messages_are_only_the_personas():
    from collections import Counter

    from evals.persona import persona_messages

    for lang in ("pt-BR", "en"):
        messages = persona_messages(lang)
        assert "ZZ-OTHER" not in repr(messages)
        assert all(count >= 25 for count in Counter(m.label for m in messages).values())
