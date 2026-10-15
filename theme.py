# -*- coding: utf-8 -*-
"""主题：浅色 / 深色两套配色，可跟随系统。

本项目约定（改 UI 前必读）：
- **颜色只在本模块定义**。组件代码里禁止再写 `#rrggbb`；需要「强调文字 / 次要文字 /
  统计数字」这类语义时，给控件设动态属性（`role="hint"` / `role="stat"` /
  `cardTitle=True`），样式由本模块的 QSS 属性选择器负责。
- 两套主题共用同一份 QSS 模板，只是换调色板，避免样式写两遍而漏改。
- 「跟随系统」由 `resolve_scheme()` 决定：优先 Qt 的 `styleHints().colorScheme()`，
  Qt 报 Unknown 时回退 Windows 注册表 `AppsUseLightTheme`，都拿不到就用深色。
"""
from string import Template

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

SYSTEM = "system"
LIGHT = "light"
DARK = "dark"
MODES = (SYSTEM, LIGHT, DARK)
MODE_LABELS = {SYSTEM: "跟随系统", LIGHT: "浅色", DARK: "深色"}
DEFAULT_SCHEME = DARK
POLL_INTERVAL_MS = 3000  # 跟随系统时的兜底轮询间隔

_PALETTES = {
    DARK: {
        "window": "#1e1f24",
        "window_deep": "#17181c",
        "surface": "#23252c",
        "surface_alt": "#1a1b20",
        "log_bg": "#16171b",
        "log_text": "#c9ced6",
        "border": "#2c2e35",
        "border_soft": "#2f313a",
        "border_strong": "#3f4450",
        "text": "#e8e8ea",
        "text_muted": "#b0b6c0",
        "text_strong": "#ffffff",
        "text_hover": "#d0d4dc",
        "text_disabled": "#6f7480",
        "tab_selected_bg": "#26282f",
        "btn_bg": "#2e313a",
        "btn_border": "#3a3d48",
        "btn_hover": "#363a45",
        "btn_pressed": "#2a2d35",
        "btn_disabled_bg": "#26272c",
        "primary": "#2f9e6e",
        "primary_text": "#ffffff",
        "primary_btn_bg": "#2f7d5b",
        "primary_btn_border": "#35906a",
        "primary_btn_hover": "#35906a",
        "danger_btn_bg": "#7d2f43",
        "danger_btn_border": "#90354e",
        "danger_btn_hover": "#90354e",
        "stat_text": "#4fd08a",
        "badge_bg": "#266b4f",
        "badge_text": "#9ff0c8",
        "badge_bad_bg": "#6b2636",
        "badge_bad_text": "#ffb3c2",
        "radio_bg": "#23252c",
        "radio_border": "#2f313a",
        "radio_hover_border": "#3d6ea8",
        "radio_checked_bg": "#1d2e28",
        "indicator_bg": "#1e1f24",
        "indicator_border": "#555b66",
        "indicator_checked_dot": "#9ff0c8",
        "input_bg": "#1a1b20",
        "combo_popup_bg": "#26282f",
        "selection_bg": "#2f5d7d",
        "table_grid": "#2a2c33",
        "progress_bg": "#1a1b20",
        "progress_text": "#9ff0c8",
        "scrollbar_handle": "#3a3d48",
        "scrollbar_handle_hover": "#4a4e5a",
        "header_bg": "#23252c",
        "header_text": "#b0b6c0",
        "tooltip_bg": "#2e313a",
        "tooltip_text": "#e8e8ea",
    },
    LIGHT: {
        "window": "#f4f5f7",
        "window_deep": "#ffffff",
        "surface": "#ffffff",
        "surface_alt": "#f7f8fa",
        "log_bg": "#ffffff",
        "log_text": "#37404d",
        "border": "#dfe3e9",
        "border_soft": "#e6e9ef",
        "border_strong": "#ccd2dc",
        "text": "#2a2f38",
        "text_muted": "#5f6a7a",
        "text_strong": "#12161d",
        "text_hover": "#1f242c",
        "text_disabled": "#949cab",
        "tab_selected_bg": "#ffffff",
        "btn_bg": "#ffffff",
        "btn_border": "#ccd2dc",
        "btn_hover": "#eef1f6",
        "btn_pressed": "#e3e7ee",
        "btn_disabled_bg": "#f0f1f4",
        "primary": "#1f8a5c",
        "primary_text": "#ffffff",
        "primary_btn_bg": "#157048",
        "primary_btn_border": "#12603d",
        "primary_btn_hover": "#12603d",
        "danger_btn_bg": "#b32b3f",
        "danger_btn_border": "#9c2437",
        "danger_btn_hover": "#9c2437",
        "stat_text": "#16794a",
        "badge_bg": "#d9f2e4",
        "badge_text": "#0d6b40",
        "badge_bad_bg": "#fde3e7",
        "badge_bad_text": "#a01f36",
        "radio_bg": "#ffffff",
        "radio_border": "#dfe3e9",
        "radio_hover_border": "#7ea9d8",
        "radio_checked_bg": "#eaf7f0",
        "indicator_bg": "#ffffff",
        "indicator_border": "#b6bcc7",
        "indicator_checked_dot": "#ffffff",
        "input_bg": "#ffffff",
        "combo_popup_bg": "#ffffff",
        "selection_bg": "#bcd9f2",
        "table_grid": "#e6e9ef",
        "progress_bg": "#eef1f6",
        "progress_text": "#0d6b40",
        "scrollbar_handle": "#c9ced8",
        "scrollbar_handle_hover": "#b0b7c4",
        "header_bg": "#f0f2f5",
        "header_text": "#5b6472",
        "tooltip_bg": "#ffffff",
        "tooltip_text": "#2a2f38",
    },
}

