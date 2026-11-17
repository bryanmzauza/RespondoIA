import logging
from dataclasses import replace

import pytest

from respondoia.capture import CaptureError
from respondoia.config import Settings
from respondoia.domain import ChatLine, ChatRef, ChatView, OpenChat, Rect, WindowInfo
from respondoia.llm import (
    ChatMessage,
    LlmError,
    Suggestion,
    SuggestionPreview,
    SuggestionReply,
    SuggestResult,
)
from respondoia.session import ChooseResult, Ports, Session, StartStatus

BOUNDS = Rect(420, 0, 1600, 900)
HWND = 100


def make_reply(count=3, text="mensagem"):
    return SuggestionReply(
        contact="Marina Duarte",
        summary="Quer saber o prazo",
        messages=[
            ChatMessage(id=1, author="contact", text=text, pending=False),
            ChatMessage(id=2, author="contact", text="Consegue entregar até sexta?", pending=True),
        ],
        suggestions=[
            Suggestion(label=f"Opção {n}", text=f"sugestão {n}") for n in range(1, count + 1)
        ],
    )


class FakePorts:
    """Records every call. Chats and texts are fictional."""

    def __init__(self, title="Marina Duarte", exe="WhatsApp.Root.exe"):
        self.calls = []
        self.window = WindowInfo(HWND, "WhatsApp", exe, Rect(0, 0, 1600, 900), False)
        self.window_alive = True
        self.chat = OpenChat(title, BOUNDS) if title is not None else None
        self.lines = []  # no lines read: the session falls back to the screenshot
        self.reply = make_reply()
        self.suggest_error = None
        self.capture_error = None
        self.insert_ok = True
        self.preview_first = True

    def names(self):
        return [call[0] for call in self.calls]

    def count(self, name):
        return self.names().count(name)

    def foreground_window(self):
        self.calls.append(("foreground_window",))
        return self.window

    def window_info(self, hwnd):
        self.calls.append(("window_info", hwnd))
        return self.window if self.window_alive else None

    def read_chat_view(self, hwnd):
        self.calls.append(("read_chat_view", hwnd))
        return ChatView(self.chat, tuple(self.lines)) if self.chat is not None else None

    def capture_chat(self, hwnd, bounds):
        self.calls.append(("capture_chat", hwnd, bounds))
        if self.capture_error:
            raise self.capture_error
        return b"png-bytes"

    def suggest(self, request, on_preview=None):
        self.calls.append(("suggest", request))
        if on_preview is not None and self.preview_first:
            on_preview(
                SuggestionPreview(summary=self.reply.summary, suggestions=self.reply.suggestions)
            )
        if self.suggest_error:
            raise self.suggest_error
        return SuggestResult(self.reply, "claude-haiku-5-5", 2100, 640, 1234, 900)

    def insert_text(self, hwnd, text):
        self.calls.append(("insert_text", hwnd, text))
        return self.insert_ok

    def copy_text(self, text):
        self.calls.append(("copy_text", text))

    def ports(self):
        return Ports(
            foreground_window=self.foreground_window,
            window_info=self.window_info,
            read_chat_view=self.read_chat_view,
            capture_chat=self.capture_chat,
            suggest=self.suggest,
            insert_text=self.insert_text,
            copy_text=self.copy_text,
        )


def started(fake, settings=None):
    session = Session(fake.ports(), settings or Settings())
    assert session.start().status is StartStatus.STARTED
    return session


def test_other_window_does_nothing():
    fake = FakePorts(exe="notepad.exe")
    fake.window = replace(fake.window, title="Notes")
    result = Session(fake.ports(), Settings()).start()
    assert result.status is StartStatus.NOT_WHATSAPP and result.chat is None
    assert fake.names() == ["foreground_window"]


def test_no_foreground_window_does_nothing():
    fake = FakePorts()
    fake.window = None
    assert Session(fake.ports(), Settings()).start().status is StartStatus.NOT_WHATSAPP
    assert fake.names() == ["foreground_window"]


def test_private_contact_is_never_captured_or_sent():
    fake = FakePorts()
    session = Session(fake.ports(), Settings(private_contacts=("marina  duarte",)))
    assert session.start().status is StartStatus.PRIVATE
    assert session.chat is None
    assert fake.count("capture_chat") == 0 and fake.count("suggest") == 0
    with pytest.raises(RuntimeError):
        session.suggest()
    assert fake.count("capture_chat") == 0 and fake.count("suggest") == 0


