import json

from evals.blind import summarize_blind
from evals.run import done_keys, load_chats, percentile, plan_calls, summarize

MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5"]


def test_full_plan_is_54_calls_per_model():
    calls = plan_calls(load_chats(), MODELS, limit=None)
    assert len(calls) == 108
    variants = ("base", "selected", "instruction")
    per_variant = {v: sum(1 for c in calls if c[2] == v) for v in variants}
    assert per_variant == {"base": 60, "selected": 24, "instruction": 24}


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
