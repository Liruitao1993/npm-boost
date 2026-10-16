# -*- coding: utf-8 -*-
"""NPM 加速器 - 入口。"""
import sys
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from main_window import MainWindow
from theme import ThemeManager
import version

SELFTEST_FLAG = "--selftest"


def check_theme() -> tuple:
    """自检用：两套主题都能渲染，并且能识别当前系统主题。"""
    try:
        import theme
        light_qss = theme.build_qss(theme.LIGHT)
        dark_qss = theme.build_qss(theme.DARK)
        if "$" in light_qss or "$" in dark_qss or light_qss == dark_qss:
            return False, "两套主题样式未正确渲染"
        theme.build_palette(theme.LIGHT)
        theme.build_palette(theme.DARK)
        return True, f"系统主题={theme.system_scheme()}，浅色/深色样式与调色板均可用"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:60]}"


def _console_window_count() -> int:
    """当前可见的控制台窗口数量（仅 Windows，用于验证不会弹出黑窗口）。"""
    if sys.platform != "win32":
        return 0
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        found = []

        def _callback(hwnd, _lparam):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value in ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS") \
                    and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
            return True

        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(callback_type(_callback), 0)
        return len(found)
    except Exception:
        return 0


def check_subprocess_quiet() -> tuple:
    """确认调用 npm 时不会弹出控制台黑窗口（windowed exe 下黑窗会闪现在用户眼前）。"""
    if sys.platform != "win32":
        return True, "非 Windows，跳过"
    import threading

    import npm_manager

    baseline = _console_window_count()
    peak = {"value": baseline}
    stop = threading.Event()

    def sample():
        while not stop.is_set():
            peak["value"] = max(peak["value"], _console_window_count())
            time.sleep(0.03)

    watcher = threading.Thread(target=sample, daemon=True)
    watcher.start()
    try:
        code, _ = npm_manager.run_npm(["--version"], timeout=90)
    finally:
        time.sleep(0.25)
        stop.set()
        watcher.join(timeout=3)
    popped = max(0, peak["value"] - baseline)
    return code == 0 and popped == 0, \
        f"npm --version 退出码 {code}，期间新增控制台窗口 {popped} 个（应为 0）"


def run_selftest() -> int:
    """打包后自检：确认 exe 里的 HTTPS、代理缓存与配置能力都可用。

    windowed 打包没有 stdout，所以结果写到 %TEMP%\\npm-accelerator-selftest.txt，
    并用退出码表示结果（0 通过 / 1 有问题）：

        NPM加速器.exe --selftest
    """
    import tempfile
    from pathlib import Path

    import github_boost
    import npm_manager
    import proxy_server

    lines = []
    ok = True

    def emit(text):
        lines.append(text)

    emit(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    emit(f"Python: {sys.version.split()[0]} | 打包运行: {getattr(sys, 'frozen', False)}")
    emit(f"版本: {version.version_text()}")

    ca_ok, ca_path = npm_manager.ca_bundle_status()
    emit(f"CA 证书包: {'可用' if ca_ok else '缺失'} -> {ca_path}")
    ok = ok and ca_ok

    results = npm_manager.measure_all(timeout=8)
    for r in results:
        emit(f"镜像 {r['name']}: {'OK' if r['ok'] else '失败'} | {r['ms']} ms | {r['status']}")
    reachable = [r for r in results if r["ok"]]
    emit(f"可用镜像源: {len(reachable)}/{len(results)}")
    ok = ok and bool(reachable)

    proxy_result = proxy_server.self_test()
    emit(f"代理缓存自检: {'通过' if proxy_result.get('ok') else '失败'} | {proxy_result.get('msg')}")
    ok = ok and bool(proxy_result.get("ok"))

    # GitHub 优选自检：作为可观测项报告。某些网络环境会整体封锁 GitHub，
    # 那种情况下本项目其它功能仍然正常，所以不计入整体成败。
    github_result = github_boost.self_test()
    emit(f"GitHub 优选自检: {'通过' if github_result.get('ok') else '不可用'} | "
         f"{github_result.get('msg')}")
    theme_ok, theme_msg = check_theme()
    emit(f"主题自检: {'通过' if theme_ok else '失败'} | {theme_msg}")
    ok = ok and theme_ok
    quiet_ok, quiet_msg = check_subprocess_quiet()
    emit(f"子进程静默自检: {'通过' if quiet_ok else '失败'} | {quiet_msg}")
    ok = ok and quiet_ok

    emit(f"结论: {'全部通过' if ok else '存在问题'}")
    try:
        path = Path(tempfile.gettempdir()) / "npm-accelerator-selftest.txt"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass
    return 0 if ok else 1


def main():
    if SELFTEST_FLAG in sys.argv[1:]:
        sys.exit(run_selftest())
    app = QApplication(sys.argv)
    app.setApplicationName("NPM 加速器")
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    # 主题：默认跟随系统，用户的选择持久化在 QSettings（切换与跟随由 ThemeManager 负责）
    theme_manager = ThemeManager(app=app)
    win = MainWindow(theme_manager)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