def test_unknown_chat_is_never_captured_or_sent():
    fake = FakePorts(title=None)
    session = Session(fake.ports(), Settings())
    assert session.start().status is StartStatus.UNKNOWN_CHAT
    with pytest.raises(RuntimeError):
        session.suggest()
    assert fake.count("capture_chat") == 0 and fake.count("suggest") == 0


def test_start_keeps_the_window_and_the_chat():
    fake = FakePorts()
    session = Session(fake.ports(), Settings())
    result = session.start()
    assert result.chat == OpenChat("Marina Duarte", BOUNDS)
    assert session.chat == ChatRef(HWND, "Marina Duarte")


def test_suggest_captures_once_and_validates_the_reply():
    fake = FakePorts()
    session = started(fake, Settings(suggestion_count=3))
    assert session.suggest() is fake.reply
    assert ("capture_chat", HWND, BOUNDS) in fake.calls
    request = fake.calls[-1][1]
    assert request.image_png == b"png-bytes" and request.count == 3 and request.previous is None

    fake.reply = make_reply(count=2)  # the model gave the wrong number of suggestions
    with pytest.raises(LlmError) as err:
        session.suggest()
    assert err.value.code == "invalid_reply"
    assert fake.count("capture_chat") == 1


def test_a_failed_capture_can_be_tried_again():
    fake = FakePorts()
    session = started(fake)
    fake.capture_error = CaptureError("blank")
    with pytest.raises(CaptureError):
        session.suggest()
    assert fake.count("suggest") == 0
    fake.capture_error = None
    assert session.suggest() is fake.reply


def test_regenerate_does_not_read_the_screen_again():
    fake = FakePorts()
    session = started(fake)
    first = session.suggest()
    fake.reply = make_reply(text="outra leitura")
    assert session.regenerate((2,), "pedir prazo") is fake.reply
    assert fake.count("capture_chat") == 1 and fake.count("read_chat_view") == 1
    request = fake.calls[-1][1]
    assert request.previous is first
    assert request.selected_ids == (2,) and request.instruction == "pedir prazo"
    assert request.image_png == b"png-bytes"


def test_regenerate_needs_a_first_reply():
    session = started(FakePorts())
    with pytest.raises(RuntimeError):
        session.regenerate((1,), "")


def test_choose_pastes_into_the_same_chat():
    fake = FakePorts()
    session = started(fake)
    session.suggest()
    assert session.choose(1) is ChooseResult.PASTED
    assert ("insert_text", HWND, "sugestão 2") in fake.calls
    assert fake.count("copy_text") == 0


def test_choose_copies_when_the_chat_changed():
    fake = FakePorts()
    session = started(fake)
    session.suggest()
    fake.chat = OpenChat("Caio Lima", BOUNDS)
    assert session.choose(0) is ChooseResult.COPIED_CHAT_CHANGED
    assert fake.count("insert_text") == 0
    assert ("copy_text", "sugestão 1") in fake.calls


def test_choose_copies_when_window_is_gone():
    fake = FakePorts()
    session = started(fake)
    session.suggest()
    fake.window_alive = False
    assert session.choose(0) is ChooseResult.COPIED_CHAT_CHANGED
    assert fake.count("insert_text") == 0 and ("copy_text", "sugestão 1") in fake.calls


def test_choose_copies_when_title_cannot_be_read():
    fake = FakePorts()
    session = started(fake)
    session.suggest()
    fake.chat = None
    assert session.choose(0) is ChooseResult.COPIED_CHAT_CHANGED
    assert fake.count("insert_text") == 0 and ("copy_text", "sugestão 1") in fake.calls


def test_copy_mode_never_pastes():
    fake = FakePorts()
    session = started(fake, Settings(insert_mode="copy"))
    session.suggest()
    assert session.choose(2) is ChooseResult.COPIED
    assert fake.count("insert_text") == 0 and ("copy_text", "sugestão 3") in fake.calls


def test_focus_failure_is_reported():
    fake = FakePorts()
    fake.insert_ok = False
    session = started(fake)
    session.suggest()
    assert session.choose(0) is ChooseResult.COPIED_FOCUS_FAILED


def test_close_drops_the_image_and_blocks_further_use():
    fake = FakePorts()
    session = started(fake)
    session.suggest()
    session.close()
    assert session.chat is None
    assert b"png-bytes" not in [v for v in vars(session).values() if isinstance(v, bytes)]
    for call in (session.suggest, lambda: session.regenerate((1,), ""), lambda: session.choose(0)):
        with pytest.raises(RuntimeError):
            call()


