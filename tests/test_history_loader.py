import random
from datetime import date

from respondoia.domain import ChatLine, ChatView, OpenChat, Rect
from respondoia.memory.loader import LoaderPorts, LoadLimits, load, months_before

HWND = 42
BOUNDS = Rect(0, 0, 800, 900)


class FakeChat:
    """A fictional chat of `n` messages over several days. The window shows `rows` lines;
    each notch moves it by two messages."""

    def __init__(self, n=300, rows=20, title="Ana", per_day=25, first_day=date(2026, 9, 1)):
        self.title = title
        self.messages = []
        for i in range(n):
            day = date.fromordinal(first_day.toordinal() + i // per_day).isoformat()
            author = "user" if i % 3 == 0 else "contact"
            text = f"mensagem {i}"
            self.messages.append(
                (day, ChatLine(author, "", f"{8 + i % 12:02d}:{i % 60:02d}", "text", text, ""))
            )
        self.rows = rows
        self.bottom = n
        self.events = []
        self.stop_after_reads = None
        self.focus_after_reads = None
        self.title_after_reads = None
        self.reads = 0
        self.jump_at_read = None

    def read(self):
        self.reads += 1
        self.events.append(("read",))
        if self.jump_at_read == self.reads:
            self.bottom = max(self.rows, self.bottom - 40)
        if self.title_after_reads is not None and self.reads > self.title_after_reads:
            self.title = "Bia"
        top = max(0, self.bottom - self.rows)
        lines = []
        for k in range(top, self.bottom):
            _, line = self.messages[k]
            first_of_day = [
                j
                for j in range(top, k + 1)
                if j == 0 or self.messages[j - 1][0] != self.messages[j][0]
            ]
            shown_day = self.messages[first_of_day[-1]][0] if first_of_day else ""
            lines.append(ChatLine(**{**line.__dict__, "day": shown_day}))
        return ChatView(OpenChat(self.title, BOUNDS), tuple(lines))

    def scroll(self, notches):
        self.events.append(("scroll", notches))
        self.bottom = min(len(self.messages), max(self.rows, self.bottom - 2 * notches))

    def foreground(self):
        if self.focus_after_reads is not None and self.reads >= self.focus_after_reads:
            return 7
        return HWND

    def stopped(self):
        return self.stop_after_reads is not None and self.reads >= self.stop_after_reads

    def sleep(self, seconds):
        self.events.append(("sleep", seconds))

    def ports(self, rng=None):
        rng = rng or random.Random(1)
        return LoaderPorts(
            read=self.read,
            scroll=self.scroll,
            foreground=self.foreground,
            stopped=self.stopped,
            sleep=self.sleep,
            random=rng.uniform,
            today=lambda: date(2026, 11, 17),
        )

    def scrolled(self):
        return sum(n for kind, *rest in self.events if kind == "scroll" for n in rest)


def texts(report):
    return [line.text for line in report.lines]


def test_loads_until_the_limit_and_scrolls_back():
    chat = FakeChat()
    report = load(HWND, "Ana", LoadLimits(screens=5, months=0), chat.ports())
    assert report.reason == "limit" and report.screens == 5
    assert texts(report) == [f"mensagem {i}" for i in range(256, 300)]
    assert chat.scrolled() == 0 and chat.bottom == 300


def test_stops_at_the_start_of_the_chat():
    chat = FakeChat(n=60)
    report = load(HWND, "Ana", LoadLimits(screens=80, months=0), chat.ports())
    assert report.reason == "start" and report.reached_start
    assert texts(report) == [f"mensagem {i}" for i in range(60)]


def test_real_input_stops_before_the_next_event():
    chat = FakeChat()
    chat.stop_after_reads = 2
    report = load(HWND, "Ana", LoadLimits(screens=80, months=0), chat.ports())
    assert report.reason == "user"
    last_read = max(i for i, event in enumerate(chat.events) if event[0] == "read")
    assert all(event[0] != "scroll" for event in chat.events[last_read:])


def test_stop_keeps_what_was_read():
    chat = FakeChat()
    chat.stop_after_reads = 2
    report = load(HWND, "Ana", LoadLimits(screens=80, months=0), chat.ports())
    assert texts(report) == [f"mensagem {i}" for i in range(274, 300)]


def test_focus_loss_stops_without_scrolling_back():
    chat = FakeChat()
    chat.focus_after_reads = 3
    report = load(HWND, "Ana", LoadLimits(screens=80, months=0), chat.ports())
    assert report.reason == "focus" and chat.scrolled() > 0  # left where it was


def test_chat_change_stops_and_keeps_nothing_from_the_other_chat():
    chat = FakeChat()
    chat.title_after_reads = 2
    report = load(HWND, "Ana", LoadLimits(screens=80, months=0), chat.ports())
    assert report.reason == "chat_changed"
    assert texts(report) == [f"mensagem {i}" for i in range(274, 300)]
    other = FakeChat(title="Bia")
    first = load(HWND, "Ana", LoadLimits(screens=80, months=0), other.ports())
    assert first.reason == "chat_changed" and first.lines == ()


def test_private_contact_loads_nothing():
    chat = FakeChat()
    report = load(
        HWND, "Ana", LoadLimits(screens=80, months=0), chat.ports(), private=lambda title: True
    )
    assert report.reason == "private" and report.lines == ()
    assert not any(event[0] == "scroll" for event in chat.events)


def test_months_limit():
    chat = FakeChat(per_day=10)  # 30 days of messages, from 2026-09-01
    report = load(HWND, "Ana", LoadLimits(screens=80, months=2), chat.ports())
    assert report.reason == "months"
    assert min(line.day for line in report.lines if line.day) < months_before(date(2026, 11, 17), 2)
    assert chat.bottom == 300


def test_joins_stops_in_gap_mode():
    chat = FakeChat()
    report = load(
        HWND,
        "Ana",
        LoadLimits(screens=80, months=0, stop_on_join=True),
        chat.ports(),
        joins=lambda block: any(line.text == "mensagem 250" for line in block),
    )
    assert report.reason == "joined" and "mensagem 250" in texts(report)


def test_lost_continuity_is_reported():
    chat = FakeChat()
    chat.jump_at_read = 3
    report = load(HWND, "Ana", LoadLimits(screens=80, months=0), chat.ports())
    assert report.reason == "lost"
    assert texts(report) == [f"mensagem {i}" for i in range(274, 300)]


def test_only_scroll_events_are_sent():
    chat = FakeChat(n=60)
    load(HWND, "Ana", LoadLimits(screens=80, months=0), chat.ports())
    assert {event[0] for event in chat.events} <= {"read", "scroll", "sleep"}


def test_pace_stays_human():
    chat = FakeChat()
    report = load(HWND, "Ana", LoadLimits(screens=6, months=0), chat.ports(random.Random(9)))
    sleeps = [event[1] for event in chat.events if event[0] == "sleep"]
    assert all(0.1 <= s <= 1.3 for s in sleeps)
    assert sum(1 for s in sleeps if s >= 0.7) == report.screens - 1


def test_a_window_over_the_chat_stops_before_any_scroll():
    # Wheel events go to the window under the cursor, whatever is in front.
    chat = FakeChat()
    ports = chat.ports()
    covered = LoaderPorts(**{**ports.__dict__, "under_cursor": lambda: False})
    report = load(HWND, "Ana", LoadLimits(screens=80, months=0), covered)
    assert report.reason == "covered" and chat.scrolled() == 0


def test_months_before():
    assert months_before(date(2026, 11, 17), 6) == "2026-05-17"
    assert months_before(date(2026, 3, 31), 1) == "2026-02-28"
