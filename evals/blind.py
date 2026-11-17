"""Blind review of two models' suggestions for the same fictional chats.

Usage:
    uv run python evals/blind.py <dir> [--lang pt-BR]

Reads <dir>/results.jsonl written by evals/run.py. For each case the two sets
of suggestions are shown as A and B in random order. Answers go to
<dir>/blind.jsonl, and the summary at the end names the model behind each side.
Running again continues after the last answered case.
"""

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.run import SHOTS, read_rows


def only_language(pairs: list[tuple], language: str | None) -> list[tuple]:
    if language is None:
        return pairs
    return [pair for pair in pairs if pair[0].startswith(language + "/")]


def load_results(dirs: list[Path]) -> tuple[dict, list[str]]:
    """Results keyed by (case, variant, side label), and the two labels.

    One dir with two models: the sides are the models. Two dirs with one model
    each (text and screenshot input, say): the sides are the dir names.
    """
    results = {}
    for folder in dirs:
        for row in read_rows(folder / "results.jsonl"):
            label = folder.name if len(dirs) > 1 else row["model"]
            results[(row["case"], row["variant"], label)] = row
    return results, sorted({key[2] for key in results})


def summarize_blind(rows: list[dict]) -> dict:
    models = sorted({row[side] for row in rows for side in ("a", "b")})
    base = [row for row in rows if row["variant"] == "base"]
    variants = [row for row in rows if row["variant"] != "base"]
    return {
        "preferred": {
            m: sum(1 for r in base if r["better"] in ("a", "b") and r[r["better"]] == m)
            for m in models
        },
        "ties": sum(1 for r in base if r["better"] == "e"),
        # Cases where this model broke a rule and the other one did not.
        "broke_alone": {
            m: sum(1 for r in base if len(r["broke"]) == 1 and r[r["broke"][0]] == m)
            for m in models
        },
        "complied": {
            m: sum(1 for r in variants for side in r["complied"] if r[side] == m) for m in models
        },
        "variant_cases": len(variants),
    }


def ask(prompt: str, allowed: set[str]) -> str:
    while True:
        answer = input(prompt).strip().lower()
        if answer in allowed:
            return answer


def ask_sides(prompt: str) -> list[str]:
    """Which sides: a, b, ab or n for neither."""
    answer = ask(prompt, {"a", "b", "ab", "n"})
    return [] if answer == "n" else list(answer)


def show_chat(chat: dict) -> None:
    print(f"\n{'=' * 72}\n{chat['id']}  ({chat['contact']})\n")
    for index, message in enumerate(chat["messages"], 1):
        who = "me" if message["author"] == "user" else (message["sender"] or "them")
        body = message["text"]
        if message["kind"] != "text":
            body = f"[{message['kind']}] {body}".strip()
        if message["quote"]:
            body = f"(quoting: {message['quote']}) {body}"
        print(f"  {index:>2} {who:>14}: {body}")


def show_side(name: str, row: dict) -> None:
    print(f"\n  {name}")
    for number, suggestion in enumerate(row["reply"]["suggestions"], 1):
        print(f"    {number}. [{suggestion['label']}] {suggestion['text']}")
    if row["reply"]["note"]:
        print(f"    note: {row['reply']['note']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dirs", type=Path, nargs="+", help="one dir with two models, or two dirs")
    parser.add_argument("--lang", help="review only one language, for example pt-BR")
    args = parser.parse_args()

    results, models = load_results(args.dirs)
    if len(models) != 2:
        print(f"Need exactly two sides to compare, found {len(models)}: {models}")
        return 1

    out = args.dirs[0] / "blind.jsonl"
    answered = read_rows(out)
    seed = answered[0]["seed"] if answered else random.randrange(1_000_000)
    seen = {(row["case"], row["variant"]) for row in answered}
    cases = sorted({(key[0], key[1]) for key in results})
    pairs = [c for c in cases if all((c[0], c[1], m) in results for m in models)]
    pairs = only_language(pairs, args.lang)

    for number, (case, variant) in enumerate(pairs, 1):
        if (case, variant) in seen:
            continue
        # The order depends on the seed and the case only, so a resumed run keeps it.
        order = random.Random(f"{seed}/{case}/{variant}").sample(models, 2)
        chat = json.loads((SHOTS / "chats" / f"{case}.json").read_text(encoding="utf-8"))
        show_chat(chat)
        print(f"\n  [{number}/{len(pairs)}] variant: {variant}")
        if variant == "selected":
            print(f"  reply only to message(s): {chat['regenerate']['selected']}")
        if variant == "instruction":
            print(f"  instruction: {chat['regenerate']['instruction']}")
        for name, model in zip("AB", order):
            show_side(name, results[(case, variant, model)])
        row = {"seed": seed, "case": case, "variant": variant, "a": order[0], "b": order[1]}
        print()
        if variant == "base":
            row["better"] = ask("  Closer to how the user writes? (a/b/e for even): ", {"a", "b", "e"})
            row["broke"] = ask_sides("  Who broke a rule? (a/b/ab/n for none): ")
        else:
            row["complied"] = ask_sides("  Who followed the restriction? (a/b/ab/n for none): ")
        with out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary = summarize_blind(read_rows(out))
    decided = sum(summary["preferred"].values())
    print(f"\nseed {seed}")
    for model in models:
        share = summary["preferred"][model] / decided if decided else 0
        print(
            f"{model}: preferred in {summary['preferred'][model]} ({share:.0%} of decided), "
            f"broke a rule alone in {summary['broke_alone'][model]}, "
            f"followed the restriction in {summary['complied'][model]}/{summary['variant_cases']}"
        )
    print(f"even: {summary['ties']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
