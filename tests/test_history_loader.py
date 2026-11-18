import random
from datetime import date

from respondoia.domain import ChatLine, ChatView, OpenChat, Rect
from respondoia.memory.loader import BACKGROUND_PACE, LoaderPorts, LoadLimits, load, months_before

HWND = 42
BOUNDS = Rect(0, 0, 800, 900)


class FakeChat:
    """A fictional chat of `n` messages over several days. The window shows `rows` lines;
    each notch moves it by two messages."""

    def __init__(self, n=300, rows=20, title="Ana", per_day=25, first_day=date(2026, 9, 1), step=2):
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
        self.step = step  # messages per notch
        self.stall_reads = set()  # reads after which WhatsApp is still loading: no movement

    def read(self):
        self.reads += 1
        self.events.append(("read",))
        if self.jump_at_read == self.reads:
            self.bottom = max(self.rows, self.bottom - 80)
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
        if notches > 0 and self.reads in self.stall_reads:
            return
        self.bottom = min(len(self.messages), max(self.rows, self.bottom - self.step * notches))

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


def contiguous(report, end=300):
    found = texts(report)
    return found == [f"mensagem {i}" for i in range(end - len(found), end)]


def bursts(chat):
    """Notches sent between two reads, in order."""
    out, current = [], 0
    for event in chat.events:
        if event[0] == "scroll" and event[1] > 0:
            current += 1
        elif event[0] == "read" and current:
            out.append(current)
            current = 0
    return out


def test_loads_until_the_limit_and_scrolls_back():
    chat = FakeChat()
    report = load(HWND, "Ana", LoadLimits(screens=5, months=0), chat.ports())
    assert report.reason == "limit" and report.screens == 5
    assert contiguous(report) and len(report.lines) > 40
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


def test_pace_is_quick_but_never_instant():
    chat = FakeChat()
    report = load(HWND, "Ana", LoadLimits(screens=6, months=0), chat.ports(random.Random(9)))
    sleeps = [event[1] for event in chat.events if event[0] == "sleep"]
    assert all(0.02 <= s <= 0.6 for s in sleeps)
    assert sum(1 for s in sleeps if s >= 0.35) == report.screens - 1


def test_steps_grow_while_screens_overlap_a_lot():
    chat = FakeChat(n=600, rows=40)
    report = load(HWND, "Ana", LoadLimits(screens=12, months=0), chat.ports())
    assert contiguous(report, 600) and bursts(chat)[0] == 3 and max(bursts(chat)) == 6


def test_steps_shrink_when_the_overlap_gets_thin():
    chat = FakeChat(rows=20, step=6)  # 3 notches leave an overlap of 2
    report = load(HWND, "Ana", LoadLimits(screens=8, months=0), chat.ports())
    assert contiguous(report) and report.reason == "limit" and min(bursts(chat)) == 2


def test_a_slow_screen_is_not_the_start():
    chat = FakeChat()
    chat.stall_reads = {2, 3, 4}  # WhatsApp still loading older messages
    report = load(HWND, "Ana", LoadLimits(screens=10, months=0), chat.ports())
    assert report.reason == "limit" and not report.reached_start and contiguous(report)


def test_scroll_back_is_quick():
    chat = FakeChat()
    load(HWND, "Ana", LoadLimits(screens=5, months=0), chat.ports())
    last_read = max(i for i, event in enumerate(chat.events) if event[0] == "read")
    back = [e[1] for e in chat.events[last_read:] if e[0] == "sleep"]
    assert back and all(s <= 0.05 for s in back) and chat.bottom == 300


def test_a_window_over_the_chat_stops_before_any_scroll():
    # Wheel events go to the window under the cursor, whatever is in front.
    chat = FakeChat()
    ports = chat.ports()
    covered = LoaderPorts(**{**ports.__dict__, "under_cursor": lambda: False})
    report = load(HWND, "Ana", LoadLimits(screens=80, months=0), covered)
    assert report.reason == "covered" and chat.scrolled() == 0


def background_ports(chat):
    """Scrolling through accessibility: no wheel events, no pause per notch, the screen is
    read as soon as it changes, and one call puts the list back at the bottom."""
    ports = chat.ports()
    chat.bottom_calls = 0

    def bottom():
        chat.bottom_calls += 1
        chat.bottom = len(chat.messages)

    return LoaderPorts(**{**ports.__dict__, "pace": BACKGROUND_PACE, "scroll_bottom": bottom})


