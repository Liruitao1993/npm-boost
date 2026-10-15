# -*- coding: utf-8 -*-
"""GUI 冒烟自检（真实窗口）：

1. 用临时 .npmrc / QSettings 隔离，验证「上次异常退出遗留代理源」的开机自愈
2. 主题：跟随系统 / 浅色 / 深色 切换，并校验界面渲染亮度真的随之变化
3. 切换全部标签页、触发一次镜像源测速
4. 关闭窗口，确认后台线程收尾、进程退出码为 0（不闪退）

用法（退出码 0 才算通过）：
    "D:\\Program Files\\Python313\\python.exe" tests\\smoke_gui.py
"""
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import npm_manager  # noqa: E402

# ---- 用临时 .npmrc 隔离，绝不写用户真实配置 ----
tmp = Path(tempfile.mkdtemp(prefix="npm-accel-smoke-"))
npm_manager.NPMRC_PATH = tmp / ".npmrc"
npm_manager.NPMRC_BAK = tmp / ".npmrc.bak"
npm_manager.NPMRC_PATH.write_text(
    "registry=http://127.0.0.1:8046\nprefix=E:/usr/local\n", encoding="utf-8")
npm_manager.NPMRC_BAK.write_text(
    "registry=https://mirrors.cloud.tencent.com/npm/\nprefix=E:/usr/local\n", encoding="utf-8")

from PySide6.QtCore import QSettings, QTimer  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import theme  # noqa: E402
import version  # noqa: E402

failures = []
t0 = time.time()


def check(name, cond, detail=""):
    print(("  [OK] " if cond else "  [FAIL] ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def mean_luminance(widget) -> float:
    """抓窗口渲染结果算平均亮度（0=全黑，1=全白），用来客观验证主题真的换了。"""
    image = QImage(widget.size(), QImage.Format.Format_RGB32)
    image.fill(0)
    widget.render(image)
    total = 0.0
    samples = 0
    for y in range(0, image.height(), 6):
        for x in range(0, image.width(), 6):
            color = image.pixelColor(x, y)
            total += 0.2126 * color.redF() + 0.7152 * color.greenF() + 0.0722 * color.blueF()
            samples += 1
    return total / samples if samples else 0.0


try:
    import main_window
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    theme_manager = theme.ThemeManager(app=app, settings=QSettings(str(tmp / "ui.ini"),
                                                                  QSettings.Format.IniFormat))
    theme_manager.set_mode(theme.DARK)
    w = main_window.MainWindow(theme_manager)
    w.show()
    app.processEvents()
    print(f"[{time.time()-t0:.1f}s] 主窗口已显示，标签页数 = {w.tabs.count()}")
    check("窗口标题含版本号 / 编译日期 / 作者",
          version.__version__ in w.windowTitle()
          and version.AUTHOR in w.windowTitle()
          and ("构建" in w.windowTitle() or "源码运行" in w.windowTitle()),
          w.windowTitle())
    check("界面标题区显示版本信息", w.version_label.text() == version.version_text(),
          w.version_label.text())

    cfg = npm_manager.read_npmrc()
    check("遗留的代理源被自动修复",
          cfg.get("registry") == "https://mirrors.cloud.tencent.com/npm/", str(cfg.get("registry")))
    check("其它键未被破坏", cfg.get("prefix") == "E:/usr/local")

    # ---- 主题 ----
    check("标题栏有主题选择器", w.theme_combo.count() == 3, str(w.theme_combo.count()))
    check("主题选项文案为中文",
          [w.theme_combo.itemText(i) for i in range(3)] == ["跟随系统", "浅色", "深色"])
    check("选择器反映了当前模式", w.theme_combo.currentData() == theme.DARK)

    theme_manager.set_mode(theme.DARK)
    app.processEvents()
    dark_qss = app.styleSheet()
    dark_luma = mean_luminance(w)

    theme_manager.set_mode(theme.LIGHT)
    app.processEvents()
    light_qss = app.styleSheet()
    light_luma = mean_luminance(w)

    check("两套主题的样式表不同", dark_qss != light_qss)
    check("深色主题底子确实深（平均亮度 < 0.35）", dark_luma < 0.35, f"{dark_luma:.3f}")
    check("浅色主题底子确实浅（平均亮度 > 0.60）", light_luma > 0.60, f"{light_luma:.3f}")
    check("切到浅色后界面明显变亮", light_luma - dark_luma > 0.25,
          f"{dark_luma:.3f} -> {light_luma:.3f}")

    # 通过下拉框切回（走的是界面事件，而不是直接调管理器）
    w.theme_combo.setCurrentIndex(w.theme_combo.findData(theme.SYSTEM))
    app.processEvents()
    check("从下拉框切到跟随系统生效", theme_manager.mode() == theme.SYSTEM)
    check("跟随系统时取到合法主题", theme_manager.scheme() in (theme.LIGHT, theme.DARK),
          theme_manager.scheme())
    check("切换后样式表与当前主题一致",
          app.styleSheet() == theme.build_qss(theme_manager.scheme()))

    # ---- 标签页与测速 ----
    for i in range(w.tabs.count()):
        w.tabs.setCurrentIndex(i)
        app.processEvents()
    check("全部标签页均可切换", w.tabs.currentIndex() == w.tabs.count() - 1)
    check("页面文案为中文", w.tabs.tabText(0) == "加速中心" and w.tabs.tabText(4) == "日志")

    w.tabs.setCurrentIndex(0)
    w.run_speedtest()

    def after_speed():
        text = w.speed_result.text()
        print(f"[{time.time()-t0:.1f}s] 测速结果:\n{text}")
        check("测速已返回结果", "测速中" not in text and bool(text.strip()))
        check("测速按钮已恢复可用", w.btn_speed.isEnabled())
        w.close()          # 走 closeEvent：等线程、停代理、还原源
        app.quit()

    QTimer.singleShot(16000, after_speed)
    app.exec()
    print(f"[{time.time()-t0:.1f}s] 事件循环已退出")
    check("环境信息已采集", w.env_labels["npm 版本"].text() != "…", w.env_labels["npm 版本"].text())
    check("退出后无残留后台线程", all(not t.isRunning() for t in w._jobs))
except Exception:
    traceback.print_exc()
    failures.append("异常")

print()
print("结果:", "全部通过" if not failures else f"失败 {len(failures)} 项：{failures}")
sys.exit(1 if failures else 0)
