"""Run the fictional chats through one or more models and check the replies.

Usage:
    uv run python evals/run.py --models <id> [<id> ...] --out <dir>
        [--input screenshot|text] [--limit N] [--dry-run]
        [--set screenshots|style|history] [--profile-dir <dir>] [--only <case id> ...]
        [--memory-dir <dir>] [--no-history]

The style set (tests/style/chats) runs in text mode only. With --profile-dir, each
case gets the persona profile <dir>/<lang>.json as its style; without it, no style.
Cases marked "knowledge" get tests/style/knowledge-<lang>.md.

The history set (tests/history) runs in text mode only. Each case's history is stored
in a throwaway database and the visible chat observed as a hotkey press would, so the
request carries the earlier messages the app finds; with --memory-dir, also the memory
<dir>/<case name>.memory.txt built by evals/history.py. --no-history sends neither.

With --input text the chat goes as masked text built from the fixture JSON, the
way the app sends what it reads through accessibility.

Every case runs the "base" variant. Cases with a regenerate block also run
"selected" (only the selected ids) and "instruction" (only the instruction),
both reusing the message list of the base reply.

Results go to <dir>/results.jsonl, one line per finished call, and failures to
<dir>/errors.jsonl. Running again skips what is already in results.jsonl.
If <dir>/../prices.toml exists (per model: input and output price per million
tokens), the summary includes the cost.
"""

import argparse
import json
import sys
import threading
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.checks import check_case
from evals.history import HISTORY, history_context
from respondoia.config import get_api_key
from respondoia.domain import ChatLine, UserContext
from respondoia.llm import (
    AnthropicClient,
    LlmError,
    SuggestionReply,
    SuggestRequest,
)
from respondoia.privacy import mask_lines
from respondoia.style.profile import StyleProfile, render

SHOTS = ROOT / "tests" / "screenshots"
STYLE = ROOT / "tests" / "style"
COUNT = 3
WORKERS = 4
# One retry inside the client, so a case takes at most twice this.
CALL_TIMEOUT_S = 30.0
VARIANTS = ("base", "selected", "instruction")


class ServedModelMismatch(RuntimeError):
    pass


def load_chats(chat_set: str = "screenshots") -> list[dict]:
    folders = {"screenshots": SHOTS / "chats", "style": STYLE / "chats", "history": HISTORY}
    folder = folders[chat_set]
    chats = [json.loads(p.read_text(encoding="utf-8")) for p in folder.glob("*/*.json")]
    # Scenario first, then language, so a small --limit still covers both languages.
    return sorted(chats, key=lambda chat: tuple(reversed(chat["id"].split("/"))))


def chat_lines(chat: dict) -> tuple[ChatLine, ...]:
    """The fixture as the reader would give it."""
    return tuple(
        ChatLine(
            author="user" if m["author"] == "user" else "contact",
            sender=m["sender"] or "",
            time=m["time"],
            kind=m["kind"],
            text=m["text"],
            quote=m["quote"] or "",
        )
        for m in chat["messages"]
    )


def only_cases(chats: list[dict], ids: list[str] | None) -> list[dict]:
    return chats if not ids else [chat for chat in chats if chat["id"] in ids]


def style_context(chat: dict, profile: StyleProfile | None, knowledge: str) -> UserContext:
    """What the app would add for this case: the persona's style and the knowledge base."""
    label = chat.get("label")
    style = render(profile, label, "") if profile is not None else ""
    return UserContext(
        profile.language if profile else None,
        label,
        True,
        False,
        style,
        knowledge if chat.get("knowledge") else "",
    )


def history_case_context(
    chat: dict, memory_dir: Path | None, use_history: bool
) -> tuple[UserContext, str]:
    """The memory (if built) and earlier messages the app would attach to this case."""
    memory = None
    if memory_dir is not None:
        path = memory_dir / f"{chat['id'].split('/')[-1]}.memory.txt"
        memory = path.read_text(encoding="utf-8") if path.exists() else None
    return history_context(chat, memory, use_history)


def plan_calls(chats: list[dict], models: list[str], limit: int | None) -> list[tuple]:
    calls = []
    for chat in chats[:limit]:
        variants = VARIANTS if "regenerate" in chat else VARIANTS[:1]
        calls += [(chat["id"], model, variant) for model in models for variant in variants]
    return calls