def test_a_choice_closes_the_session():
    fake = FakePorts()
    session = started(fake)
    session.suggest()
    session.choose(0)
    with pytest.raises(RuntimeError):
        session.choose(0)
    assert fake.count("insert_text") == 1


def test_logs_carry_no_content(caplog):
    fake = FakePorts(title="ZZ-SECRET-TITLE")
    fake.reply = make_reply(text="ZZ-SECRET-TEXT")
    with caplog.at_level(logging.DEBUG, logger="respondoia"):
        session = started(fake)
        session.suggest()
        session.regenerate((1,), "ZZ-SECRET-INSTRUCTION")
        fake.suggest_error = LlmError("network")
        with pytest.raises(LlmError):
            session.regenerate((1,), "")
        session.choose(0)
    assert caplog.records, "the session should log its events"
    assert "ZZ-SECRET" not in caplog.text and "sugestão" not in caplog.text
    assert "network" in caplog.text and "claude-haiku-5-5" in caplog.text


def failed_capture(settings=None):
    fake = FakePorts()
    session = started(fake, settings)
    fake.capture_error = CaptureError("blank")
    with pytest.raises(CaptureError):
        session.suggest()
    fake.capture_error = None
    return fake, session


def test_retry_after_a_failed_capture_reads_the_chat_again():
    fake, session = failed_capture()
    fake.chat = OpenChat("Caio Lima", BOUNDS)  # the user opened another chat meanwhile
    with pytest.raises(CaptureError):
        session.suggest()
    assert fake.count("capture_chat") == 1 and fake.count("suggest") == 0


def test_retry_after_a_failed_capture_never_sends_a_private_chat():
    fake, session = failed_capture(Settings(private_contacts=("Bia Rocha",)))
    fake.chat = OpenChat("Bia Rocha", BOUNDS)
    with pytest.raises(CaptureError):
        session.suggest()
    assert fake.count("capture_chat") == 1 and fake.count("suggest") == 0


def test_retry_after_a_failed_capture_stops_if_the_window_is_gone():
    fake, session = failed_capture()
    fake.window_alive = False
    with pytest.raises(CaptureError):
        session.suggest()
    assert fake.count("capture_chat") == 1


def test_retry_after_a_failed_capture_uses_the_current_bounds():
    fake, session = failed_capture()
    moved = Rect(500, 0, 1700, 900)
    fake.chat = OpenChat("Marina Duarte", moved)
    session.suggest()
    assert fake.calls[-2] == ("capture_chat", HWND, moved)


def test_suggest_hands_the_preview_over():
    fake = FakePorts()
    session = started(fake)
    previews = []
    session.suggest(previews.append)
    assert [p.suggestions for p in previews] == [fake.reply.suggestions]


def test_choice_uses_the_preview_when_the_list_failed():
    fake = FakePorts()
    session = started(fake)
    fake.suggest_error = LlmError("truncated")  # the stream broke after the suggestions
    with pytest.raises(LlmError):
        session.suggest()
    assert session.choose(1) is ChooseResult.PASTED
    assert ("insert_text", HWND, "sugestão 2") in fake.calls


def test_a_refused_reply_is_never_pasted_even_after_a_preview():
    fake = FakePorts()
    session = started(fake)
    fake.suggest_error = LlmError("refused")
    with pytest.raises(LlmError):
        session.suggest()
    with pytest.raises(LlmError) as err:
        session.choose(0)
    assert err.value.code == "refused"
    assert fake.count("insert_text") == 0 and fake.count("copy_text") == 0


def test_without_a_preview_a_failed_call_leaves_nothing_to_choose():
    fake = FakePorts()
    fake.preview_first = False
    session = started(fake)
    fake.suggest_error = LlmError("network")
    with pytest.raises(LlmError):
        session.suggest()
    with pytest.raises(LlmError) as err:
        session.choose(0)
    assert err.value.code == "network"
    assert fake.count("insert_text") == 0


def test_a_choice_during_a_failed_regenerate_never_pastes_an_old_option():
    fake = FakePorts()
    fake.preview_first = False
    session = started(fake)
    session.suggest()
    fake.suggest_error = LlmError("network")  # fails before any new option is shown
    with pytest.raises(LlmError):
        session.regenerate((2,), "pedir prazo")
    with pytest.raises(LlmError):
        session.choose(0)
    assert fake.count("insert_text") == 0 and fake.count("copy_text") == 0


