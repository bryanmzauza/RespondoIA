from respondoia.config import Settings
from respondoia.domain import ChatLine, OpenChat, Rect
from respondoia.media import may_send_pictures, pending_pictures

BOUNDS = Rect(420, 0, 1600, 900)
AREA = Rect(420, 65, 1600, 840)
CHAT = OpenChat("Tânia Prado", BOUNDS, messages=AREA)


def photo(author="contact", kind="image", sender="", top=100):
    return ChatLine(author, sender, "", kind, "", "", rect=Rect(485, top, 785, top + 200), shape="0.7")


def text(author="user", sender=""):
    return ChatLine(author, sender, "09:00", "text", "manda as fotos", "")


def test_only_pending_contact_pictures_are_cropped():
    lines = [photo(), text(), photo(), photo(kind="video"), photo()]
    assert pending_pictures(lines) == (3, 4)  # the two newest after the user's reply


def test_user_pictures_are_never_pending():
    assert pending_pictures([text(), photo(author="user")]) == ()


def test_stickers_and_documents_are_never_pictures():
    sticker = ChatLine("contact", "", "", "sticker", "", "", rect=Rect(485, 100, 675, 290))
    document = ChatLine("contact", "", "09:08", "document", "Orçamento", "", rect=Rect(485, 300, 821, 406))
    assert pending_pictures([text(), sticker, document]) == ()


def test_picture_without_rect_is_not_pending():
    assert pending_pictures([text(), ChatLine("contact", "", "", "image", "", "")]) == ()


def test_pictures_may_go_in_a_one_to_one_chat():
    assert may_send_pictures(Settings(), CHAT, [text(), photo()], known_group=False)


def test_group_without_sender_on_screen_sends_no_picture():
    # A known group: this screen shows only photos, none with the sender's name.
    assert not may_send_pictures(Settings(), CHAT, [photo(), photo()], known_group=True)


def test_sender_on_screen_sends_no_picture():
    lines = [text(author="contact", sender="Bia Rocha"), photo()]
    assert not may_send_pictures(Settings(), CHAT, lines, known_group=False)


def test_known_group_sends_no_picture():
    assert not may_send_pictures(Settings(), CHAT, [text(), photo()], known_group=True)


def test_switch_off_sends_no_picture():
    settings = Settings(send_pictures=False)
    assert not may_send_pictures(settings, CHAT, [text(), photo()], known_group=False)


def test_screenshot_mode_sends_no_picture():
    settings = Settings(input="screenshot")
    assert not may_send_pictures(settings, CHAT, [text(), photo()], known_group=False)


def test_unknown_message_area_sends_no_picture():
    chat = OpenChat("Tânia Prado", BOUNDS)
    assert not may_send_pictures(Settings(), chat, [text(), photo()], known_group=False)
