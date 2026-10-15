# -*- coding: utf-8 -*-
"""后台工作线程：把耗时操作（测速 / npm 命令 / 环境采集 / 代理操作）移出 UI 线程。

线程生命周期约定：QThread 对象必须由调用方（主窗口）持有引用，否则线程仍在
运行时被 Python 回收会直接 abort 进程（退出码 0xC0000409）。
"""
import shutil
import subprocess

from PySide6.QtCore import QThread, Signal

import npm_manager
import proxy_server


class SpeedTestThread(QThread):
    """并发测速所有镜像源。"""
    finished_sig = Signal(list)

    def run(self):
        self.finished_sig.emit(npm_manager.measure_all())


class NpmCommandThread(QThread):
    """执行任意 npm 命令并流式回传输出。"""
    output = Signal(str)
    done = Signal(int)  # exit code

    def __init__(self, args, cwd=None, parent=None):
        super().__init__(parent)
        self.args = args
        self.cwd = cwd
        self.proc = None

    def run(self):
        cmd = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
        try:
            self.proc = subprocess.Popen(
                [cmd] + self.args, cwd=self.cwd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                **npm_manager.spawn_kwargs(),
            )
            for line in self.proc.stdout:
                self.output.emit(line.rstrip("\n"))
            code = self.proc.wait()
            self.done.emit(code)
        except Exception as e:
            self.output.emit(f"执行出错：{e}")
            self.done.emit(1)

    def stop(self):
        """终止命令。

        npm.cmd 是批处理壳，真正干活的是它派生的 node 进程；只 kill 壳会留下
        孤儿进程继续占用 node_modules，所以用 taskkill /T 结束整棵进程树。
        """
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        taskkill = shutil.which("taskkill")
        if taskkill:
            try:
                subprocess.run([taskkill, "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=15, shell=False,
                               **npm_manager.spawn_kwargs())
                return
            except Exception:
                pass
        try:
            proc.kill()
        except Exception:
            pass


class EnvInfoThread(QThread):
    """采集 npm 环境信息。"""
    finished_sig = Signal(dict)

    def run(self):
        self.finished_sig.emit(npm_manager.npm_info_summary())


class ProxyActionThread(QThread):
    """启动/停止代理、清空代理缓存等代理操作。"""
    finished_sig = Signal(bool, str)  # ok, message

    def __init__(self, action: str, parent=None):
        super().__init__(parent)
        self.action = action

    def run(self):
        if self.action == "start":
            ok, msg = proxy_server.start_proxy()
        elif self.action == "stop":
            ok, msg = proxy_server.stop_proxy()
        elif self.action == "clear_cache":
            ok, msg = proxy_server.clear_proxy_cache()
        else:
            ok, msg = False, f"未知操作 {self.action}"
        self.finished_sig.emit(ok, msg)
