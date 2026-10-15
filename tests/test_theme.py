# -*- coding: utf-8 -*-
"""主题自检：两套主题可用、文字对比度达标、跟随系统可解析、组件不再硬编码色值。

用法（退出码 0 才算通过）：
    "D:\\Program Files\\Python313\\python.exe" tests\\test_theme.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import theme  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  [OK] " if cond else "  [FAIL] ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


# 前景 / 背景 / 最低对比度（WCAG：正文 4.5，非文本元素 3.0）
PAIRS = [
    ("text", "window", 4.5),
    ("text", "surface", 4.5),
    ("text", "surface_alt", 4.5),
    ("text", "btn_bg", 4.5),
    ("text", "input_bg", 4.5),
    ("text", "combo_popup_bg", 4.5),
    ("text", "tab_selected_bg", 4.5),
    ("text_muted", "surface", 4.5),
    ("text_muted", "window", 4.5),
    ("text_strong", "surface", 4.5),
    ("text_strong", "tab_selected_bg", 4.5),
    ("log_text", "log_bg", 4.5),
    ("header_text", "header_bg", 4.5),
    ("stat_text", "surface", 4.5),
    ("badge_text", "badge_bg", 4.5),
    ("badge_bad_text", "badge_bad_bg", 4.5),
    ("primary_text", "primary_btn_bg", 4.5),
    ("primary_text", "danger_btn_bg", 4.5),
    ("tooltip_text", "tooltip_bg", 4.5),
    ("primary", "surface", 3.0),          # 边框 / 指示器这类非文本元素
    ("border_strong", "surface", 1.3),    # 边框只需可见
    ("border", "window", 1.05),
    ("text_disabled", "btn_disabled_bg", 2.0),  # 禁用态放宽
]

app = QApplication(sys.argv)

print("1) 两套主题都能渲染")
qss_light = theme.build_qss(theme.LIGHT)
qss_dark = theme.build_qss(theme.DARK)
check("浅色 QSS 生成长度正常", len(qss_light) > 1500, str(len(qss_light)))
check("深色 QSS 生成长度正常", len(qss_dark) > 1500, str(len(qss_dark)))
check("两套 QSS 内容不同", qss_light != qss_dark)
check("模板占位符已全部替换", "$" not in qss_light and "$" not in qss_dark)
check("未知主题回退到默认调色板", theme.palette("nope") == theme.palette(theme.DEFAULT_SCHEME))
light_p, dark_p = theme.palette(theme.LIGHT), theme.palette(theme.DARK)
check("浅色主题底色确实浅", theme.contrast_ratio(light_p["window"], "#000000") > 12,
      f"{theme.contrast_ratio(light_p['window'], '#000000'):.1f}")
check("深色主题底色确实深", theme.contrast_ratio(dark_p["window"], "#ffffff") > 12,
      f"{theme.contrast_ratio(dark_p['window'], '#ffffff'):.1f}")

print("2) 文字 / 元素对比度")
for scheme in (theme.LIGHT, theme.DARK):
    p = theme.palette(scheme)
    for fg, bg, minimum in PAIRS:
        ratio = theme.contrast_ratio(p[fg], p[bg])
        check(f"{scheme}: {fg} on {bg} = {ratio:.2f} (≥{minimum})", ratio >= minimum)

print("3) 跟随系统的解析")
check("resolve_scheme(light)", theme.resolve_scheme(theme.LIGHT) == theme.LIGHT)
check("resolve_scheme(dark)", theme.resolve_scheme(theme.DARK) == theme.DARK)
system_now = theme.resolve_scheme(theme.SYSTEM)
check("resolve_scheme(system) 落在两套之一", system_now in (theme.LIGHT, theme.DARK), system_now)
check("system_scheme() 结果合法", theme.system_scheme() in (theme.LIGHT, theme.DARK), theme.system_scheme())

print("4) 主题管理器：切换 / 持久化 / 跟随系统")
tmp = Path(tempfile.mkdtemp(prefix="npm-accel-theme-"))
settings = QSettings(str(tmp / "settings.ini"), QSettings.Format.IniFormat)
manager = theme.ThemeManager(app=app, settings=settings)
check("默认模式是跟随系统", manager.mode() == theme.SYSTEM)
check("默认主题可用", manager.scheme() in (theme.LIGHT, theme.DARK))
check("跟随系统时轮询在运行（兜底）", manager._poll.isActive())
manager.set_mode(theme.LIGHT)
check("切到浅色生效", manager.scheme() == theme.LIGHT)
check("应用了浅色 QSS", app.styleSheet() == qss_light)
check("浅色已写入设置", str(settings.value("ui/theme")) == theme.LIGHT)
check("固定主题时停用轮询", not manager._poll.isActive())
manager.set_mode(theme.DARK)
check("切到深色生效", manager.scheme() == theme.DARK and app.styleSheet() == qss_dark)
manager.set_mode(theme.SYSTEM)
check("回到跟随系统", manager.mode() == theme.SYSTEM)
manager.set_mode("乱写的模式")
check("两套调色板的键完全一致", set(light_p) == set(dark_p),
      str(sorted(set(light_p) ^ set(dark_p))))
check("不存在未在 QSS 里用到的空色值",
      all(v.startswith("#") and len(v) in (4, 7) for v in list(light_p.values()) + list(dark_p.values())))

check("非法模式回退为跟随系统", manager.mode() == theme.SYSTEM)

settings.setValue("ui/theme", theme.LIGHT)
manager2 = theme.ThemeManager(app=app, settings=settings)
check("重建后读回用户选择", manager2.mode() == theme.LIGHT and manager2.scheme() == theme.LIGHT)

pal = theme.build_palette(theme.DARK)
check("QPalette.Window 与调色板一致",
      pal.color(QPalette.ColorRole.Window).name() == dark_p["window"],
      pal.color(QPalette.ColorRole.Window).name())
check("QPalette.Base 用输入框底色",
      pal.color(QPalette.ColorRole.Base).name() == dark_p["input_bg"])

print("5) 组件里不得再出现硬编码色值")
color_pattern = re.compile(r"#[0-9a-fA-F]{6}\b")
for name in ("main.py", "main_window.py", "workers.py", "npm_manager.py", "proxy_server.py"):
    text = (ROOT / name).read_text(encoding="utf-8")
    hits = color_pattern.findall(text)
    check(f"{name} 无硬编码色值", not hits, str(hits[:3]))

print()
print("结果:", "全部通过" if not failures else f"失败 {len(failures)} 项：{failures[:6]}")
sys.exit(1 if failures else 0)
