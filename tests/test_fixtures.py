import json
import re
from pathlib import Path

import pytest
from PIL import Image

SHOTS = Path(__file__).parent / "screenshots"
CHATS = SHOTS / "chats"
LANGS = ["pt-BR", "en"]
SCENARIOS = [
    "01-short",
    "02-long",
    "03-group",
    "04-dark",
    "05-image",
    "06-audio",
    "07-quoted",
    "08-injection",
    "09-price-missing",
    "10-schedule",
    "11-complaint",
    "12-informal",
    "13-multi-question",
    "14-nothing-pending",
    "15-decline",
]
WITH_REGENERATE = {
    "09-price-missing",
    "10-schedule",
    "11-complaint",
    "12-informal",
    "13-multi-question",
    "15-decline",
}


def load(lang, name):
    return json.loads((CHATS / lang / f"{name}.json").read_text(encoding="utf-8"))


def all_chats():
    return [load(lang, name) for lang in LANGS for name in SCENARIOS]


@pytest.mark.parametrize("lang", LANGS)
def test_every_scenario_exists_with_its_image(lang):
    for name in SCENARIOS:
        chat = load(lang, name)
        assert chat["id"] == f"{lang}/{name}" and chat["language"] == lang
        with Image.open(SHOTS / lang / f"{name}.png") as im:
            assert max(im.size) <= 1300


def test_expectations_point_at_real_messages():
    for chat in all_chats():
        ids = list(chat["expect"]["pending"])
        ids += chat.get("regenerate", {}).get("selected", [])
        for i in ids:
            assert 1 <= i <= len(chat["messages"]), chat["id"]
            assert chat["messages"][i - 1]["author"] != "user", chat["id"]


def test_regenerate_blocks_are_where_the_model_test_expects_them():
    for chat in all_chats():
        name = chat["id"].split("/")[1]
        assert ("regenerate" in chat) == (name in WITH_REGENERATE), chat["id"]
        if "regenerate" in chat:
            assert chat["regenerate"]["selected"] and chat["regenerate"]["instruction"].strip()


def test_no_phone_numbers_or_long_digit_runs():
    for chat in all_chats():
        texts = [chat["contact"]]
        for m in chat["messages"]:
            texts += [m["text"], m["quote"] or "", m["sender"] or ""]
        for text in texts:
            assert not re.search(r"\d{8,}", text), chat["id"]


def test_special_scenarios_carry_their_flags():
    for lang in LANGS:
        injection = load(lang, "08-injection")
        canary = injection["expect"]["canary"]
        assert canary and any(
            canary in m["text"] for m in injection["messages"] if m["author"] == "contact"
        )
        assert load(lang, "06-audio")["expect"]["note_expected"] is True
        assert any(m["kind"] == "audio" for m in load(lang, "06-audio")["messages"])
        assert any(m["kind"] == "image" for m in load(lang, "05-image")["messages"])
        assert any(m["quote"] for m in load(lang, "07-quoted")["messages"])
        assert load(lang, "09-price-missing")["expect"]["needs_confirm"] is True
        assert load(lang, "04-dark")["theme"] == "dark"
        group = load(lang, "03-group")
        assert group["group"] is True
        assert all(m["sender"] for m in group["messages"] if m["author"] == "contact")
        assert load(lang, "14-nothing-pending")["expect"]["pending"] == []
        assert load(lang, "14-nothing-pending")["messages"][-1]["author"] == "user"
