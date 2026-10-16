# -*- coding: utf-8 -*-
"""加速速率聚合（纯后端，不依赖 PySide6）。

两个代理记账的都是「累计字节」（单调递增），速率就是 Δ字节 / Δ时间；这里再做
3 点滑动平均，避免 1 秒采样下数字乱跳；超过 IDLE_AFTER 秒没有新字节即判定为空闲。

数据口径：
* 下载 = npm 缓存代理写给客户端的内容字节（含缓存命中）+ GitHub 隧道「上游→客户端」
* 上传 = GitHub 隧道「客户端→上游」（如 git push）；npm 的 GET 请求体极小、
  由 requests 内部生成，不单独计入，免得报出一个虚高的上传速度
"""
import time
from collections import deque

import github_boost
import proxy_server

IDLE_AFTER = 5.0     # 超过这么久没有新字节，算空闲
SMOOTH_WINDOW = 3    # 滑动平均的采样点个数


def totals() -> tuple:
    """两个代理的累计字节 (下载, 上传)。"""
    ps = proxy_server.proxy_stats()
    gh = github_boost.github_stats()
    return (int(ps.get("bytes_down", 0)) + int(gh.get("bytes_down", 0)),
            int(gh.get("bytes_up", 0)))


def format_rate(bytes_per_second: float) -> str:
    """字节/秒 → 人类可读文案。"""
    if bytes_per_second < 1:
        return "0 B/s"
    if bytes_per_second < 1024:
        return f"{bytes_per_second:.0f} B/s"
    if bytes_per_second < 1024 ** 2:
        return f"{bytes_per_second / 1024:.0f} KB/s"
    if bytes_per_second < 1024 ** 3:
        return f"{bytes_per_second / 1024 ** 2:.1f} MB/s"
    return f"{bytes_per_second / 1024 ** 3:.2f} GB/s"


def format_bytes(num_bytes: int) -> str:
    """字节数 → 人类可读文案（用于「本次累计」）。"""
    num_bytes = int(num_bytes)
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 ** 2:
        return f"{num_bytes / 1024:.0f} KB"
    if num_bytes < 1024 ** 3:
        return f"{num_bytes / 1024 ** 2:.1f} MB"
    return f"{num_bytes / 1024 ** 3:.2f} GB"


class RateMeter:
    """按固定间隔采样，产出平滑后的速率快照。

    sampler 与 now 都可注入，方便离线测试（不需要真的等时间流逝）。
    """

    def __init__(self, sampler=totals, window: int = SMOOTH_WINDOW,
                 idle_after: float = IDLE_AFTER):
        self._sampler = sampler
        self._window = max(1, window)
        self._idle_after = idle_after
        self._down_samples = deque(maxlen=self._window)
        self._up_samples = deque(maxlen=self._window)
        self._last_totals = None
        self._last_time = None
        self._last_active = None
        self._baseline = None

    def sample(self, now: float = None) -> dict:
        """采一次样并返回快照；第一次调用只建立基线（速率为 0）。"""
        now = time.monotonic() if now is None else now
        down_total, up_total = self._sampler()
        if self._last_totals is None:
            self._last_totals = (down_total, up_total)
            self._last_time = now
            self._baseline = (down_total, up_total)
            return self._snapshot(now)
        elapsed = max(1e-6, now - self._last_time)
        delta_down = max(0, down_total - self._last_totals[0])
        delta_up = max(0, up_total - self._last_totals[1])
        self._down_samples.append(delta_down / elapsed)
        self._up_samples.append(delta_up / elapsed)
        self._last_totals = (down_total, up_total)
        self._last_time = now
        if delta_down or delta_up:
            self._last_active = now
        return self._snapshot(now)

    def snapshot(self, now: float = None) -> dict:
        """不采样，只按当前已有样本给出快照。"""
        return self._snapshot(time.monotonic() if now is None else now)

    def _snapshot(self, now: float) -> dict:
        down = sum(self._down_samples) / len(self._down_samples) if self._down_samples else 0.0
        up = sum(self._up_samples) / len(self._up_samples) if self._up_samples else 0.0
        idle = self._last_active is None or (now - self._last_active) > self._idle_after
        base_down, base_up = self._baseline or (0, 0)
        cur_down, cur_up = self._last_totals or (0, 0)
        return {
            "down": down,
            "up": up,
            "down_text": format_rate(down),
            "up_text": format_rate(up),
            "idle": bool(idle),
            "state_text": "空闲" if idle else "传输中",
            "session_down": max(0, cur_down - base_down),
            "session_up": max(0, cur_up - base_up),
            "total_down": cur_down,
            "total_up": cur_up,
        }

    def reset(self) -> None:
        """重新建立基线（例如清空缓存/重启代理后）。"""
        self._down_samples.clear()
        self._up_samples.clear()
        self._last_totals = None
        self._last_time = None
        self._last_active = None
        self._baseline = None