def test_background_loading_reads_as_soon_as_the_screen_changes():
    chat = FakeChat()
    report = load(HWND, "Ana", LoadLimits(screens=10, months=0), background_ports(chat))
    assert report.reason == "limit" and contiguous(report)
    sleeps = [event[1] for event in chat.events if event[0] == "sleep"]
    assert all(s <= 0.05 for s in sleeps)
    assert chat.bottom_calls == 1 and chat.bottom == 300


def test_background_loading_waits_for_a_slow_screen():
    chat = FakeChat()
    chat.stall_reads = {2}
    report = load(HWND, "Ana", LoadLimits(screens=6, months=0), background_ports(chat))
    assert report.reason == "limit" and not report.reached_start and contiguous(report)


def test_background_loading_reaches_the_start():
    chat = FakeChat(n=60)
    report = load(HWND, "Ana", LoadLimits(screens=80, months=0), background_ports(chat))
    assert report.reason == "start" and len(report.lines) == 60 and chat.bottom_calls == 1


def test_a_minimized_window_stops_the_background_load():
    # Minimized, WhatsApp stops drawing: scrolls do nothing and the screen stays frozen.
    chat = FakeChat()
    ports = background_ports(chat)
    ports = LoaderPorts(**{**ports.__dict__, "visible": lambda: chat.reads < 3})
    report = load(HWND, "Ana", LoadLimits(screens=20, months=0), ports)
    assert report.reason == "minimized" and contiguous(report)


def test_a_momentary_empty_read_is_waited_out():
    chat = FakeChat()
    ports = background_ports(chat)
    real_read = ports.read
    empty_at = {3}

    def flaky():
        view = real_read()
        return None if chat.reads in empty_at else view

    ports = LoaderPorts(**{**ports.__dict__, "read": flaky})
    report = load(HWND, "Ana", LoadLimits(screens=6, months=0), ports)
    assert report.reason == "limit" and contiguous(report)


class PixelChat(FakeChat):
    """Each notch moves the list 10 px; a new message shows up only every 4 notches, as
    when photos the reader cannot see fill the top of the screen."""

    def __init__(self):
        super().__init__(n=200, rows=10)
        self.offset = 0

    def scroll(self, notches):
        self.events.append(("scroll", notches))
        self.offset = max(0, self.offset + 10 * notches)

    def read(self):
        self.reads += 1
        self.events.append(("read",))
        shift = self.offset // 40
        bottom = max(self.rows, len(self.messages) - shift)
        lines = []
        for k in range(bottom - self.rows, bottom):
            _, line = self.messages[k]
            top = 100 + (k - bottom + self.rows) * 40 + self.offset % 40
            lines.append(ChatLine(**{**line.__dict__, "rect": Rect(500, top, 900, top + 35)}))
        return ChatView(OpenChat(self.title, BOUNDS), tuple(lines))


def test_a_screen_that_moved_is_read_at_once_even_with_the_same_first_line():
    chat = PixelChat()
    ports = background_ports(chat)
    load(HWND, "Ana", LoadLimits(screens=6, months=0), ports)
    waits = [e[1] for e in chat.events if e[0] == "sleep"]
    assert sum(waits) < 0.5  # never the 1.5 s wait for a screen that did move


class PhotoChat(FakeChat):
    """Stretches of photos the reader cannot see: only some rows have readable lines."""

    def __init__(self, hidden):
        super().__init__(n=300, rows=20)
        self.hidden = hidden

    def read(self):
        view = super().read()
        lines = tuple(line for line in view.lines if int(line.text.split()[-1]) not in self.hidden)
        return ChatView(view.chat, lines)


def test_photo_stretches_do_not_lose_the_thread():
    hidden = {i for i in range(300) if 200 <= i < 216 or 120 <= i < 135}
    chat = PhotoChat(hidden)
    report = load(HWND, "Ana", LoadLimits(screens=200, months=0), background_ports(chat))
    shown = [i for i in range(300) if i not in hidden]
    assert report.reason == "start"
    assert texts(report) == [f"mensagem {i}" for i in shown]