LINES = [
    ChatLine("contact", "", "09:10", "text", "Oi! cpf 529.982.247-25", ""),
    ChatLine("user", "", "09:12", "text", "oi", ""),
    ChatLine("contact", "", "09:13", "text", "Manda o arquivo?", ""),
]


def text_fake():
    fake = FakePorts()
    fake.lines = list(LINES)
    return fake


def test_text_input_never_captures():
    fake = text_fake()
    session = started(fake)
    session.suggest()
    request = fake.calls[-1][1]
    assert fake.count("capture_chat") == 0
    assert request.image_png is None and len(request.lines) == 3
    assert session.fell_back is False


def test_lines_are_masked_before_the_request():
    fake = text_fake()
    session = started(fake)
    session.suggest()
    assert fake.calls[-1][1].lines[0].text == "Oi! cpf [CPF]"


def test_masking_can_be_turned_off():
    fake = text_fake()
    session = started(fake, Settings(mask_sensitive_data=False))
    session.suggest()
    assert fake.calls[-1][1].lines[0].text == "Oi! cpf 529.982.247-25"


def test_no_lines_falls_back_to_screenshot_with_notice():
    fake = FakePorts()
    session = started(fake)
    session.suggest()
    request = fake.calls[-1][1]
    assert fake.count("capture_chat") == 1 and request.image_png == b"png-bytes"
    assert request.lines == () and session.fell_back is True


def test_screenshot_input_always_captures():
    fake = text_fake()
    session = started(fake, Settings(input="screenshot"))
    session.suggest()
    assert fake.count("capture_chat") == 1 and fake.calls[-1][1].lines == ()
    assert session.fell_back is False


def test_private_contact_blocks_both_inputs():
    for settings in (Settings(), Settings(input="screenshot")):
        fake = text_fake()
        session = Session(fake.ports(), replace(settings, private_contacts=("Marina Duarte",)))
        assert session.start().status is StartStatus.PRIVATE
        with pytest.raises(RuntimeError):
            session.suggest()
        assert fake.count("capture_chat") == 0 and fake.count("suggest") == 0


def test_regenerate_in_text_mode_reuses_the_masked_lines():
    fake = text_fake()
    session = started(fake)
    session.suggest()
    first = fake.calls[-1][1]
    session.regenerate((3,), "pedir prazo")
    again = fake.calls[-1][1]
    assert again.lines == first.lines and again.image_png is None
    assert (again.selected_ids, again.instruction) == ((3,), "pedir prazo")
    assert fake.count("read_chat_view") == 1 and fake.count("capture_chat") == 0


def test_text_mode_choice_still_checks_the_chat():
    fake = text_fake()
    session = started(fake)
    session.suggest()
    fake.chat = OpenChat("Caio Lima", BOUNDS)
    assert session.choose(0) is ChooseResult.COPIED_CHAT_CHANGED


def test_logs_carry_no_content_in_text_mode(caplog):
    fake = FakePorts(title="ZZ-SECRET-TITLE")
    fake.lines = [ChatLine("contact", "ZZ-SECRET-SENDER", "09:10", "text", "ZZ-SECRET-TEXT", "")]
    with caplog.at_level(logging.DEBUG, logger="respondoia"):
        session = started(fake)
        session.suggest()
        session.regenerate((1,), "ZZ-SECRET-INSTRUCTION")
        session.choose(0)
    assert "ZZ-SECRET" not in caplog.text
    assert "input=text" in caplog.text


def test_lines_without_any_text_fall_back_to_the_screenshot():
    fake = FakePorts()
    fake.lines = [
        ChatLine("contact", "", "09:10", "audio", "", ""),
        ChatLine("contact", "", "09:11", "image", "", ""),
    ]
    session = started(fake)
    session.suggest()
    assert fake.count("capture_chat") == 1 and fake.calls[-1][1].lines == ()
    assert session.fell_back is True


# Style and context (stage 0.3).

from respondoia.domain import UserContext


