"""Tray icon and menu."""

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygonF
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from respondoia.i18n import t

_ACTIVE, _PAUSED = "#1f9d6b", "#8a8f98"
NOTICE_MS = 5000


def make_icon(paused: bool) -> QIcon:
    """Drawn in code: a speech bubble, or two bars while paused."""
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(_PAUSED if paused else _ACTIVE))
    painter.drawRoundedRect(QRectF(4, 4, 56, 56), 14, 14)
    painter.setBrush(QColor("white"))
    if paused:
        painter.drawRoundedRect(QRectF(20, 18, 9, 28), 3, 3)
        painter.drawRoundedRect(QRectF(35, 18, 9, 28), 3, 3)
    else:
        painter.drawRoundedRect(QRectF(14, 16, 36, 24), 8, 8)
        painter.drawPolygon(QPolygonF([QPointF(22, 38), QPointF(22, 50), QPointF(34, 38)]))
    painter.end()
    return QIcon(pixmap)


class Tray(QSystemTrayIcon):
    pause_toggled = Signal(bool)
    quit_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._paused = False
        self._menu = QMenu()
        self.pause_action = self._menu.addAction(t("tray.pause"))
        self.pause_action.triggered.connect(self._toggle)
        self._menu.addSeparator()
        self.quit_action = self._menu.addAction(t("tray.quit"))
        self.quit_action.triggered.connect(self.quit_requested)
        self.setContextMenu(self._menu)
        self.set_paused(False)

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self.setIcon(make_icon(paused))
        self.setToolTip(t("tray.tooltip_paused") if paused else t("tray.tooltip"))
        self.pause_action.setText(t("tray.resume") if paused else t("tray.pause"))

    def notify(self, key: str, **values: object) -> None:
        """Show the translated text for key as a balloon."""
        self.showMessage(t("tray.tooltip"), t(key, **values), self.icon(), NOTICE_MS)

    def _toggle(self) -> None:
        self.set_paused(not self._paused)
        self.pause_toggled.emit(self._paused)
