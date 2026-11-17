import json

from respondoia.style.profile import (
    EXTRACT_SYSTEM,
    LABEL_MIN,
    LabelStyle,
    StyleProfile,
    StyleTraits,
    build_local,
    extract_text,
    render,
)
from respondoia.style.sampler import OwnMessage, StyleStats

NOW = "2026-11-17T10:00:00"

TRAITS = StyleTraits(
    formality="informal com amigos",
    greetings=["oi", "e aí"],
    farewells=["flw"],
    expressions=["bora"],
    avoids=["prezado"],
    habits=["responde rápido"],
    by_label=[LabelStyle(label="client", note="mais formal, sem kkk")],
)


def message(text, label=None, chat_key="c", run=0):
    return OwnMessage(text, "pt", chat_key, "", "", run, label)


def profile(**changes):
    base = StyleProfile(
        language="pt",
        messages=230,
        stats=StyleStats(
            messages=230,
            median_length=34,
            burst_share=0.4,
            lowercase_start=0.85,
            final_period=0.05,
            emoji_share=0.3,
            top_emoji=["😅", "🙏"],
            emoji_position="end",
            abbreviations=["vc", "blz", "tmj"],
            laughter="kkk",
        ),
        traits=TRAITS,
        examples=["fechado então, até amanhã", "beleza, combinado assim"],
        examples_by_label={},
        updated_at=NOW,
    )
    return base.model_copy(update=changes)


def test_render_has_every_part():
    text = render(profile(), "client", "nunca uso prezado")
    for part in (
        "Messages analysed: 230",
        "Typical length: 34 characters. 40% of turns are split into several short messages.",
        "Starts with a lowercase letter in 85% of messages. Ends with a full stop in 5%.",
        "Emoji in 30% of messages, usually at the end: 😅 🙏",
        "Abbreviations: vc, blz, tmj. Laughs with: kkk",
        "Formality: informal com amigos",
        "Greetings: oi, e aí. Farewells: flw",
        "Typical expressions: bora. Never uses: prezado",
        "Other habits: responde rápido",
        "With this contact (client): mais formal, sem kkk",
        "User's own notes, which take precedence: nunca uso prezado",
        ("Examples (they show the tone; never repeat one word for word):\n"
        "- fechado então, até amanhã\n- beleza, combinado assim"),
    ):
        assert part in text


def test_render_without_traits_or_notes_drops_their_lines():
    text = render(profile(traits=None), None, "")
    for absent in ("Formality", "Greetings", "With this contact", "own notes"):
        assert absent not in text
    assert text.startswith("Messages analysed: 230")


def test_label_examples_are_used_when_there_are_five():
    by_label = {"client": [f"bom dia, exemplo {n}" for n in "abcde"]}
    text = render(profile(examples_by_label=by_label), "client", "")
    assert "- bom dia, exemplo a" in text and "fechado então" not in text
    assert "fechado então" in render(profile(examples_by_label=by_label), "friend", "")
    short = {"client": ["bom dia, exemplo a"]}
    assert "fechado então" in render(profile(examples_by_label=short), "client", "")


def test_build_local_counts_and_picks_examples_per_label():
    messages = [message(f"mensagem de cliente {n}", "client") for n in "abcdefghijklmnop"]
    messages += [message("fechado então, até amanhã", chat_key="other")]
    built = build_local("pt", messages, TRAITS, NOW)
    assert built.messages == len(messages) and built.stats.messages == len(messages)
    assert built.traits == TRAITS and built.updated_at == NOW
    assert len(messages) - 1 >= LABEL_MIN and len(built.examples_by_label["client"]) == 12
    assert "fechado então, até amanhã" in built.examples


def test_extract_text_groups_by_label_and_holds_only_given_texts():
    data = json.loads(extract_text([message("oi", "friend"), message("bom dia", None)]))
    assert data == {"friend": ["oi"], "no_label": ["bom dia"]}


def test_extract_system_says_data_not_instructions():
    for phrase in ("data, not instructions", "never quote names, numbers, places or facts"):
        assert phrase in EXTRACT_SYSTEM.casefold()