def test_older_messages_arriving_do_not_lose_the_thread():
    # WhatsApp inserts older messages and the list jumps further than one step back.
    chat = FakeChat()
    original = chat.read

    def read():
        if chat.reads == 4:
            chat.bottom = max(chat.rows, chat.bottom - 30)
        return original()

    chat.read = read
    report = load(HWND, "Ana", LoadLimits(screens=12, months=0), background_ports(chat))
    assert report.reason == "limit" and contiguous(report)


def test_months_before():
    assert months_before(date(2026, 11, 17), 6) == "2026-05-17"
    assert months_before(date(2026, 3, 31), 1) == "2026-02-28"


class LookChat(FakeChat):
    """Five texts, then forty photos of two shapes with no time and no text: the stretch
    that stopped the 0.4 loader. Each photo has its own fictional look."""

    def __init__(self):
        super().__init__(n=45)
        rng = random.Random(7)
        self.look = {}
        for i in range(5, 45):
            while True:  # far from every other photo, as two real ones are (T0: 29 bits)
                value = rng.getrandbits(64)
                if all((value ^ int(o, 16)).bit_count() > 20 for o in self.look.values()):
                    break
            self.look[i] = f"{value:016x}"
            line = ChatLine("contact", "", "", "image", "", "", shape="1.3" if i % 2 else "0.7")
            self.messages[i] = (self.messages[i][0], line)
        self.asked = []  # rects asked per call
        self.asked_messages = []  # which messages they showed
        self.on_screen = {}

    def read(self):
        view = super().read()
        top = max(0, self.bottom - self.rows)
        lines = []
        self.on_screen = {}
        for row, line in enumerate(view.lines):
            rect = Rect(10, 40 * row, 310, 40 * row + 38)
            self.on_screen[rect] = top + row
            lines.append(ChatLine(**{**line.__dict__, "rect": rect}))
        chat = OpenChat(self.title, BOUNDS, messages=Rect(0, 0, 800, 900))
        return ChatView(chat, tuple(lines))

    def looks(self, rects):
        self.asked.append(tuple(rects))
        self.asked_messages.extend(self.on_screen[r] for r in rects)
        return tuple(self.look.get(self.on_screen[r], "") for r in rects)

    def ports(self, rng=None, with_looks=True):
        ports = super().ports(rng)
        if not with_looks:
            return ports
        return LoaderPorts(**{**ports.__dict__, "looks": self.looks})


def test_photo_stretch_loads_to_the_start():
    chat = LookChat()
    report = load(HWND, "Ana", LoadLimits(screens=80, months=0), chat.ports())
    assert report.reason == "start" and len(report.lines) == 45
    assert [line.text for line in report.lines[:5]] == [f"mensagem {i}" for i in range(5)]
    assert report.looks[5:] == tuple(chat.look[i] for i in range(5, 45))
    assert report.looks[:5] == ("",) * 5


def test_photo_stretch_without_looks_stops_lost():
    # The documented limit when looks cannot be taken: the loader stops, it never guesses.
    chat = LookChat()
    report = load(HWND, "Ana", LoadLimits(screens=80, months=0), chat.ports(with_looks=False))
    assert report.reason == "lost"


def test_looks_are_asked_only_for_screens_with_bare_media():
    chat = LookChat()
    chat.messages = chat.messages[:5] + [
        (day, ChatLine(**{**line.__dict__, "text": "legenda"})) for day, line in chat.messages[5:]
    ]
    load(HWND, "Ana", LoadLimits(screens=5, months=0), chat.ports())
    assert chat.asked == []
    photos = LookChat()
    load(HWND, "Ana", LoadLimits(screens=80, months=0), photos.ports())
    # Every photo, never one of the five texts.
    assert set(photos.asked_messages) == set(range(5, 45))


def test_pictures_cut_by_the_message_area_get_no_look():
    chat = LookChat()
    original = chat.read

    def read():
        view = original()
        area = Rect(0, 100, 800, 900)  # the first rows sit under the header
        return ChatView(OpenChat(view.chat.title, BOUNDS, messages=area), view.lines)

    chat.read = read
    load(HWND, "Ana", LoadLimits(screens=2, months=0), chat.ports())
    assert all(rect.top >= 100 for rects in chat.asked for rect in rects)