_QSS = Template("""
QWidget {
    background-color: $window;
    color: $text;
    font-family: "Microsoft YaHei UI";
    font-size: 13px;
}
QMainWindow { background-color: $window_deep; }
QLabel { background: transparent; }
QCheckBox, QRadioButton { background: transparent; }
QCheckBox { color: $text; }

/* 顶部标题与徽章 */
QLabel#appTitle { font-size: 17px; font-weight: 700; color: $text_strong; background: transparent; }
QLabel#appSubtitle { color: $text_muted; font-size: 12px; background: transparent; }
QLabel#statusBadge {
    background-color: $badge_bg; color: $badge_text;
    border-radius: 10px; padding: 3px 12px; font-weight: 600;
}
QLabel#statusBadge[bad="true"] { background-color: $badge_bad_bg; color: $badge_bad_text; }

/* 卡片标题（QLabel[cardTitle]）在下面统一处理 */
/* 文字语义角色（组件用动态属性，不要内联色值） */
QLabel[cardTitle="true"] {
    font-size: 14px; font-weight: 700; color: $text_strong;
    border: none; background: transparent;
}
QLabel[role="hint"] { color: $text_muted; font-size: 12px; border: none; background: transparent; }
QLabel[role="value"] { color: $text_strong; font-weight: 600; border: none; background: transparent; }
QLabel[role="stat"] { color: $stat_text; font-weight: 600; border: none; background: transparent; }

/* 标签页 */
QTabWidget::pane {
    border: 1px solid $border; border-radius: 6px;
    background: $window; top: -1px;
}
QTabBar::tab {
    background: transparent; color: $text_muted;
    padding: 9px 20px; margin-right: 2px;
    border-top-left-radius: 6px; border-top-right-radius: 6px;
}
QTabBar::tab:selected { background: $tab_selected_bg; color: $text_strong; font-weight: 600; }
QTabBar::tab:hover { color: $text_hover; }

/* 卡片容器 */
QFrame.card {
    background: $surface; border: 1px solid $border_soft; border-radius: 10px;
}

/* 按钮 */
QPushButton {
    background: $btn_bg; color: $text;
    border: 1px solid $btn_border; border-radius: 7px;
    padding: 7px 16px; font-weight: 500;
}
QPushButton:hover { background: $btn_hover; }
QPushButton:pressed { background: $btn_pressed; }
QPushButton:disabled { color: $text_disabled; background: $btn_disabled_bg; }
QPushButton#primaryBtn {
    background: $primary_btn_bg; border-color: $primary_btn_border;
    color: $primary_text; font-weight: 600;
}
QPushButton#primaryBtn:hover { background: $primary_btn_hover; }
QPushButton#primaryBtn:disabled {
    color: $text_disabled; background: $btn_disabled_bg; border-color: $btn_border;
}
QPushButton#dangerBtn { background: $danger_btn_bg; border-color: $danger_btn_border; color: $primary_text; }
QPushButton#dangerBtn:hover { background: $danger_btn_hover; }
QPushButton#dangerBtn:disabled {
    color: $text_disabled; background: $btn_disabled_bg; border-color: $btn_border;
}

/* 单选按钮（镜像源卡片） */
QRadioButton {
    background: $radio_bg; border: 1px solid $radio_border;
    border-radius: 8px; padding: 10px 12px;
}
QRadioButton:hover { border-color: $radio_hover_border; }
QRadioButton:checked { border-color: $primary; background: $radio_checked_bg; }
QRadioButton::indicator {
    width: 14px; height: 14px; border-radius: 7px;
    border: 2px solid $indicator_border; background: $indicator_bg;
}
QRadioButton::indicator:checked { border: 4px solid $primary; background: $indicator_checked_dot; }

/* 复选框 */
QCheckBox { spacing: 8px; padding: 4px 0; }
QCheckBox::indicator {
    width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid $btn_border; background: $indicator_bg;
}
QCheckBox::indicator:checked { background: $primary; border-color: $primary; }
QCheckBox::indicator:disabled { background: $btn_disabled_bg; }

/* 输入框与下拉框 */
QLineEdit, QSpinBox, QComboBox {
    background: $input_bg; border: 1px solid $border_strong;
    border-radius: 6px; padding: 6px 10px; color: $text;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: $primary; }
QLineEdit:disabled, QComboBox:disabled { color: $text_disabled; background: $btn_disabled_bg; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background: $combo_popup_bg; color: $text;
    border: 1px solid $border_strong;
    selection-background-color: $primary_btn_bg; selection-color: $primary_text;
}

/* 表格与日志 */
QTableWidget {
    background: $surface_alt; border: 1px solid $border;
    border-radius: 8px; gridline-color: $table_grid;
    selection-background-color: $selection_bg;
}
QHeaderView::section {
    background: $header_bg; color: $header_text; border: none;
    padding: 7px; font-weight: 600;
}
QPlainTextEdit, QTextEdit {
    background: $log_bg; border: 1px solid $border;
    border-radius: 8px; color: $log_text;
    font-family: "Consolas"; font-size: 12px;
    selection-background-color: $selection_bg;
}

/* 进度条 */
QProgressBar {
    background: $progress_bg; border: 1px solid $border;
    border-radius: 6px; text-align: center; color: $progress_text; height: 18px;
}
QProgressBar::chunk { background: $primary; border-radius: 5px; }

/* 滚动条 */
QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: $scrollbar_handle; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: $scrollbar_handle_hover; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 10px; }
QScrollBar::handle:horizontal { background: $scrollbar_handle; border-radius: 5px; min-width: 30px; }
QScrollBar::handle:horizontal:hover { background: $scrollbar_handle_hover; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }

/* 状态栏与气泡提示 */
QStatusBar { background: $window_deep; color: $text_muted; border-top: 1px solid $border; }
QToolTip {
    background: $tooltip_bg; color: $tooltip_text;
    border: 1px solid $border_strong; padding: 4px 6px;
}
""")


