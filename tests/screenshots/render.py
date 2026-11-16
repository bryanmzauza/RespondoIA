"""Render the fictional chats in chats/ to PNG screenshots with headless Edge.

Usage: uv run python tests/screenshots/render.py [--only <id>]

The JSON files are the source. The PNGs are generated and committed so the
tests and the model comparison run without a browser.
"""

import argparse
import html
import json
import subprocess
import tempfile
import time
from pathlib import Path
from string import Template

HERE = Path(__file__).parent
CHATS = HERE / "chats"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
WIDTH, HEIGHT = 900, 1300
COMPOSER = {"pt-BR": "Mensagem", "en": "Type a message"}


def message_html(message: dict, group: bool) -> str:
    parts = []
    if group and message["author"] == "contact" and message["sender"]:
        parts.append(f'<div class="sender">{html.escape(message["sender"])}</div>')
    if message["quote"]:
        parts.append(f'<div class="quote">{html.escape(message["quote"])}</div>')
    if message["kind"] == "image":
        parts.append('<div class="photo"></div>')
    if message["kind"] == "audio":
        parts.append(
            '<div class="audio"><span class="play"></span><span class="wave"></span>'
            '<span class="len">0:23</span></div>'
        )
    parts.append(html.escape(message["text"]))
    parts.append(f'<span class="time">{html.escape(message["time"])}</span>')
    side = "out" if message["author"] == "user" else "in"
    return f'<div class="row {side}"><div class="bubble">{"".join(parts)}</div></div>'


def page_html(chat: dict) -> str:
    senders = dict.fromkeys(m["sender"] for m in chat["messages"] if m["sender"])
    subtitle = f'<div class="sub">{html.escape(", ".join(senders))}</div>' if senders else ""
    template = Template((HERE / "template.html").read_text(encoding="utf-8"))
    return template.substitute(
        lang=chat["language"],
        theme=chat["theme"],
        title=html.escape(chat["contact"]),
        initial=html.escape(chat["contact"][:1].upper()),
        subtitle=subtitle,
        messages="\n".join(message_html(m, chat["group"]) for m in chat["messages"]),
        composer=html.escape(COMPOSER[chat["language"]]),
    )


def render(chat: dict, workdir: Path) -> Path:
    page = workdir / "chat.html"
    page.write_text(page_html(chat), encoding="utf-8")
    png = HERE / f"{chat['id']}.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    png.unlink(missing_ok=True)
    subprocess.run(
        [
            str(EDGE),
            "--headless=new",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            f"--window-size={WIDTH},{HEIGHT}",
            # A separate profile keeps this run apart from an Edge that is already open.
            f"--user-data-dir={workdir / 'profile'}",
            f"--screenshot={png}",
            page.as_uri(),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    # The Edge launcher returns at once; the browser writes the file a moment later.
    deadline = time.monotonic() + 30
    size = -1
    while time.monotonic() < deadline:
        current = png.stat().st_size if png.exists() else -1
        if current > 0 and current == size:
            return png
        size = current
        time.sleep(0.3)
    raise RuntimeError(f"Edge did not write {png}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", help="chat id to render, for example pt-BR/01-short")
    args = parser.parse_args()
    for path in sorted(CHATS.glob("*/*.json")):
        chat = json.loads(path.read_text(encoding="utf-8"))
        if args.only and chat["id"] != args.only:
            continue
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            print(render(chat, Path(tmp)).relative_to(HERE))


if __name__ == "__main__":
    main()
