import logging
from datetime import datetime, timedelta

import pytest

from respondoia.domain import ChatLine
from respondoia.llm import ExtractResult, LlmError
from respondoia.style.parser import ExportChat, ExportMessage
from respondoia.style.profile import LabelStyle, StyleTraits
from respondoia.style.service import StyleService

NOW = datetime(2026, 11, 17, 10, 0)
TRAITS = StyleTraits(
    formality="informal",
    greetings=["oi"],
    farewells=["flw"],
    expressions=["bora"],
    avoids=[],
    habits=[],
    by_label=[LabelStyle(label="client", note="mais formal")],
)


class FakeClient:
    def __init__(self, error=None):
        self.texts = []
        self.error = error

    def extract(self, system, text, schema):
        self.texts.append(text)
        if self.error:
            raise LlmError(self.error)
        return ExtractResult(TRAITS, "claude-haiku-5-5", 1000, 200, 900)


class Clock:
    def __init__(self):
        self.value = NOW

    def __call__(self):
        return self.value


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def fake_client():
    return FakeClient()


@pytest.fixture
def service(db, tmp_path, fake_client, clock):
    return StyleService(db, lambda: fake_client, tmp_path / "knowledge.md", clock)


def db_dump(db):
    with db.transaction() as conn:
        rows = []
        for table in ("contact", "style_sample", "style_profile", "pasted"):
            rows += [repr(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
    return "\n".join(rows)


def user(text, time="09:01"):
    return ChatLine("user", "", time, "text", text, "")


def contact(text, time="09:00"):
    return ChatLine("contact", "", time, "text", text, "")


WORDS = ["agora", "cedo", "tarde", "depois", "logo", "mais", "menos", "sempre", "nunca", "ainda", "talvez", "enfim"]
PT = [user(f"vc vem hoje? tô aqui em casa {w}", f"09:{n:02d}") for n, w in enumerate(WORDS)]


def test_learn_stores_no_third_party_text(service, db):
    lines = (
        ChatLine("contact", "ZZ-SENDER", "09:00", "text", "ZZ-CONTACT vamos sim, tudo certo", ""),
        ChatLine("user", "", "09:01", "text", "bora, chego umas 8", "ZZ-QUOTE"),
    )
    service.learn("Marina Duarte", lines, is_group=True)
    dump = db_dump(db)
    assert "ZZ-" not in dump and "bora, chego umas 8" in dump


def test_pasted_suggestion_is_not_learned(service, db):
    service.remember_pasted("Consigo sim, sexta de manhã")
    service.learn("Marina Duarte", (contact("e aí, tudo bem?"), user("Consigo sim, sexta de manhã")), False)
    assert "Consigo sim" not in db_dump(db)


def test_learn_returns_the_chat_language(service):
    assert service.learn("Marina Duarte", (contact("oi, tudo bem com vc?"), user("ok")), False) == "pt"


def test_local_profile_appears_after_ten_messages(service):
    service.learn("Marina Duarte", PT[:9], False)
    assert service.context("Marina Duarte", "pt", False).style == ""
    service.learn("Marina Duarte", PT[9:], False)
    style = service.context("Marina Duarte", "pt", False).style
    assert style.startswith("Messages analysed: 12") and "Formality" not in style


def test_context_follows_the_chat_language(service):
    service.learn("Marina Duarte", PT, False)
    english = [
        user(f"are you coming over tonight? I am at home {w}", f"10:{n:02d}")
        for n, w in enumerate(WORDS)
    ]
    service.learn("Marina Duarte", english, False)
    assert "are you coming over" in service.context("Marina Duarte", "en", False).style
    assert "tô aqui em casa" in service.context("Marina Duarte", "pt", False).style
    # Lines that decide nothing fall back to the contact's last language.
    language = service.learn("Marina Duarte", (user("ok"), contact("👍")), False)
    assert language == "en"


def test_label_guess_is_saved_once_and_never_over_a_user_label(service):
    service.learn("Marina Duarte", PT[:1], False)
    assert service.context("Marina Duarte", "pt", False).ask_label
    service.guess_label("Marina Duarte", "client")
    ctx = service.context("Marina Duarte", "pt", False)
    assert (ctx.label, ctx.label_from_user, ctx.ask_label) == ("client", False, False)
    service.set_label("Marina Duarte", "friend")
    service.guess_label("Marina Duarte", "supplier")
    ctx = service.context("Marina Duarte", "pt", False)
    assert (ctx.label, ctx.label_from_user) == ("friend", True)
    service.guess_label("Marina Duarte", "boss")
    assert service.context("Marina Duarte", "pt", False).label == "friend"


def test_group_is_not_asked_for_a_label(service):
    service.learn("Futebol de quinta", PT[:1], True)
    assert not service.context("Futebol de quinta", "pt", True).ask_label


def test_forget_then_nothing_is_kept_for_that_title(service, db):
    service.learn("Marina Duarte", PT, False)
    service.forget("Marina Duarte")
    assert "Marina" not in db_dump(db) and "tô aqui em casa" not in db_dump(db)


def test_refresh_sends_only_sampled_own_messages(service, fake_client):
    chat = ExportChat(
        tuple(
            ExportMessage("2026-11-16", f"09:{n:02d}", author, text, "text")
            for n, (author, text) in enumerate(
                [("Marina", "ZZ-OTHER oi, tudo bem com vc?"), ("Rafa", "tudo sim e vc? to aqui")] * 6
            )
        ),
        (("Marina", 6), ("Rafa", 6)),
    )
    service.import_export(chat, "Rafa", "friend")
    service.learn("Bia", PT, False)
    profile = service.refresh("pt")
    assert profile.traits == TRAITS
    assert "ZZ-OTHER" not in "".join(fake_client.texts) and "to aqui" in fake_client.texts[0]
    assert "Formality: informal" in service.context("Bia", "pt", False).style


def test_refresh_if_due_runs_at_most_once_a_day(service, fake_client, clock):
    many = [user(f"vc vem hoje? tô aqui em casa {n}", f"{n}") for n in range(40)]
    service.learn("Marina Duarte", many, False)
    assert service.refresh_if_due("pt") and len(fake_client.texts) == 1
    more = [user(f"vc vem amanhã? tô em casa {n}", f"{n}") for n in range(40)]
    service.learn("Marina Duarte", more, False)
    assert not service.refresh_if_due("pt")
    clock.value = NOW + timedelta(hours=25)
    assert service.refresh_if_due("pt") and len(fake_client.texts) == 2


def test_refresh_if_due_swallows_model_errors(db, tmp_path):
    service = StyleService(db, lambda: FakeClient("rate_limit"), tmp_path / "k.md", Clock())
    service.learn("Marina", [user(f"vc vem hoje? tô aqui {n}", f"{n}") for n in range(40)], False)
    assert service.refresh_if_due("pt") is False


def test_refresh_without_api_key_is_an_auth_error(db, tmp_path):
    service = StyleService(db, lambda: None, tmp_path / "k.md", Clock())
    service.learn("Marina", PT, False)
    with pytest.raises(LlmError) as err:
        service.refresh("pt")
    assert err.value.code == "auth"


def test_import_reports_counts_and_keeps_no_file(service, tmp_path):
    service.remember_pasted("tudo sim e vc? to aqui 1")
    chat = ExportChat(
        tuple(
            ExportMessage("2026-11-16", f"09:{n:02d}", "Rafa", f"tudo sim e vc? to aqui {n}", "text")
            for n in range(5)
        ),
        (("Rafa", 5),),
    )
    result = service.import_export(chat, "Rafa", None)
    assert (result.added, result.skipped_pasted, result.language) == (4, 1, "pt")
    assert all(p.name.startswith("test.db") for p in tmp_path.iterdir())


def test_overview_lists_languages_with_small_volume(service):
    service.learn("Marina Duarte", PT, False)
    service.save_manual("pt", "nunca uso prezado")
    (view,) = service.overview().languages
    assert (view.language, view.messages, view.small, view.manual) == ("pt", 12, True, "nunca uso prezado")
    assert view.profile is not None


def test_excluded_example_leaves_the_profile(service):
    service.learn("Marina Duarte", PT, False)
    first = service.overview().languages[0].profile.examples[0]
    service.exclude_example("pt", first)
    assert first not in service.overview().languages[0].profile.examples


def test_reset_forgets_the_style(service):
    service.learn("Marina Duarte", PT, False)
    service.reset()
    assert service.overview().languages == ()
    assert service.context("Marina Duarte", "pt", False).style == ""


def test_without_database_everything_is_neutral(tmp_path):
    (tmp_path / "knowledge.md").write_text("Pão: R$ 18", encoding="utf-8")
    s = StyleService(None, lambda: None, tmp_path / "knowledge.md")
    assert s.learn("Marina", (contact("oi, tudo bem com vc?"),), False) == "pt"
    ctx = s.context("Marina", "pt", False)
    assert (ctx.style, ctx.knowledge, ctx.label, ctx.ask_label) == ("", "Pão: R$ 18", None, False)
    s.guess_label("Marina", "client")
    s.remember_pasted("x")
    s.forget("Marina")
    assert s.overview().languages == () and s.refresh_if_due("pt") is False


def test_logs_carry_no_content(service, caplog):
    caplog.set_level(logging.INFO, logger="respondoia")
    service.learn("ZZ-TITLE", (user("ZZ-TEXT vc vem hoje? tô aqui"),), False)
    service.context("ZZ-TITLE", "pt", False)
    assert "ZZ-" not in caplog.text and "learn added=1" in caplog.text