class FakeStyle:
    """Records what the session asks of the style service."""

    def __init__(self, context=None, language="pt"):
        self.calls = []
        self.language = language
        self.ctx = context or UserContext("pt", None, False, True, "ZZ-STYLE", "ZZ-KNOWLEDGE")

    def names(self):
        return [call[0] for call in self.calls]

    def learn(self, title, lines, is_group):
        self.calls.append(("learn", title, tuple(lines), is_group))
        return self.language

    def context(self, title, language, is_group):
        self.calls.append(("context", title, language, is_group))
        return self.ctx

    def guess_label(self, title, label):
        self.calls.append(("guess_label", title, label))

    def set_label(self, title, label):
        self.calls.append(("set_label", title, label))

    def remember_pasted(self, text):
        self.calls.append(("remember_pasted", text))

    def forget(self, title):
        self.calls.append(("forget", title))


def styled(fake, style, settings=None):
    session = Session(replace(fake.ports(), style=style), settings or Settings())
    return session, session.start()


TEXT_LINES = [
    ChatLine("contact", "", "09:10", "text", "Oi! Consegue entregar até sexta?", ""),
]


def test_private_start_forgets_and_learns_nothing():
    fake, style = FakePorts(), FakeStyle()
    fake.lines = TEXT_LINES
    _, result = styled(fake, style, Settings(private_contacts=("Marina Duarte",)))
    assert result.status is StartStatus.PRIVATE
    assert style.calls == [("forget", "Marina Duarte")]


def test_start_learns_then_builds_the_context():
    fake, style = FakePorts(), FakeStyle()
    fake.lines = TEXT_LINES
    _, result = styled(fake, style)
    assert style.names() == ["learn", "context"]
    assert style.calls[0] == ("learn", "Marina Duarte", tuple(TEXT_LINES), False)
    assert style.calls[1] == ("context", "Marina Duarte", "pt", False)
    assert result.context is style.ctx


def test_a_chat_with_senders_is_a_group():
    fake, style = FakePorts(), FakeStyle()
    fake.lines = [ChatLine("contact", "Bia Rocha", "09:10", "text", "Quem leva a bola?", "")]
    styled(fake, style)
    assert style.calls[0][3] is True and style.calls[1][3] is True


@pytest.mark.parametrize("input_mode", ["text", "screenshot"])
def test_request_carries_the_context_in_both_inputs(input_mode):
    fake, style = FakePorts(), FakeStyle()
    fake.lines = TEXT_LINES
    session, _ = styled(fake, style, Settings(input=input_mode))
    session.suggest()
    session.regenerate((1,), "pedir prazo")
    requests = [call[1] for call in fake.calls if call[0] == "suggest"]
    assert [r.context for r in requests] == [style.ctx, style.ctx]


def test_label_guess_is_passed_on_once():
    fake, style = FakePorts(), FakeStyle()
    fake.lines = TEXT_LINES
    fake.reply = make_reply().model_copy(update={"contact_label": "client"})
    session, _ = styled(fake, style)
    session.suggest()
    session.regenerate((1,), "")
    assert [c for c in style.calls if c[0] == "guess_label"] == [
        ("guess_label", "Marina Duarte", "client")
    ]


def test_label_guess_is_ignored_when_not_asked():
    fake = FakePorts()
    style = FakeStyle(UserContext("pt", "friend", True, False, "", ""))
    fake.lines = TEXT_LINES
    fake.reply = make_reply().model_copy(update={"contact_label": "client"})
    session, _ = styled(fake, style)
    session.suggest()
    assert "guess_label" not in style.names()


@pytest.mark.parametrize("window_alive", [True, False])
def test_choose_remembers_what_was_pasted_or_copied(window_alive):
    fake, style = FakePorts(), FakeStyle()
    fake.lines = TEXT_LINES
    session, _ = styled(fake, style)
    session.suggest()
    fake.window_alive = window_alive
    result = session.choose(1)
    assert result in (ChooseResult.PASTED, ChooseResult.COPIED_CHAT_CHANGED)
    assert style.calls[-1] == ("remember_pasted", "sugestão 2")


def test_logs_carry_no_content_with_style(caplog):
    fake, style = FakePorts(), FakeStyle()
    fake.lines = TEXT_LINES
    with caplog.at_level(logging.INFO, logger="respondoia"):
        session, _ = styled(fake, style)
        session.suggest()
    assert "ZZ-" not in caplog.text and "start ok style=1 knowledge=1 label=0" in caplog.text


