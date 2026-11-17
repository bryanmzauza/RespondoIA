"""Suggestion panel."""

from collections.abc import Sequence
from datetime import date

from PySide6.QtCore import QDate, QLocale, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from respondoia.domain import LABELS, Rect
from respondoia.i18n import t
from respondoia.llm import Suggestion, SuggestionPreview, SuggestionReply
from respondoia.memory.service import HistoryContext

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


def _day(iso: str) -> str:
    """An ISO date in the short format of the interface language."""
    parsed = QDate.fromString(iso, Qt.DateFormat.ISODate)
    if not parsed.isValid():
        return iso
    return QLocale().toString(parsed, QLocale.FormatType.ShortFormat)


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
    label_chosen = Signal(object)  # label, or None for no label
    style_requested = Signal()
    load_requested = Signal(str)  # "quick", "full" or "gap"
    identity_answered = Signal(object)  # stored contact id, or None for another person
    memory_requested = Signal()
    contact_history_toggled = Signal(bool)
    contact_history_deleted = Signal()

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
        self._history: HistoryContext | None = None

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
        self.label_button = QPushButton()
        self.label_button.setFlat(True)
        self.label_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.label_button.setMenu(QMenu(self.label_button))
        self.label_button.hide()
        header = QHBoxLayout()
        header.addWidget(self.title_label, 1)
        header.addWidget(self.label_button)
        header.addWidget(self.settings_button)
        header.addWidget(self.esc_label)

        # Whose memory is in use, and how much is stored. Names are shown as written.
        self.history_label = QLabel()
        self.history_label.setWordWrap(True)
        self.history_label.setTextFormat(Qt.TextFormat.PlainText)
        self.history_label.hide()
        self.history_button = QPushButton()
        self.history_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.history_button.setMenu(QMenu(self.history_button))
        self.history_button.hide()
        self.ask_box = QWidget()
        self.ask_label = QLabel()
        self.ask_label.setWordWrap(True)
        self.ask_label.setTextFormat(Qt.TextFormat.PlainText)
        self.ask_buttons: list[QPushButton] = []
        self._ask_layout = QHBoxLayout()
        self._ask_layout.setContentsMargins(0, 0, 0, 0)
        ask_layout = QVBoxLayout(self.ask_box)
        ask_layout.setContentsMargins(0, 0, 0, 0)
        ask_layout.addWidget(self.ask_label)
        ask_layout.addLayout(self._ask_layout)
        self.ask_box.hide()

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
        self.style_button = QPushButton()
        self.style_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.style_button.clicked.connect(self.style_requested)
        footer = QHBoxLayout()
        footer.addWidget(self.adjust_button, 1)
        footer.addWidget(self.history_button)
        footer.addWidget(self.style_button)

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
        layout.addWidget(self.history_label)
        layout.addWidget(self.ask_box)
        layout.addWidget(self.status_label)
        layout.addWidget(self.retry_button)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.note_label)
        for button in self.option_buttons:
            layout.addWidget(button)
        layout.addLayout(footer)
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
        for widget in (
            self.summary_label,
            self.note_label,
            self.adjust_button,
            self.style_button,
            self.details,
        ):
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
        self.style_button.show()
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
        for widget in (
            self.summary_label,
            self.note_label,
            self.adjust_button,
            self.style_button,
            self.details,
        ):
            widget.hide()
        self._set_status(t("error." + code))
        self.retry_button.show()
        self._place()

    def show_notice(self, key: str) -> None:
        self._set_status(t(key))
        self._place()

    def set_label(self, label: str | None, guessed: bool) -> None:
        """The contact's label in the header. A model guess is shown with a question mark."""
        name = t("label." + label) if label in LABELS else t("label.none")
        if guessed and label in LABELS:
            name = t("label.guessed", label=name)
        self.label_button.setText(_no_mnemonic(name))
        menu = self.label_button.menu()
        menu.clear()
        for value in (*LABELS, None):
            action = menu.addAction(t("label." + value) if value else t("label.none"))
            action.setData(value)
            action.triggered.connect(lambda _=False, v=value: self._choose_label(v))
        self.label_button.show()

    def set_history(self, history: HistoryContext | None) -> None:
        """The history state of the open chat. Nothing shows while the history is off."""
        self._history = history
        state = history.state if history is not None else "off"
        self.history_label.setVisible(state == "ready")
        self.history_button.setVisible(state in ("ready", "out_of_scope"))
        self.ask_box.setVisible(state == "ask")
        if state == "ready":
            self.history_label.setText(self._history_line(history))
        if state in ("ready", "out_of_scope"):
            self._fill_history_menu(history)
        if state == "ask":
            self._fill_question(history)
        self._place()

    def _history_line(self, history: HistoryContext) -> str:
        if history.gap_since:
            line = t("history.gap", day=_day(history.gap_since))
        elif not history.messages:
            line = t("history.none")
        else:
            today = not history.last_day or history.last_day == date.today().isoformat()
            when = t("history.today") if today else _day(history.last_day)
            line = t("history.status", count=history.messages, day=when)
        if history.owner_since:
            owner = t("history.owner", name=history.owner, day=_day(history.owner_since))
            line = owner + " · " + line
        return line

    def _fill_history_menu(self, history: HistoryContext) -> None:
        self.history_button.setText(t("history.menu"))
        menu = self.history_button.menu()
        menu.clear()
        if history.state == "out_of_scope":
            entries = [("contact_on", "history.contact_on")]
        else:
            entries = [
                ("load_quick", "history.load_quick"),
                ("load_full", "history.load_full"),
            ]
            if history.gap_since:
                entries.append(("load_gap", "history.load_gap"))
            entries += [
                ("view_memory", "history.view_memory"),
                ("contact_off", "history.contact_off"),
                ("contact_delete", "history.contact_delete"),
            ]
        for value, key in entries:
            action = menu.addAction(t(key))
            action.setData(value)
            action.triggered.connect(lambda _=False, v=value: self._history_action(v))

    def _history_action(self, value: str) -> None:
        owner = self._history.owner if self._history is not None else ""
        if value.startswith("load_"):
            self.load_requested.emit(value.removeprefix("load_"))
        elif value == "view_memory":
            self.memory_requested.emit()
        elif value == "contact_on":
            self.contact_history_toggled.emit(True)
        elif value == "contact_off" and self._confirm(
            t("history.contact_off_confirm", name=owner)
        ):
            self.contact_history_toggled.emit(False)
        elif value == "contact_delete" and self._confirm(
            t("history.contact_delete_confirm", name=owner)
        ):
            self.contact_history_deleted.emit()

    def _confirm(self, text: str) -> bool:
        answer = QMessageBox.question(self, t("history.menu"), text)
        return answer == QMessageBox.StandardButton.Yes

    def _fill_question(self, history: HistoryContext) -> None:
        self.ask_label.setText(t("history.ask", name=history.owner))
        for button in self.ask_buttons:
            self._ask_layout.removeWidget(button)
            button.deleteLater()
        self.ask_buttons = []
        choices = [(cid, t("history.ask_same", day=_day(day))) for cid, day in history.candidates]
        choices.append((None, t("history.ask_other")))
        for contact_id, text in choices:
            button = QPushButton(_no_mnemonic(text))
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _=False, c=contact_id: self._answer(c))
            self._ask_layout.addWidget(button)
            self.ask_buttons.append(button)

    def _answer(self, contact_id: int | None) -> None:
        self.ask_box.hide()
        self._place()
        self.identity_answered.emit(contact_id)

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
            button.clicked.connect(lambda _=False, text=instruction: self._request_regenerate(text))
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
        self.style_button.setText(t("panel.style"))
        self.messages_label.setText(t("panel.messages"))
        self.instruction.setPlaceholderText(t("panel.instruction_placeholder"))
        self.regenerate_button.setText(t("panel.regenerate"))
        self.private_button.setText(t("panel.mark_private"))

    def _choose_label(self, label: str | None) -> None:
        self.set_label(label, guessed=False)
        self.label_chosen.emit(label)

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
