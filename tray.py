# -*- coding: utf-8 -*-
"""托盘常驻与速率悬浮条（UI 层）。

* `make_icon()`：用代码画图标（圆角方块 + 上下箭头），颜色取自 theme 的调色板，
  这样 exe 里不依赖任何图标文件；托盘、任务栏按钮、窗口图标共用同一个绘制函数。
* `SpeedBar`：无边框 + 置顶 + 可拖动 + 记住位置的速率条，默认停在工作区右下角
  （即任务栏正上方），显示实时 ↓/↑ 速率。
* `TrayController`：托盘图标与菜单，通过信号把「显示主窗口 / 退出」等意图交给主窗口处理。
"""
from PySide6.QtCore import QObject, QPoint, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QMenu, QSystemTrayIcon,
                               QWidget)

import speed_meter
import theme

SETTINGS_ORG = "npm-accelerator"
SETTINGS_APP = "NPM加速器"
CLOSE_TO_TRAY_KEY = "ui/close_to_tray"
SPEED_BAR_KEY = "ui/speed_bar"
SPEED_BAR_POS_KEY = "ui/speed_bar_pos"
TRAY_HINT_KEY = "ui/tray_hint_shown"


def settings() -> QSettings:
    """与主题共用同一份设置（换主题、托盘偏好都在一起）。"""
    return QSettings(SETTINGS_ORG, SETTINGS_APP)


def make_icon(active: bool = True, transferring: bool = False, size: int = 64) -> QIcon:
    """画应用图标：圆角方块 + 上下箭头。颜色取自调色板，不硬编码色值。

    active=False 用灰色（表示两个代理都没在跑），transferring=True 时用更亮的强调色。
    """
    pal = theme.palette(theme.LIGHT)
    base = pal["primary"] if active else pal["text_muted"]
    ink = pal["primary_text"]
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(base))
        radius = size * 0.24
        painter.drawRoundedRect(1, 1, size - 2, size - 2, radius, radius)

        pen = QPen(QColor(ink))
        pen.setWidthF(max(1.5, size * 0.075))
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        # 上行箭头（左）与下行箭头（右）：一条竖线 + 一个箭头帽
        for cx, up in ((size * 0.36, True), (size * 0.64, False)):
            top, bottom = size * 0.28, size * 0.72
            painter.drawLine(QPoint(int(cx), int(top)), QPoint(int(cx), int(bottom)))
            head, wing = (top, size * 0.115) if up else (bottom, size * 0.115)
            painter.drawLine(QPoint(int(cx), int(head)),
                             QPoint(int(cx - wing), int(head + wing if up else head - wing)))
            painter.drawLine(QPoint(int(cx), int(head)),
                             QPoint(int(cx + wing), int(head + wing if up else head - wing)))
    finally:
        painter.end()
    return QIcon(pixmap)


class SpeedBar(QWidget):
    """无边框置顶的速率条；可拖动、记住位置，默认停在工作区右下角。"""

    show_main_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("speedBar")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setWindowTitle("加速速率")
        self.setWindowIcon(make_icon())

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 7, 14, 7)
        row.setSpacing(10)
        self.dot = QLabel("●")
        self.dot.setObjectName("speedDot")
        self.dot.setProperty("state", "idle")
        self.lbl_down = QLabel("↓ 0 B/s")
        self.lbl_down.setObjectName("speedDown")
        self.lbl_up = QLabel("↑ 0 B/s")
        self.lbl_up.setObjectName("speedUp")
        for widget in (self.dot, self.lbl_down, self.lbl_up):
            row.addWidget(widget)

        self._drag_offset = None
        self._dragged = False
        self._status_text = "加速未启动"

    # ---------- 数据刷新 ----------
    def set_snapshot(self, snap: dict, status_text: str) -> None:
        self._status_text = status_text
        self.lbl_down.setText(f"↓ {snap['down_text']}")
        self.lbl_up.setText(f"↑ {snap['up_text']}")
        state = "idle" if snap["idle"] else "active"
        if self.dot.property("state") != state:
            self.dot.setProperty("state", state)
            self.dot.style().unpolish(self.dot)
            self.dot.style().polish(self.dot)
        self.setToolTip(
            f"{status_text}\n"
            f"当前 ↓ {snap['down_text']} · ↑ {snap['up_text']}（{snap['state_text']}）\n"
            f"本次累计 ↓ {speed_meter.format_bytes(snap['session_down'])}"
            f" · ↑ {speed_meter.format_bytes(snap['session_up'])}\n"
            "拖动可移动 · 双击打开主窗口 · 右键更多"
        )

    # ---------- 位置 ----------
    def move_to_default(self) -> None:
        """停到工作区右下角（availableGeometry 已排除任务栏，所以就在任务栏上方）。"""
        self.adjustSize()          # 未显示过时尺寸还是默认值，先按布局定好
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        margin = 12
        x = area.right() - self.width() - margin
        y = area.bottom() - self.height() - margin
        self.move(max(area.left(), x), max(area.top(), y))

    def restore_position(self) -> None:
        """恢复用户上次拖动的位置；位置已不可见（换显示器等）就回到默认位置。"""
        self.adjustSize()          # 先按布局定好尺寸，否则首次定位会偏
        saved = settings().value(SPEED_BAR_POS_KEY)
        if isinstance(saved, QPoint) and self._pos_visible(saved):
            self.move(saved)
        else:
            self.move_to_default()

    @staticmethod
    def _pos_visible(pos: QPoint) -> bool:
        for screen in QApplication.screens():
            if screen.availableGeometry().contains(pos):
                return True
        return False

    def save_position(self) -> None:
        settings().setValue(SPEED_BAR_POS_KEY, self.pos())

    # ---------- 交互 ----------
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._dragged = False
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and (event.buttons() & Qt.LeftButton):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            self._dragged = True
            event.accept()

    def mouseReleaseEvent(self, event):
        if self._drag_offset is not None:
            if self._dragged:
                self.save_position()
            self._drag_offset = None
            event.accept()

    def mouseDoubleClickEvent(self, event):
        self.show_main_requested.emit()
        event.accept()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction("回到默认位置（右下角）", self._reset_position)
        menu.addAction("隐藏悬浮条", self.hide)
        menu.addAction("打开主窗口", self.show_main_requested.emit)
        menu.exec(event.globalPos())

    def _reset_position(self) -> None:
        self.move_to_default()
        self.save_position()