def read_rows(path: Path) -> list[dict]:
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a line cut by an interrupted run
    return rows


def done_keys(path: Path) -> set[tuple]:
    return {(row["case"], row["model"], row["variant"]) for row in read_rows(path)}


def percentile(values: list[int], pct: int) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))]


def summarize(rows: list[dict], prices: dict) -> dict:
    summary = {}
    for model in sorted({row["model"] for row in rows}):
        mine = [row for row in rows if row["model"] == model]
        latencies = [row["latency_ms"] for row in mine]
        # Time until summary and suggestions were complete: what the panel waits for.
        previews = [row["preview_ms"] for row in mine if row.get("preview_ms") is not None]
        names = sorted({name for row in mine for name in row["checks"]})
        entry = {
            "calls": len(mine),
            "pass_rate": {
                name: sum(row["checks"][name] for row in mine if name in row["checks"])
                / sum(1 for row in mine if name in row["checks"])
                for name in names
            },
            "p50_ms": percentile(latencies, 50),
            "p95_ms": percentile(latencies, 95),
            "preview_p50_ms": percentile(previews, 50),
            "preview_p95_ms": percentile(previews, 95),
            "input_tokens": sum(row["input_tokens"] for row in mine),
            "output_tokens": sum(row["output_tokens"] for row in mine),
        }
        if model in prices:
            entry["cost"] = (
                entry["input_tokens"] / 1e6 * prices[model]["input"]
                + entry["output_tokens"] / 1e6 * prices[model]["output"]
            )
        summary[model] = entry
    return summary


def print_summary(summary: dict) -> None:
    for model, entry in summary.items():
        print(f"\n{model}: {entry['calls']} calls")
        for name, rate in entry["pass_rate"].items():
            print(f"  {name:<22}{rate:6.0%}")
        print(f"  latency p50 {entry['p50_ms']} ms, p95 {entry['p95_ms']} ms")
        print(
            f"  options shown p50 {entry['preview_p50_ms']} ms, "
            f"p95 {entry['preview_p95_ms']} ms"
        )
        print(f"  tokens in {entry['input_tokens']}, out {entry['output_tokens']}")
        if "cost" in entry:
            per_call = entry["cost"] / entry["calls"]
            print(f"  cost {entry['cost']:.4f} total, {per_call:.5f} per call")


