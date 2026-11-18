"""Settings window and hotkey recorder."""

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from respondoia.config import Settings
from respondoia.hotkey import display_hotkey, text_from_keys
from respondoia.i18n import instruction_label, t

_ROLE = Qt.ItemDataRole.UserRole
_MODIFIER_KEYS = {
    Qt.Key.Key_Control,
    Qt.Key.Key_Shift,
    Qt.Key.Key_Alt,
    Qt.Key.Key_AltGr,
    Qt.Key.Key_Meta,
}
_POSITIONS = (
    ("bottom-right", "settings.position_bottom_right"),
    ("bottom-left", "settings.position_bottom_left"),
    ("top-right", "settings.position_top_right"),
    ("top-left", "settings.position_top_left"),
)


class HotkeyEdit(QLineEdit):
    """Shows the hotkey and records a new one from the next key press."""

    hotkey_changed = Signal(str)  # hotkey text, e.g. "ctrl+alt+r"
    rejected = Signal()  # a key without a modifier, or one the app does not accept

    def __init__(self, value: str) -> None:
        super().__init__()
        self.value = value
        self.setReadOnly(True)
        self.setText(display_hotkey(value))
        self.setPlaceholderText(t("settings.hotkey_hint"))

    def keyPressEvent(self, event) -> None:
        if event.key() in _MODIFIER_KEYS:
            return  # wait for the rest of the combination
        text = text_from_keys(event.modifiers(), event.key())
        if text is None:
            self.rejected.emit()
            return
        self.value = text
        self.setText(display_hotkey(text))
        self.hotkey_changed.emit(text)