def palette(scheme: str) -> dict:
    """取某个主题的调色板（未知主题回退到默认）。"""
    return _PALETTES.get(scheme, _PALETTES[DEFAULT_SCHEME])


def build_qss(scheme: str) -> str:
    """渲染某个主题的样式表。"""
    return _QSS.substitute(palette(scheme))


def build_palette(scheme: str) -> QPalette:
    """构造配套的 QPalette。

    只靠 QSS 管不到原生绘制部分（下拉箭头、菜单、对话框、禁用态），
    这里让它们也跟着主题走。
    """
    p = palette(scheme)
    pal = QPalette()
    role, group = QPalette.ColorRole, QPalette.ColorGroup
    pal.setColor(role.Window, QColor(p["window"]))
    pal.setColor(role.WindowText, QColor(p["text"]))
    pal.setColor(role.Base, QColor(p["input_bg"]))
    pal.setColor(role.AlternateBase, QColor(p["surface_alt"]))
    pal.setColor(role.Text, QColor(p["text"]))
    pal.setColor(role.Button, QColor(p["btn_bg"]))
    pal.setColor(role.ButtonText, QColor(p["text"]))
    pal.setColor(role.BrightText, QColor(p["badge_bad_text"]))
    pal.setColor(role.Link, QColor(p["primary"]))
    pal.setColor(role.Highlight, QColor(p["selection_bg"]))
    pal.setColor(role.HighlightedText, QColor(p["text_strong"]))
    pal.setColor(role.ToolTipBase, QColor(p["tooltip_bg"]))
    pal.setColor(role.ToolTipText, QColor(p["tooltip_text"]))
    pal.setColor(role.PlaceholderText, QColor(p["text_muted"]))
    for role_name in ("Text", "WindowText", "ButtonText"):
        pal.setColor(group.Disabled, getattr(role, role_name), QColor(p["text_disabled"]))
    pal.setColor(group.Disabled, role.Base, QColor(p["btn_disabled_bg"]))
    return pal


