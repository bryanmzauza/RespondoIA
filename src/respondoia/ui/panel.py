"""Suggestion panel."""

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from respondoia.domain import Rect
from respondoia.i18n import t
from respondoia.llm import Suggestion, SuggestionPreview, SuggestionReply

COMPOSER_CLEARANCE = 72  # logical pixels kept free above the bottom of the chat area
HEADER_CLEARANCE = 72  # logical pixels kept free below the top of the chat area
MARGIN = 12
WIDTH = 420
MAX_OPTIONS = 3
_STYLE = """
#panel { background: palette(window); border: 1px solid palette(mid); }
QPushButton#option { text-align: left; padding: 6px 8px; }
"""


def to_logical(rect: Rect, device_pixel_ratio: float) -> Rect:
    return Rect(
        round(rect.left / device_pixel_ratio),
        round(rect.top / device_pixel_ratio),
        round(rect.right / device_pixel_ratio),
        round(rect.bottom / device_pixel_ratio),
    )


def panel_position(
    chat: Rect, panel_size: tuple[int, int], screen: Rect, corner: str = "bottom-right"
) -> tuple[int, int]:
    """A corner of the chat area, clear of the header and the message box, on screen."""
    width, height = panel_size
    if corner.endswith("left"):
        x = chat.left + MARGIN
    else:
        x = chat.right - width - MARGIN
    if corner.startswith("top"):
        y = chat.top + HEADER_CLEARANCE
    else:
        y = chat.bottom - COMPOSER_CLEARANCE - height
    x = max(screen.left, min(x, screen.right - width))
    y = max(screen.top, min(y, screen.bottom - height))
    return x, y


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _no_mnemonic(text: str) -> str:
    # Buttons and check boxes read "&x" as a keyboard shortcut and hide the "&".
    return text.replace("&", "&&")