class Runner:
    def __init__(
        self,
        out: Path,
        api_key: str,
        input_mode: str = "screenshot",
        profiles: dict[str, StyleProfile] | None = None,
        chat_set: str = "screenshots",
        memory_dir: Path | None = None,
        use_history: bool = True,
    ) -> None:
        self.memory_dir = memory_dir
        self.use_history = use_history
        self.input_mode = input_mode
        self.profiles = profiles  # by chat language; None: the cases get no style
        self.chat_set = chat_set
        self.results = out / "results.jsonl"
        self.errors = out / "errors.jsonl"
        self.api_key = api_key
        self.lock = threading.Lock()
        self.done = {
            (row["case"], row["model"], row["variant"]): row for row in read_rows(self.results)
        }

    def append(self, path: Path, row: dict) -> None:
        with self.lock, path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def run_chain(self, chat: dict, model: str) -> None:
        """Base first, then the variants that reuse its message list."""
        client = AnthropicClient(self.api_key, model, timeout_s=CALL_TIMEOUT_S)
        image = None
        if self.input_mode != "text":
            image = (SHOTS / f"{chat['id']}.png").read_bytes()
        lines, _ = mask_lines(chat_lines(chat))
        context, knowledge, examples, history = None, "", (), ""
        if self.chat_set == "history":
            context, history = history_case_context(chat, self.memory_dir, self.use_history)
        if self.chat_set == "style":
            knowledge_file = STYLE / f"knowledge-{chat['language']}.md"
            knowledge = knowledge_file.read_text(encoding="utf-8").strip()
            profile = (self.profiles or {}).get(chat["language"])
            context = style_context(chat, profile, knowledge)
            if profile is not None:
                examples = tuple(profile.examples) + tuple(
                    e for items in profile.examples_by_label.values() for e in items
                )
            knowledge = context.knowledge
        base: SuggestionReply | None = None
        for variant in VARIANTS if "regenerate" in chat else VARIANTS[:1]:
            key = (chat["id"], model, variant)
            if key in self.done:
                if variant == "base":
                    base = SuggestionReply.model_validate(self.done[key]["reply"])
                continue
            if variant != "base" and base is None:
                return  # the base call failed; its error is already recorded
            selected, instruction = (), ""
            if variant == "selected":
                selected = tuple(chat["regenerate"]["selected"])
            if variant == "instruction":
                instruction = chat["regenerate"]["instruction"]
            if self.input_mode == "text":
                request = SuggestRequest(
                    None, COUNT, None, selected, instruction, lines, context=context
                )
            else:
                previous = None if variant == "base" else base
                request = SuggestRequest(image, COUNT, previous, selected, instruction)
            try:
                result = client.suggest(request)
            except LlmError as exc:  # recorded by class and code, the run goes on
                cause = type(exc.__cause__).__name__ if exc.__cause__ else "LlmError"
                row = {"case": key[0], "model": model, "variant": variant}
                self.append(self.errors, {**row, "error": cause, "code": exc.code})
                print(f"  failed {key[0]} {model} {variant}: {cause} {exc.code}")
                continue
            if not result.model.startswith(model):
                raise ServedModelMismatch(f"asked for {model}, served {result.model}")
            row = {
                "case": key[0],
                "model": model,
                "variant": variant,
                "input": self.input_mode,
                "set": self.chat_set,
                "profile": bool(context and context.style),
                "history": bool(context and (context.memory or context.snippets)),
                "served_model": result.model,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "latency_ms": result.latency_ms,
                "preview_ms": result.preview_ms,
                "checks": check_case(
                    chat,
                    result.reply,
                    COUNT,
                    instruction=instruction,
                    knowledge=knowledge,
                    examples=examples,
                    history=history,
                ),
                "reply": result.reply.model_dump(),
            }
            self.append(self.results, row)
            if variant == "base":
                base = result.reply
            print(f"  done {key[0]} {model} {variant} {result.latency_ms} ms")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--input", choices=["screenshot", "text"], default="screenshot")
    parser.add_argument("--limit", type=int, help="run only the first N cases")
    parser.add_argument("--dry-run", action="store_true", help="count the calls and stop")
    parser.add_argument(
        "--set", choices=["screenshots", "style", "history"], default="screenshots"
    )
    parser.add_argument("--profile-dir", type=Path, help="persona profiles for the style set")
    parser.add_argument("--only", nargs="+", help="run only these case ids")
    parser.add_argument("--memory-dir", type=Path, help="contact memories for the history set")
    parser.add_argument("--no-history", action="store_true", help="history set without history")
    args = parser.parse_args()
    if args.set in ("style", "history") and args.input != "text":
        print(f"The {args.set} set has no screenshots: use --input text.")
        return 1
    profiles = None
    if args.profile_dir:
        profiles = {
            path.stem: StyleProfile.model_validate_json(path.read_text(encoding="utf-8"))
            for path in args.profile_dir.glob("*.json")
        }

    chats = only_cases(load_chats(args.set), args.only)[: args.limit]
    calls = plan_calls(chats, args.models, None)
    if args.dry_run:
        print(f"{len(calls)} calls planned, none made")
        return 0

    api_key = get_api_key()
    if not api_key:
        print("No API key stored. Run: uv run python -m respondoia --set-api-key")
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    runner = Runner(
        args.out, api_key, args.input, profiles, args.set, args.memory_dir, not args.no_history
    )
    todo = len(set(calls) - set(runner.done))
    print(f"{len(calls)} calls planned, {todo} to make")

    with ThreadPoolExecutor(WORKERS) as pool:
        chains = [(chat, model) for chat in chats for model in args.models]
        futures = [pool.submit(runner.run_chain, chat, model) for chat, model in chains]
        try:
            for future in futures:
                future.result()
        except ServedModelMismatch as exc:
            pool.shutdown(cancel_futures=True)
            print(f"Stopped: {exc}")
            return 2

    prices_file = args.out.parent / "prices.toml"
    prices = tomllib.loads(prices_file.read_text(encoding="utf-8")) if prices_file.exists() else {}
    wanted = set(calls)
    rows = [
        row
        for row in read_rows(runner.results)
        if (row["case"], row["model"], row["variant"]) in wanted
    ]
    print_summary(summarize(rows, prices))
    failed = len(read_rows(runner.errors))
    if failed:
        print(f"\n{failed} failed calls so far, see {runner.errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