class SettingsWindow(QWidget):
    saved = Signal(object)  # Settings
    api_key_entered = Signal(str)
    knowledge_requested = Signal()
    delete_all_requested = Signal()  # every stored message and memory

    def __init__(self, settings: Settings, has_api_key: bool) -> None:
        super().__init__()
        self._settings = settings
        self.delete_history_on_save = False  # the user agreed to delete it when turning off
        self.setWindowTitle(t("settings.title"))

        self.hotkey_edit = HotkeyEdit(settings.hotkey)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.hotkey_edit.rejected.connect(lambda: self.show_error("settings.hotkey_no_modifier"))
        self.hotkey_edit.hotkey_changed.connect(lambda _: self.error_label.clear())

        self.count_box = self._combo([(str(n), n) for n in (2, 3)], settings.suggestion_count)
        self.insert_box = self._combo(
            [(t("settings.insert_paste"), "paste"), (t("settings.insert_copy"), "copy")],
            settings.insert_mode,
        )
        self.position_box = self._combo(
            [(t(key), value) for value, key in _POSITIONS], settings.panel_position
        )
        self.language_box = self._combo(
            [
                (t("settings.language_auto"), "auto"),
                (t("settings.language_pt"), "pt-BR"),
                (t("settings.language_en"), "en"),
            ],
            settings.language,
        )
        self.input_box = self._combo(
            [
                (t("settings.input_text"), "text"),
                (t("settings.input_screenshot"), "screenshot"),
            ],
            settings.input,
        )
        self.mask_check = QCheckBox(t("settings.mask"))
        self.mask_check.setChecked(settings.mask_sensitive_data)
        self.pictures_check = QCheckBox(t("settings.send_pictures"))
        self.pictures_check.setChecked(settings.send_pictures)
        self.pictures_hint = QLabel(t("settings.send_pictures_hint"))
        self.pictures_hint.setWordWrap(True)
        self.model_box = self._combo(
            [
                (t("settings.model_haiku"), "claude-haiku-5-5"),
                (t("settings.model_sonnet"), "claude-sonnet-5-5"),
            ],
            settings.model,
        )
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText(t("settings.api_key_hint"))
        self.api_key_state = QLabel(
            t("settings.api_key_set") if has_api_key else t("settings.api_key_missing")
        )
        self.autostart_check = QCheckBox(t("settings.autostart"))
        self.autostart_check.setChecked(settings.start_with_windows)
        self.knowledge_button = QPushButton(t("settings.knowledge"))
        self.knowledge_button.clicked.connect(self.knowledge_requested)
        self.knowledge_hint = QLabel(t("settings.knowledge_hint"))
        self.knowledge_hint.setWordWrap(True)
        self.history_check = QCheckBox(t("settings.history_enabled"))
        self.history_check.setChecked(settings.history_enabled)
        self.history_hint = QLabel(t("settings.history_hint"))
        self.history_hint.setWordWrap(True)
        self.retention_box = QSpinBox()
        self.retention_box.setRange(1, 120)
        self.retention_box.setValue(settings.retention_months)
        self.history_delete_button = QPushButton(t("settings.history_delete_all"))
        self.history_delete_button.clicked.connect(self._delete_all)

        self.instructions_list = QListWidget()
        for entry in settings.quick_instructions:
            self._add_instruction(entry)
        self.instructions_list.itemChanged.connect(self._instruction_edited)
        self.instruction_edit = QLineEdit()
        self.instruction_edit.setPlaceholderText(t("settings.instruction_hint"))
        self.instruction_add = QPushButton(t("settings.add"))
        self.instruction_add.clicked.connect(self._new_instruction)
        self.instruction_remove = QPushButton(t("settings.remove"))
        self.instruction_remove.clicked.connect(
            lambda: self._remove_current(self.instructions_list)
        )

        self.private_list = QListWidget()
        for name in settings.private_contacts:
            item = QListWidgetItem(name)
            item.setData(_ROLE, name)
            self.private_list.addItem(item)
        self.private_remove = QPushButton(t("settings.remove"))
        self.private_remove.clicked.connect(lambda: self._remove_current(self.private_list))

        self.save_button = QPushButton(t("settings.save"))
        self.save_button.clicked.connect(self._save)
        self.cancel_button = QPushButton(t("settings.cancel"))
        self.cancel_button.clicked.connect(self.close)

        form = QFormLayout()
        form.addRow(t("settings.hotkey"), self.hotkey_edit)
        form.addRow(t("settings.count"), self.count_box)
        form.addRow(t("settings.insert_mode"), self.insert_box)
        form.addRow(t("settings.position"), self.position_box)
        form.addRow(t("settings.language"), self.language_box)
        form.addRow(t("settings.input"), self.input_box)
        form.addRow(self.mask_check)
        form.addRow(self.pictures_check)
        form.addRow(self.pictures_hint)
        form.addRow(t("settings.model"), self.model_box)
        key_row = QHBoxLayout()
        key_row.addWidget(self.api_key_edit, 1)
        key_row.addWidget(self.api_key_state)
        form.addRow(t("settings.api_key"), key_row)
        form.addRow(self.autostart_check)
        form.addRow(self.knowledge_button, self.knowledge_hint)
        form.addRow(QLabel(t("settings.history")))
        form.addRow(self.history_check)
        form.addRow(self.history_hint)
        form.addRow(t("settings.retention"), self.retention_box)
        form.addRow(self.history_delete_button)

        new_instruction = QHBoxLayout()
        new_instruction.addWidget(self.instruction_edit, 1)
        new_instruction.addWidget(self.instruction_add)
        new_instruction.addWidget(self.instruction_remove)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.save_button)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel(t("settings.instructions")))
        layout.addWidget(self.instructions_list)
        layout.addLayout(new_instruction)
        layout.addWidget(QLabel(t("settings.private")))
        layout.addWidget(self.private_list)
        layout.addWidget(self.private_remove)
        layout.addWidget(self.error_label)
        layout.addLayout(buttons)

    def show_error(self, key: str, **values: object) -> None:
        self.error_label.setText(t(key, **values))

    def _combo(self, items: list[tuple[str, object]], current: object) -> QComboBox:
        box = QComboBox()
        for label, value in items:
            box.addItem(label, value)
        if box.findData(current) < 0:  # a value set by hand in config.toml
            box.addItem(str(current), current)
        box.setCurrentIndex(box.findData(current))
        return box

    def _add_instruction(self, entry: str) -> None:
        item = QListWidgetItem(instruction_label(entry))
        item.setData(_ROLE, entry)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        self.instructions_list.addItem(item)

    def _instruction_edited(self, item: QListWidgetItem) -> None:
        text = item.text().strip()
        if not text:
            item.setText(instruction_label(item.data(_ROLE)))
        elif text != instruction_label(item.data(_ROLE)):
            item.setData(_ROLE, text)  # a renamed built-in becomes the user's own text

    def _new_instruction(self) -> None:
        text = self.instruction_edit.text().strip()
        if text:
            self._add_instruction(text)
            self.instruction_edit.clear()

    def _remove_current(self, listing: QListWidget) -> None:
        row = listing.currentRow()
        if row >= 0:
            listing.takeItem(row)

    def _ask(self, key: str) -> bool:
        answer = QMessageBox.question(self, t("settings.history"), t(key))
        return answer == QMessageBox.StandardButton.Yes

    def _delete_all(self) -> None:
        if self._ask("settings.history_delete_all_confirm"):
            self.delete_all_requested.emit()

    def _save(self) -> None:
        turned_off = self._settings.history_enabled and not self.history_check.isChecked()
        # The controller deletes only if the save works: a failed save keeps history on.
        self.delete_history_on_save = turned_off and self._ask("settings.history_off_confirm")
        key = self.api_key_edit.text().strip()
        if key:
            self.api_key_entered.emit(key)
            self.api_key_edit.clear()

        def values(listing: QListWidget) -> tuple[str, ...]:
            return tuple(listing.item(i).data(_ROLE) for i in range(listing.count()))

        self.saved.emit(
            replace(
                self._settings,
                hotkey=self.hotkey_edit.value,
                suggestion_count=self.count_box.currentData(),
                insert_mode=self.insert_box.currentData(),
                panel_position=self.position_box.currentData(),
                language=self.language_box.currentData(),
                input=self.input_box.currentData(),
                mask_sensitive_data=self.mask_check.isChecked(),
                send_pictures=self.pictures_check.isChecked(),
                model=self.model_box.currentData(),
                start_with_windows=self.autostart_check.isChecked(),
                quick_instructions=values(self.instructions_list),
                private_contacts=values(self.private_list),
                history_enabled=self.history_check.isChecked(),
                retention_months=self.retention_box.value(),
            )
        )
