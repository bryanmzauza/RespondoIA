import logging
from dataclasses import replace

import pytest

from respondoia.capture import CaptureError
from respondoia.config import Settings
from respondoia.domain import ChatRef, OpenChat, Rect, WindowInfo
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
        suggestions=[Suggestion(label=f"Opção {n}", text=f"sugestão {n}") for n in range(1, count + 1)],
    )


class FakePorts:
    """Records every call. Chats and texts are fictional."""

    def __init__(self, title="Marina Duarte", exe="WhatsApp.Root.exe"):
        self.calls = []
        self.window = WindowInfo(HWND, "WhatsApp", exe, Rect(0, 0, 1600, 900), False)
        self.window_alive = True
        self.chat = OpenChat(title, BOUNDS) if title is not None else None
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

    def read_open_chat(self, hwnd):
        self.calls.append(("read_open_chat", hwnd))
        return self.chat

    def capture_chat(self, hwnd, bounds):
        self.calls.append(("capture_chat", hwnd, bounds))
        if self.capture_error:
            raise self.capture_error
        return b"png-bytes"

    def suggest(self, request, on_preview=None):
        self.calls.append(("suggest", request))
        if on_preview is not None and self.preview_first:
            on_preview(SuggestionPreview(summary=self.reply.summary,
                                         suggestions=self.reply.suggestions))
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
            read_open_chat=self.read_open_chat,
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
    assert fake.count("capture_chat") == 1 and fake.count("read_open_chat") == 1
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
