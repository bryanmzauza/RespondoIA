"""Plain data shared by the modules. No Qt and no Win32 here."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    exe: str
    rect: Rect
    minimized: bool


@dataclass(frozen=True)
class OpenChat:
    title: str  # name at the top of the open chat, as displayed
    bounds: Rect  # open chat area in screen pixels
    avatar: Rect | None = None  # the contact's picture in the header, screen pixels


@dataclass(frozen=True)
class ChatRef:
    hwnd: int
    title: str


@dataclass(frozen=True)
class ChatLine:
    author: str  # "user" or "contact"
    sender: str  # group sender as shown, "" in one-to-one chats and for the user
    time: str  # as shown ("21:47", "9:47 PM"), "" if not found
    kind: str  # "text", "link", "image", "video", "sticker", "document", "audio" or "other"
    text: str  # message text or caption, an audio transcript; "" for media without any
    quote: str  # text of the quoted message, "" if none
    # One text and nothing else besides time and quote. Forwarded messages, link
    # previews and cards carry other people's text, so only plain lines are learned.
    plain: bool = True
    day: str = ""  # ISO date from a day separator above it on this screen, "" if none
    rect: Rect | None = None  # the bubble, in screen pixels
    duration: str = ""  # audio or video length as shown ("0:42"), "" if none
    site: str = ""  # domain shown in a link preview, "" if none
    shape: str = ""  # height / width of a picture without text nodes, one decimal ("1.4")
    transcript: bool = False  # text is WhatsApp's own transcript of an audio


# Kinds a picture of the bubble can show: cropped for the request, hashed for the history.
PICTURE_KINDS = ("image", "video")


def family(kind: str) -> str:
    """Kinds that can be the same message read twice: a link read as text before, a video
    read as a bare picture."""
    if kind in ("text", "link"):
        return "text"
    return "audio" if kind == "audio" else "media"


@dataclass(frozen=True)
class ChatView:
    chat: OpenChat
    lines: tuple[ChatLine, ...]  # visible messages, oldest first


# Contact labels. Their names on screen come from the translations.
LABELS = ("client", "supplier", "friend", "family")


@dataclass(frozen=True)
class UserContext:
    """What the app knows about the user and the contact, added to each request."""

    language: str | None
    label: str | None  # label in use; None without a unique contact or a label
    label_from_user: bool  # False for a model guess
    ask_label: bool  # no label yet, not a group: the model may guess one
    style: str  # rendered "Style" section, "" without a profile
    knowledge: str  # knowledge.md text, "" without one
    memory: str = ""  # contact memory, masked; "" unless the contact was told apart
    snippets: tuple[dict, ...] = ()  # earlier messages: day, time, author, text; oldest first