class SuggestionPanel(QWidget):
    suggestion_chosen = Signal(int)  # 0-based index
    regenerate_requested = Signal(tuple, str)  # selected message ids, instruction
    mark_private_requested = Signal()
    retry_requested = Signal()
    closed = Signal()
    settings_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("panel")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet(_STYLE)
        self.setFixedWidth(WIDTH)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._reply: SuggestionReply | None = None  # with the message list
        self._choices: list[Suggestion] | None = None  # the options on screen
        self._anchor: tuple[Rect, Rect] | None = None  # chat area and screen, logical pixels
        self._message_ids: list[int] = []
        self._corner = "bottom-right"

        self.title_label = QLabel()
        title_font = self.title_label.font()
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)
        self.esc_label = QLabel()
        self.settings_button = QPushButton()
        self.settings_button.setFlat(True)
        self.settings_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.settings_button.clicked.connect(self.settings_requested)
        header = QHBoxLayout()
        header.addWidget(self.title_label, 1)
        header.addWidget(self.settings_button)
        header.addWidget(self.esc_label)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.retry_button = QPushButton()
        self.retry_button.clicked.connect(self.retry_requested)
        # Chat and model text is shown as written, never as rich text.
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        self.summary_label.setTextFormat(Qt.TextFormat.PlainText)
        self.note_label = QLabel()
        self.note_label.setWordWrap(True)
        self.note_label.setTextFormat(Qt.TextFormat.PlainText)

        self.option_buttons: list[QPushButton] = []
        for index in range(MAX_OPTIONS):
            button = QPushButton()
            button.setObjectName("option")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _=False, i=index: self._choose(i))
            self.option_buttons.append(button)

        self.adjust_button = QPushButton()
        self.adjust_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.adjust_button.clicked.connect(self._toggle_details)

        self.details = QWidget()
        self.messages_label = QLabel()
        self.message_boxes: list[QCheckBox] = []
        self._boxes_layout = QVBoxLayout()
        self._boxes_layout.setContentsMargins(0, 0, 0, 0)
        self.instruction = QLineEdit()
        self.instruction.returnPressed.connect(self._request_regenerate)
        self.regenerate_button = QPushButton()
        self.regenerate_button.clicked.connect(self._request_regenerate)
        self.private_button = QPushButton()
        self.private_button.clicked.connect(self.mark_private_requested)
        instruction_row = QHBoxLayout()
        instruction_row.addWidget(self.instruction, 1)
        instruction_row.addWidget(self.regenerate_button)
        details_layout = QVBoxLayout(self.details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.addWidget(self.messages_label)
        details_layout.addLayout(self._boxes_layout)
        self.instruction_buttons: list[QPushButton] = []
        self._instructions_layout = QGridLayout()
        self._instructions_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.addLayout(self._instructions_layout)
        details_layout.addLayout(instruction_row)
        details_layout.addWidget(self.private_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        layout.addLayout(header)
        layout.addWidget(self.status_label)
        layout.addWidget(self.retry_button)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.note_label)
        for button in self.option_buttons:
            layout.addWidget(button)
        layout.addWidget(self.adjust_button)
        layout.addWidget(self.details)

    # Public interface

    def show_loading(self, chat_title: str, chat_bounds: Rect) -> None:
        """Open, or stay open, in the loading state. Bounds are in screen pixels."""
        self._retranslate()
        self._reply = None
        self._choices = None
        self._anchor = self._logical_anchor(chat_bounds)
        self.title_label.setText(chat_title)
        self._set_status(t("panel.loading"))
        self.retry_button.hide()
        for widget in (self.summary_label, self.note_label, self.adjust_button, self.details):
            widget.hide()
        for button in self.option_buttons:
            button.hide()
        self._place()
        self.show()
        self.bring_to_front()

    def show_preview(self, preview: SuggestionPreview) -> None:
        """Summary and options while the message list is still arriving."""
        self._show_options(preview.summary, preview.suggestions)
        self.note_label.hide()
        self.adjust_button.hide()
        self.details.hide()
        self._place()
        self.setFocus()

    def show_reply(self, reply: SuggestionReply) -> None:
        self._reply = reply
        self._show_options(reply.summary, reply.suggestions)
        self.note_label.setVisible(bool(reply.note))
        if reply.note:
            self.note_label.setText(t("panel.note") + ": " + reply.note)
        self._fill_messages(reply)
        self.instruction.clear()
        self.adjust_button.show()
        self.details.hide()
        self._place()
        self.setFocus()

    def _show_options(self, summary: str, suggestions: list[Suggestion]) -> None:
        self._choices = suggestions
        self.status_label.hide()
        self.retry_button.hide()
        self.summary_label.setText(t("panel.summary") + ": " + summary)
        self.summary_label.show()
        available = WIDTH - 2 * MARGIN - 24
        for index, button in enumerate(self.option_buttons):
            if index >= len(suggestions):
                button.hide()
                continue
            suggestion = suggestions[index]
            line = f"{index + 1}  {suggestion.label}  {_one_line(suggestion.text)}"
            elided = button.fontMetrics().elidedText(line, Qt.TextElideMode.ElideRight, available)
            button.setText(_no_mnemonic(elided))
            button.setToolTip(suggestion.text)
            button.show()

    def show_error(self, code: str) -> None:
        """Show the translated error and the retry button. Options become unavailable."""
        self._reply = None
        self._choices = None
        for button in self.option_buttons:
            button.hide()
        for widget in (self.summary_label, self.note_label, self.adjust_button, self.details):
            widget.hide()
        self._set_status(t("error." + code))
        self.retry_button.show()
        self._place()

    def show_notice(self, key: str) -> None:
        self._set_status(t(key))
        self._place()

    def set_corner(self, corner: str) -> None:
        self._corner = corner
        self._place()

    def set_quick_instructions(self, items: Sequence[tuple[str, str]]) -> None:
        """Buttons in the adjust view: (label, instruction sent to the model)."""
        for button in self.instruction_buttons:
            self._instructions_layout.removeWidget(button)
            button.deleteLater()
        self.instruction_buttons = []
        for index, (label, instruction) in enumerate(items):
            button = QPushButton(_no_mnemonic(label))
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(
                lambda _=False, text=instruction: self._request_regenerate(text)
            )
            self._instructions_layout.addWidget(button, index // 2, index % 2)
            self.instruction_buttons.append(button)

    def bring_to_front(self) -> None:
        """Raise and focus the panel if it is open. A hidden panel stays hidden."""
        if self.isVisible():
            self.raise_()
            self.activateWindow()
            self.setFocus()

    def dismiss(self) -> None:
        """Hide without emitting closed."""
        self.hide()

    # Keys

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.hide()
            self.closed.emit()
        elif Qt.Key.Key_1 <= key <= Qt.Key.Key_9:
            self._choose(key - Qt.Key.Key_1)
        else:
            super().keyPressEvent(event)

    def focusNextPrevChild(self, forward: bool) -> bool:
        # Qt routes Tab here from the panel and from every child: Tab switches modes.
        self._toggle_details()
        return True

    # Internals

    def _retranslate(self) -> None:
        self.esc_label.setText(t("panel.esc"))
        self.settings_button.setText(t("panel.settings"))
        self.retry_button.setText(t("panel.retry"))
        self.adjust_button.setText(t("panel.adjust"))
        self.messages_label.setText(t("panel.messages"))
        self.instruction.setPlaceholderText(t("panel.instruction_placeholder"))
        self.regenerate_button.setText(t("panel.regenerate"))
        self.private_button.setText(t("panel.mark_private"))

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.show()

    def _choose(self, index: int) -> None:
        if self._choices is not None and index < len(self._choices):
            self.suggestion_chosen.emit(index)

    def _toggle_details(self) -> None:
        if self._reply is None:
            return
        expand = self.details.isHidden()
        self.details.setVisible(expand)
        self._place()
        if expand:
            self.instruction.setFocus()
        else:
            self.setFocus()

    def _fill_messages(self, reply: SuggestionReply) -> None:
        for box in self.message_boxes:
            self._boxes_layout.removeWidget(box)
            box.deleteLater()
        self.message_boxes = []
        self._message_ids = []
        for message in reply.messages:
            box = QCheckBox()
            box.setText(_no_mnemonic(_one_line(message.text)))
            box.setChecked(message.pending)
            self._boxes_layout.addWidget(box)
            self.message_boxes.append(box)
            self._message_ids.append(message.id)

    def _request_regenerate(self, instruction: str | None = None) -> None:
        if self._reply is None:
            return
        selected = tuple(
            message_id
            for message_id, box in zip(self._message_ids, self.message_boxes)
            if box.isChecked()
        )
        if instruction is None:
            instruction = self.instruction.text().strip()
        self.regenerate_requested.emit(selected, instruction)

    def _logical_anchor(self, bounds: Rect) -> tuple[Rect, Rect]:
        """Chat bounds in screen pixels -> chat area and its screen in Qt's logical pixels."""
        center_x = (bounds.left + bounds.right) // 2
        center_y = (bounds.top + bounds.bottom) // 2
        chosen = QGuiApplication.primaryScreen()
        for screen in QGuiApplication.screens():
            geometry, ratio = screen.geometry(), screen.devicePixelRatio()
            inside_x = geometry.x() <= center_x < geometry.x() + geometry.width() * ratio
            inside_y = geometry.y() <= center_y < geometry.y() + geometry.height() * ratio
            if inside_x and inside_y:
                chosen = screen
                break
        geometry, ratio = chosen.geometry(), chosen.devicePixelRatio()
        # A screen's origin is the same in both systems; only distances inside it scale.
        origin_x, origin_y = geometry.x(), geometry.y()
        relative = Rect(
            bounds.left - origin_x,
            bounds.top - origin_y,
            bounds.right - origin_x,
            bounds.bottom - origin_y,
        )
        local = to_logical(relative, ratio)
        chat = Rect(
            local.left + origin_x,
            local.top + origin_y,
            local.right + origin_x,
            local.bottom + origin_y,
        )
        screen_rect = Rect(
            origin_x, origin_y, origin_x + geometry.width(), origin_y + geometry.height()
        )
        return chat, screen_rect

    def _place(self) -> None:
        if self._anchor is None:
            return
        self.adjustSize()
        chat, screen = self._anchor
        self.move(*panel_position(chat, (self.width(), self.height()), screen, self._corner))