def test_masked_lines_stay_unset_when_masking_fails(monkeypatch):
    from respondoia import session as session_module

    fake = FakePorts()
    fake.lines = [ChatLine("contact", "", "09:10", "text", "cpf 529.982.247-25", "")]
    session = started(fake)

    def broken(lines):
        raise ValueError("masking failed")

    monkeypatch.setattr(session_module, "mask_lines", broken)
    with pytest.raises(ValueError):
        session.suggest()
    monkeypatch.undo()
    with pytest.raises(RuntimeError):
        session.regenerate((1,), "")
    assert fake.count("suggest") == 0


from respondoia.memory.merge import Stored
from respondoia.memory.recall import Snippet
from respondoia.memory.service import HistoryContext

READY = HistoryContext(
    "ready",
    7,
    "Marina Duarte",
    memory="ZZ-MEMORY",
    snippets=(Snippet("2026-10-20", (Stored("2026-10-20", "10:00", "contact", "", "text",
                                            "ZZ-SNIPPET", ""),)),),
)


class FakeHistory:
    def __init__(self, context=READY):
        self.calls = []
        self.context = context

    def observe(self, title, lines, is_group, avatar):
        self.calls.append(("observe", title, tuple(lines), is_group, avatar))
        return self.context

    def confirm(self, title, lines, contact_id):
        self.calls.append(("confirm", title, contact_id))
        return READY


def historied(fake, history, style=None, settings=None, avatar=""):
    ports = replace(fake.ports(), history=history, avatar=lambda hwnd, rect: avatar)
    if style is not None:
        ports = replace(ports, style=style)
    session = Session(ports, settings or Settings())
    return session, session.start()


def test_history_is_observed_after_style_and_before_the_request():
    fake, style, history = FakePorts(), FakeStyle(), FakeHistory()
    fake.lines = TEXT_LINES
    _, result = historied(fake, history, style, avatar="ab12")
    assert style.names() == ["learn", "context"]
    assert history.calls == [("observe", "Marina Duarte", tuple(TEXT_LINES), False, "ab12")]
    assert result.history is READY and fake.count("suggest") == 0


def test_ready_history_reaches_the_request():
    fake, history = FakePorts(), FakeHistory()
    fake.lines = TEXT_LINES
    session, _ = historied(fake, history, FakeStyle())
    session.suggest()
    context = fake.calls[-1][1].context
    assert context.memory == "ZZ-MEMORY" and context.snippets[0]["text"] == "ZZ-SNIPPET"
    assert context.style == "ZZ-STYLE"


@pytest.mark.parametrize("state", ["ask", "off", "group", "out_of_scope", "unavailable"])
def test_history_not_ready_reaches_nothing(state):
    fake = FakePorts()
    fake.lines = TEXT_LINES
    history = FakeHistory(HistoryContext(state, memory="ZZ-MEMORY"))
    session, _ = historied(fake, history, FakeStyle())
    session.suggest()
    context = fake.calls[-1][1].context
    assert context.memory == "" and context.snippets == ()


def test_private_start_observes_nothing():
    fake, history = FakePorts(), FakeHistory()
    fake.lines = TEXT_LINES
    _, result = historied(fake, history, settings=Settings(private_contacts=("Marina Duarte",)))
    assert result.status is StartStatus.PRIVATE and history.calls == []


def test_confirm_identity_rebuilds_the_context():
    fake = FakePorts()
    fake.lines = TEXT_LINES
    history = FakeHistory(HistoryContext("ask", candidates=((3, "2026-10-01"),)))
    session, _ = historied(fake, history, FakeStyle())
    session.suggest()
    assert fake.calls[-1][1].context.memory == ""
    assert session.confirm_identity(3) is READY
    assert history.calls[-1] == ("confirm", "Marina Duarte", 3)
    session.regenerate((), "")
    assert fake.calls[-1][1].context.memory == "ZZ-MEMORY"


def test_logs_carry_no_history_content(caplog):
    fake = FakePorts()
    fake.lines = TEXT_LINES
    with caplog.at_level(logging.INFO, logger="respondoia"):
        session, _ = historied(fake, FakeHistory(), FakeStyle())
        session.suggest()
    assert "ZZ-" not in caplog.text


def test_deleted_history_is_not_sent_on_regenerate():
    fake = FakePorts()
    fake.lines = TEXT_LINES
    session, _ = historied(fake, FakeHistory(), FakeStyle())
    session.suggest()
    session.drop_history()
    session.regenerate((), "")
    context = fake.calls[-1][1].context
    assert context.memory == "" and context.snippets == () and context.style == "ZZ-STYLE"
