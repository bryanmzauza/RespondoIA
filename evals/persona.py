"""Build the style profile of a fictional persona, the way the app builds the user's.

Usage:
    uv run python evals/persona.py --lang pt-BR --out <dir> [--model <id>]

Reads the persona's exports listed in tests/style/personas.json, keeps only the
persona's messages, asks the model once for the description (paid call) and writes
<dir>/<lang>.json for evals/run.py --profile-dir. Prints counts and tokens, never text.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from respondoia.config import Settings, get_api_key
from respondoia.llm import AnthropicClient
from respondoia.style.parser import parse_export
from respondoia.style.profile import EXTRACT_SYSTEM, StyleTraits, build_local, extract_text
from respondoia.style.sampler import OwnMessage, from_export, sample

STYLE = ROOT / "tests" / "style"
EXPORTS = ROOT / "tests" / "exports"
LANGUAGE = {"pt-BR": "pt", "en": "en"}


def persona_messages(lang: str) -> list[OwnMessage]:
    """The persona's own messages from every export, each with the label of its chat."""
    persona = json.loads((STYLE / "personas.json").read_text(encoding="utf-8"))[lang]
    messages: list[OwnMessage] = []
    for label, name in persona["label_files"].items():
        chat = parse_export((EXPORTS / name).read_bytes(), name)
        messages += from_export(chat, persona["author"], label, set())
    return [m for m in messages if m.language == LANGUAGE[lang]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lang", choices=sorted(LANGUAGE), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default=Settings().model)
    args = parser.parse_args()

    api_key = get_api_key()
    if not api_key:
        print("No API key stored. Run: uv run python -m respondoia --set-api-key")
        return 1
    messages = persona_messages(args.lang)
    picked = sample(messages)
    result = AnthropicClient(api_key, args.model, timeout_s=60).extract(
        EXTRACT_SYSTEM, extract_text(picked), StyleTraits
    )
    now = datetime.now().isoformat(timespec="seconds")
    profile = build_local(LANGUAGE[args.lang], messages, result.value, now)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{args.lang}.json").write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    print(
        f"{args.lang}: {len(messages)} messages, {len(picked)} sent, "
        f"{len(profile.examples)} examples, tokens in {result.input_tokens} "
        f"out {result.output_tokens}, {result.latency_ms} ms"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
