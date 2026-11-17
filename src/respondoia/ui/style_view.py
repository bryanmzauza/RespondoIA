"""Screen for learning and reviewing the writing style."""

from collections.abc import Sequence

from PySide6.QtCore import QDate, QLocale, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from respondoia.domain import LABELS
from respondoia.i18n import t
from respondoia.style.profile import StyleProfile
from respondoia.style.service import LanguageView, StyleOverview

_EXPORTS = (".txt", ".zip")
_LINE_BREAK = "\n"


def _pct(share: float) -> int:
    return round(share * 100)


def describe(profile: StyleProfile) -> list[str]:
    """What the app noticed, in the interface language. Model text is shown as written."""
    s, lines = profile.stats, []
    if s.messages:
        lines.append(t("style.stat_length", chars=s.median_length))
        lines.append(t("style.stat_burst", pct=_pct(s.burst_share)))
        lines.append(
            t("style.stat_case", lower=_pct(s.lowercase_start), period=_pct(s.final_period))
        )
        if s.emoji_share:
            lines.append(
                t("style.stat_emoji", pct=_pct(s.emoji_share), emoji=" ".join(s.top_emoji))
            )
        if s.abbreviations:
            lines.append(t("style.stat_abbreviations", items=", ".join(s.abbreviations)))
        if s.laughter:
            lines.append(t("style.stat_laughter", laugh=s.laughter))
    traits = profile.traits
    if traits is not None:
        if traits.formality:
            lines.append(t("style.trait_formality", text=traits.formality))
        for key, items in (
            ("style.trait_greetings", traits.greetings),
            ("style.trait_farewells", traits.farewells),
            ("style.trait_expressions", traits.expressions),
            ("style.trait_avoids", traits.avoids),
            ("style.trait_habits", traits.habits),
        ):
            if items:
                lines.append(t(key, items=", ".join(items)))
        for item in traits.by_label:
            if item.label in LABELS:
                lines.append(t("style.trait_label", label=t("label." + item.label), text=item.note))
    return lines


def _plain_label() -> QLabel:
    label = QLabel()
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