class TrayController(QObject):
    """托盘图标 + 右键菜单；只发信号，具体动作由主窗口决定。"""

    show_requested = Signal()
    quit_requested = Signal()
    speed_bar_toggled = Signal(bool)
    close_to_tray_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        self.icon = None
        if not self.available:
            return
        store = settings()
        self.icon = QSystemTrayIcon(make_icon(active=False), self)

        menu = QMenu()
        act_show = menu.addAction("显示主窗口")
        act_show.triggered.connect(self.show_requested.emit)
        menu.addSeparator()
        self.act_bar = menu.addAction("速率悬浮条")
        self.act_bar.setCheckable(True)
        self.act_bar.setChecked(bool(store.value(SPEED_BAR_KEY, False, type=bool)))
        self.act_bar.toggled.connect(self._on_bar_toggled)
        self.act_close = menu.addAction("关闭窗口时最小化到托盘")
        self.act_close.setCheckable(True)
        self.act_close.setChecked(bool(store.value(CLOSE_TO_TRAY_KEY, True, type=bool)))
        self.act_close.toggled.connect(self._on_close_toggled)
        menu.addSeparator()
        self.act_status = menu.addAction("加速未启动")
        self.act_status.setEnabled(False)
        self.act_down = menu.addAction("↓ 0 B/s")
        self.act_down.setEnabled(False)
        menu.addSeparator()
        act_quit = menu.addAction("退出")
        act_quit.triggered.connect(self.quit_requested.emit)

        self.icon.setContextMenu(menu)
        self.icon.activated.connect(self._on_activated)

    # ---------- 偏好 ----------
    def close_to_tray(self) -> bool:
        if self.icon is None:
            return False
        return self.act_close.isChecked()

    def speed_bar_visible(self) -> bool:
        if self.icon is None:
            return False
        return self.act_bar.isChecked()

    def _on_bar_toggled(self, on: bool) -> None:
        settings().setValue(SPEED_BAR_KEY, bool(on))
        self.speed_bar_toggled.emit(bool(on))

    def _on_close_toggled(self, on: bool) -> None:
        settings().setValue(CLOSE_TO_TRAY_KEY, bool(on))
        self.close_to_tray_toggled.emit(bool(on))

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_requested.emit()

    # ---------- 展示 ----------
    def update_status(self, snap: dict, status_text: str) -> None:
        if self.icon is None:
            return
        active = "运行" in status_text
        self.icon.setIcon(make_icon(active=active, transferring=not snap["idle"]))
        self.icon.setToolTip(
            f"NPM 加速器 {version_text()}\n"
            f"↓ {snap['down_text']}  ↑ {snap['up_text']}\n"
            f"{status_text}"
        )
        self.act_status.setText(status_text)
        self.act_down.setText(f"↓ {snap['down_text']}  ↑ {snap['up_text']}")

    def notify(self, title: str, text: str) -> None:
        if self.icon is not None:
            self.icon.showMessage(title, text, QSystemTrayIcon.Information, 4000)

    def hide(self) -> None:
        if self.icon is not None:
            self.icon.hide()


def version_text() -> str:
    import version
    return version.version_text()


def tray_hint_shown() -> bool:
    return bool(settings().value(TRAY_HINT_KEY, False, type=bool))


def mark_tray_hint_shown() -> None:
    settings().setValue(TRAY_HINT_KEY, True)