# ==================== 系统主题探测 ====================

def _registry_scheme():
    """Windows 上从注册表读系统主题（Qt 报 Unknown 时的兜底）。"""
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return LIGHT if winreg.QueryValueEx(key, "AppsUseLightTheme")[0] else DARK
    except Exception:
        return None


def system_scheme() -> str:
    """当前系统用的是深色还是浅色。"""
    try:
        app = QApplication.instance()
        if app is not None:
            scheme = app.styleHints().colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return DARK
            if scheme == Qt.ColorScheme.Light:
                return LIGHT
    except Exception:
        pass
    return _registry_scheme() or DEFAULT_SCHEME


def resolve_scheme(mode: str) -> str:
    """把「模式」解析成实际生效的主题。"""
    if mode in (LIGHT, DARK):
        return mode
    return system_scheme()


# ==================== 对比度（供自检使用） ====================

def _luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    channels = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [(c / 12.92) if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG 对比度（1~21）。"""
    a, b = _luminance(foreground), _luminance(background)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


# ==================== 主题管理器 ====================

class ThemeManager(QObject):
    """主题模式（跟随系统 / 浅色 / 深色）的管理与持久化。

    - `changed(scheme)`：实际生效的主题变化时发出（"light" / "dark"）。
    - 跟随系统时既监听 Qt 的 colorSchemeChanged，也做低频轮询兜底
      （某些环境下系统切换主题不会通知 Qt）。
    """

    changed = Signal(str)

    def __init__(self, app=None, settings=None, parent=None):
        super().__init__(parent)
        self._app = app or QApplication.instance()
        self._settings = settings if settings is not None else QSettings("npm-accelerator", "NPM加速器")
        self._mode = SYSTEM
        self._scheme = DEFAULT_SCHEME
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_INTERVAL_MS)
        self._poll.timeout.connect(self._sync_system)
        self._apply_mode(self._load_mode(), save=False)
        try:
            self._app.styleHints().colorSchemeChanged.connect(self._on_system_signal)
        except Exception:
            pass

    # ---------- 查询 ----------
    def mode(self) -> str:
        return self._mode

    def scheme(self) -> str:
        return self._scheme

    # ---------- 设置 ----------
    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            mode = SYSTEM
        self._apply_mode(mode, save=True)

    def apply_now(self) -> None:
        """按当前模式重新应用一次（例如窗口重建后）。"""
        self._apply(self._resolve())

    # ---------- 内部 ----------
    def _resolve(self) -> str:
        return resolve_scheme(self._mode)

    def _apply_mode(self, mode: str, save: bool) -> None:
        self._mode = mode
        if save:
            self._save_mode(mode)
        self._apply(self._resolve())
        if mode == SYSTEM:
            self._poll.start()
        else:
            self._poll.stop()

    def _apply(self, scheme: str) -> None:
        self._scheme = scheme
        if self._app is not None:
            self._app.setPalette(build_palette(scheme))
            self._app.setStyleSheet(build_qss(scheme))
        self.changed.emit(scheme)

    def _sync_system(self) -> None:
        if self._mode != SYSTEM:
            return
        scheme = self._resolve()
        if scheme != self._scheme:
            self._apply(scheme)

    def _on_system_signal(self, *_args) -> None:
        self._sync_system()

    def _load_mode(self) -> str:
        try:
            value = str(self._settings.value("ui/theme", SYSTEM))
        except Exception:
            value = SYSTEM
        return value if value in MODES else SYSTEM

    def _save_mode(self, mode: str) -> None:
        try:
            self._settings.setValue("ui/theme", mode)
        except Exception:
            pass


# 兼容旧引用
DARK_QSS = build_qss(DARK)
LIGHT_QSS = build_qss(LIGHT)