class StyleWindow(QWidget):
    import_requested = Signal(str)  # file path
    author_chosen = Signal(str, object)  # author, label or None
    import_cancelled = Signal()
    refresh_requested = Signal(str)  # language
    manual_saved = Signal(str, str)  # language, text
    example_removed = Signal(str, str)  # language, example
    reset_requested = Signal()
    load_requested = Signal()  # scroll the open WhatsApp chat and learn from it

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(t("style.title"))
        self.setAcceptDrops(True)
        self.resize(520, 640)
        self._views: dict[str, LanguageView] = {}

        self.language_combo = QComboBox()
        self.language_combo.currentIndexChanged.connect(self._show_current)
        self.status_label = _plain_label()
        self.small_label = _plain_label()
        self.small_label.setText(t("style.small"))
        self.updated_label = _plain_label()
        self.noticed_title = QLabel(t("style.noticed"))
        self.noticed_label = _plain_label()
        self.examples_title = QLabel(t("style.examples"))
        self.examples_list = QListWidget()
        self.remove_button = QPushButton(t("style.remove"))
        self.remove_button.clicked.connect(self._remove_example)
        self.notes_title = QLabel(t("style.notes"))
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setPlaceholderText(t("style.notes_hint"))
        self.notes_edit.setMaximumHeight(80)
        self.save_notes_button = QPushButton(t("style.save_notes"))
        self.save_notes_button.clicked.connect(self._save_notes)

        self.drop_label = _plain_label()
        self.drop_label.setText(t("style.drop_hint"))
        self.import_button = QPushButton(t("style.import"))
        self.import_button.clicked.connect(self._pick_file)
        self.refresh_button = QPushButton(t("style.refresh"))
        self.refresh_button.clicked.connect(self._refresh)
        self.reset_button = QPushButton(t("style.reset"))
        self.reset_button.clicked.connect(self._reset)
        self.load_button = QPushButton(t("style.load"))
        self.load_button.clicked.connect(self.load_requested)
        self.load_hint = _plain_label()
        self.load_hint.setText(t("style.load_hint"))
        self.message_label = _plain_label()

        # Shown after a file was read: whose messages to keep.
        self.author_box = QWidget()
        self.author_combo = QComboBox()
        self.chat_label_combo = QComboBox()
        self.chat_label_combo.addItem(t("label.none"), None)
        for label in LABELS:
            self.chat_label_combo.addItem(t("label." + label), label)
        self.import_start_button = QPushButton(t("style.import_start"))
        self.import_start_button.clicked.connect(self._choose_author)
        self.cancel_button = QPushButton(t("style.cancel"))
        self.cancel_button.clicked.connect(self._cancel_import)
        author_layout = QVBoxLayout(self.author_box)
        author_layout.setContentsMargins(0, 0, 0, 0)
        author_layout.addWidget(QLabel(t("style.which_author")))
        author_layout.addWidget(self.author_combo)
        author_layout.addWidget(QLabel(t("style.chat_label")))
        author_layout.addWidget(self.chat_label_combo)
        author_buttons = QHBoxLayout()
        author_buttons.addStretch(1)
        author_buttons.addWidget(self.cancel_button)
        author_buttons.addWidget(self.import_start_button)
        author_layout.addLayout(author_buttons)
        self.author_box.hide()

        language_row = QHBoxLayout()
        language_row.addWidget(QLabel(t("style.language")))
        language_row.addWidget(self.language_combo, 1)
        actions = QHBoxLayout()
        actions.addWidget(self.import_button)
        actions.addWidget(self.load_button)
        actions.addWidget(self.refresh_button)
        actions.addStretch(1)
        actions.addWidget(self.reset_button)

        layout = QVBoxLayout(self)
        layout.addLayout(language_row)
        layout.addWidget(self.status_label)
        layout.addWidget(self.small_label)
        layout.addWidget(self.updated_label)
        layout.addWidget(self.noticed_title)
        layout.addWidget(self.noticed_label)
        layout.addWidget(self.examples_title)
        layout.addWidget(self.examples_list, 1)
        layout.addWidget(self.remove_button)
        layout.addWidget(self.notes_title)
        layout.addWidget(self.notes_edit)
        layout.addWidget(self.save_notes_button)
        layout.addWidget(self.drop_label)
        layout.addLayout(actions)
        layout.addWidget(self.load_hint)
        layout.addWidget(self.author_box)
        layout.addWidget(self.message_label)
        self.show_overview(StyleOverview(()))

    # Public interface

    def show_overview(self, overview: StyleOverview) -> None:
        current = self.language_combo.currentData()
        self._views = {view.language: view for view in overview.languages}
        self.language_combo.blockSignals(True)
        self.language_combo.clear()
        for view in overview.languages:
            self.language_combo.addItem(t("language." + view.language), view.language)
        index = self.language_combo.findData(current)
        self.language_combo.setCurrentIndex(max(index, 0))
        self.language_combo.blockSignals(False)
        self._show_current()

    def ask_author(self, authors: Sequence[tuple[str, int]]) -> None:
        self.author_combo.clear()
        for author, count in authors:
            self.author_combo.addItem(f"{author} ({count})", author)
        self.chat_label_combo.setCurrentIndex(0)
        self.message_label.clear()
        self.author_box.show()

    def show_busy(self, key: str) -> None:
        self.message_label.setText(t(key))

    def show_notice(self, key: str, **values: object) -> None:
        self.message_label.setText(t(key, **values))

    def show_error(self, key: str, **values: object) -> None:
        self.message_label.setText(t(key, **values))

    def drop_files(self, paths: Sequence[str]) -> bool:
        """Start an import with the first exported chat among the paths."""
        for path in paths:
            if path.lower().endswith(_EXPORTS):
                self.import_requested.emit(path)
                return True
        return False

    # Drag and drop

    def dragEnterEvent(self, event) -> None:
        if any(url.toLocalFile().lower().endswith(_EXPORTS) for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        if self.drop_files([url.toLocalFile() for url in event.mimeData().urls()]):
            event.acceptProposedAction()

    # Internals

    def _language(self) -> str | None:
        return self.language_combo.currentData()

    def _show_current(self) -> None:
        view = self._views.get(self._language())
        has_view = view is not None
        for widget in (self.refresh_button, self.remove_button, self.save_notes_button):
            widget.setEnabled(has_view)
        self.examples_list.clear()
        if view is None:
            self.status_label.setText(t("style.empty"))
            for widget in (self.small_label, self.updated_label):
                widget.hide()
            self.noticed_label.clear()
            self.notes_edit.clear()
            return
        profile = view.profile
        key = "style.learning" if view.small else "style.messages"
        self.status_label.setText(t(key, count=view.messages))
        self.small_label.setVisible(view.small)
        noticed = describe(profile) if profile else []
        self.noticed_label.setText(_LINE_BREAK.join(noticed))
        self.updated_label.setVisible(profile is not None)
        if profile is not None:
            day = QDate.fromString(profile.updated_at[:10], "yyyy-MM-dd")
            date = QLocale().toString(day, QLocale.FormatType.ShortFormat)
            self.updated_label.setText(t("style.updated", date=date))
            self.examples_list.addItems(profile.examples)
        self.notes_edit.setPlainText(view.manual)

    def _remove_example(self) -> None:
        item = self.examples_list.currentItem()
        if item is not None and self._language():
            self.example_removed.emit(self._language(), item.text())

    def _save_notes(self) -> None:
        if self._language():
            self.manual_saved.emit(self._language(), self.notes_edit.toPlainText())

    def _refresh(self) -> None:
        if self._language():
            self.refresh_requested.emit(self._language())

    def _reset(self) -> None:
        answer = QMessageBox.question(self, t("style.title"), t("style.reset_confirm"))
        if answer == QMessageBox.StandardButton.Yes:
            self.reset_requested.emit()

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, t("style.import"), "", "*.txt *.zip")
        if path:
            self.drop_files([path])

    def _choose_author(self) -> None:
        author = self.author_combo.currentData()
        if author is None:
            return
        self.author_box.hide()
        self.author_chosen.emit(author, self.chat_label_combo.currentData())

    def _cancel_import(self) -> None:
        self.author_box.hide()
        self.import_cancelled.emit()
