"""The contact memory window: what the app keeps about one contact, editable."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from respondoia.i18n import t


class MemoryWindow(QWidget):
    saved = Signal(int, str)  # contact id, text

    def __init__(self) -> None:
        super().__init__()
        self.resize(460, 420)
        self._contact_id: int | None = None
        self.hint = QLabel(t("memory.hint"))
        self.hint.setWordWrap(True)
        # Plain text only: the memory was written by the model from other people's messages.
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(t("memory.empty"))
        self.save_button = QPushButton(t("memory.save"))
        self.save_button.clicked.connect(self._save)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.save_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.hint)
        layout.addWidget(self.editor, 1)
        layout.addLayout(buttons)

    def show_memory(self, contact_id: int, owner: str, text: str) -> None:
        self._contact_id = contact_id
        self.setWindowTitle(t("memory.title", name=owner))
        self.editor.setPlainText(text)

    def _save(self) -> None:
        if self._contact_id is not None:
            self.saved.emit(self._contact_id, self.editor.toPlainText().strip())
