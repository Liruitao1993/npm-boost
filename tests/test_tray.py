# -*- coding: utf-8 -*-
"""托盘常驻与速率显示回归自检（全离线）。

覆盖：
  1. 速率 / 累计文案格式化
  2. RateMeter：基线、Δ速率、3 点滑动平均、空闲判定、reset
  3. 数据口径：两个代理的累计字节合并方式
  4. 图标：代码绘制可用（exe 里不依赖外部图标文件）
  5. 速率悬浮条：文案 / 状态点 / 提示、默认位置（任务栏上方）、拖动后位置持久化
  6. 托盘：菜单项、状态刷新、勾选偏好持久化与信号

用法：
    "D:\\Program Files\\Python313\\python.exe" tests\\test_tray.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import github_boost  # noqa: E402
import proxy_server  # noqa: E402
import speed_meter  # noqa: E402
import tray  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="npm-accel-tray-"))
# 托盘偏好换到临时 ini：QSettings(org, app) 默认写注册表，而且 setDefaultFormat 对它无效，
# 所以这里直接把 tray.settings 指向隔离实例 —— 测试绝不碰用户真实设置
tray.settings = lambda: QSettings(str(tmp / "tray.ini"), QSettings.Format.IniFormat)

app = QApplication(sys.argv)
failures = []
skips = []


def check(name, cond, detail=""):
    print(("  [OK] " if cond else "  [FAIL] ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def skip(name, reason):
    print(f"  [SKIP] {name}  {reason}")
    skips.append(name)


print("1) 速率与累计文案")
check("0 → 0 B/s", speed_meter.format_rate(0) == "0 B/s")
check("512 B/s", speed_meter.format_rate(512) == "512 B/s", speed_meter.format_rate(512))
check("2048 → 2 KB/s", speed_meter.format_rate(2048) == "2 KB/s", speed_meter.format_rate(2048))
check("1.5 MB/s", speed_meter.format_rate(int(1.5 * 1024 ** 2)) == "1.5 MB/s")
check("2 GB/s", speed_meter.format_rate(2 * 1024 ** 3) == "2.00 GB/s")
check("累计 999 B", speed_meter.format_bytes(999) == "999 B")
check("累计 2 KB", speed_meter.format_bytes(2048) == "2 KB", speed_meter.format_bytes(2048))
check("累计 5.5 MB", speed_meter.format_bytes(int(5.5 * 1024 ** 2)) == "5.5 MB")

print("2) RateMeter 采样与平滑")
state = {"down": 0, "up": 0}
meter = speed_meter.RateMeter(sampler=lambda: (state["down"], state["up"]),
                              window=3, idle_after=5.0)
snap0 = meter.sample(now=0.0)
check("首次采样只建基线，速率为 0", snap0["down"] == 0 and snap0["idle"] is True)

state["down"] = 1024
snap1 = meter.sample(now=1.0)
check("Δ字节 / Δ时间 = 速率", abs(snap1["down"] - 1024) < 1e-6, str(snap1["down"]))
check("速率文案随采样更新", snap1["down_text"] == "1 KB/s", snap1["down_text"])
check("有流量时不算空闲", snap1["idle"] is False and snap1["state_text"] == "传输中")

state["down"] = 1024 + 4096
snap2 = meter.sample(now=2.0)
check("3 点滑动平均生效", abs(snap2["down"] - (1024 + 4096) / 2) < 1e-6, str(snap2["down"]))
check("累计按基线起点算", snap2["session_down"] == 5120, str(snap2["session_down"]))

snap3 = meter.sample(now=20.0)
check("长时间无流量判定为空闲", snap3["idle"] is True and snap3["state_text"] == "空闲")
check("空闲后累计仍保留", snap3["session_down"] == 5120, str(snap3["session_down"]))

state["up"] = 2048
snap_up = meter.sample(now=21.0)
check("上传方向被计入（3 点平均，含前面的 0 样本）",
      abs(snap_up["up"] - 2048 / 3) < 1, str(snap_up["up"]))
state["up"] = 2048 * 2
meter.sample(now=22.0)
state["up"] = 2048 * 3
snap_up3 = meter.sample(now=23.0)
check("持续同速上传时收敛到真实速率", abs(snap_up3["up"] - 2048) < 1e-6, str(snap_up3["up"]))
check("上传文案", snap_up3["up_text"] == "2 KB/s", snap_up3["up_text"])

meter.reset()
snap4 = meter.sample(now=100.0)
check("reset 后重新建基线", snap4["down"] == 0 and snap4["session_down"] == 0)

print("3) 数据口径：两个代理的累计字节合并")
orig_ps, orig_gh = proxy_server.proxy_stats, github_boost.github_stats
try:
    proxy_server.proxy_stats = lambda: {"bytes_down": 100}
    github_boost.github_stats = lambda: {"bytes_down": 7, "bytes_up": 3}
    check("下载 = 缓存代理 + GitHub 隧道；上传只算隧道",
          speed_meter.totals() == (107, 3), str(speed_meter.totals()))
finally:
    proxy_server.proxy_stats = orig_ps
    github_boost.github_stats = orig_gh

print("4) 图标（代码绘制，不依赖资源文件）")
for active in (True, False):
    icon = tray.make_icon(active=active)
    check(f"图标非空（active={active}）",
          (not icon.isNull()) and (not icon.pixmap(32, 32).isNull()))
check("托盘静态图标可用", not tray.make_icon(active=False, transferring=True).isNull())

print("5) 速率悬浮条")
bar = tray.SpeedBar()
snap = {"down_text": "1.2 MB/s", "up_text": "3 KB/s", "idle": False, "state_text": "传输中",
        "session_down": 1234, "session_up": 5}
bar.set_snapshot(snap, "缓存代理运行中")
check("显示上下行速率",
      "1.2 MB/s" in bar.lbl_down.text() and "3 KB/s" in bar.lbl_up.text(),
      f"{bar.lbl_down.text()} / {bar.lbl_up.text()}")
check("传输中状态点为 active", bar.dot.property("state") == "active")
check("提示含累计与操作说明",
      "本次累计" in bar.toolTip() and "双击" in bar.toolTip())
bar.set_snapshot(dict(snap, idle=True, state_text="空闲"), "加速未启动")
check("空闲时状态点回落", bar.dot.property("state") == "idle")

bar.move_to_default()
screen = bar.screen() or app.primaryScreen()
area = screen.availableGeometry()
check("默认位置完整落在工作区内（任务栏上方）",
      area.contains(bar.frameGeometry()), f"({bar.x()},{bar.y()}) {bar.width()}x{bar.height()}")
check("默认位置贴右下角（availableGeometry 已排除任务栏）",
      abs((area.right() - bar.width() - 12) - bar.x()) <= 2
      and abs((area.bottom() - bar.height() - 12) - bar.y()) <= 2,
      f"({bar.x()},{bar.y()})")

tray.settings().remove(tray.SPEED_BAR_POS_KEY)
bar.move(area.left() + 120, area.top() + 140)
bar.save_position()
bar2 = tray.SpeedBar()
bar2.restore_position()
check("拖动后的位置能持久化并恢复",
      (bar2.x(), bar2.y()) == (area.left() + 120, area.top() + 140),
      f"({bar2.x()},{bar2.y()})")
bar2.hide()

print("6) 托盘")
trayctl = tray.TrayController()
if not trayctl.available:
    skip("托盘菜单与状态", "当前系统托盘不可用")
else:
    menu = trayctl.icon.contextMenu()
    texts = [action.text() for action in menu.actions()]
    for item in ("显示主窗口", "速率悬浮条", "关闭窗口时最小化到托盘", "退出"):
        check(f"菜单含「{item}」", item in texts, str(texts))
    check("托盘图标非空", not trayctl.icon.icon().isNull())

    trayctl.update_status(snap, "缓存代理运行中 · GitHub 隧道运行中")
    tooltip = trayctl.icon.toolTip()
    check("托盘提示含速率与运行状态",
          "MB/s" in tooltip and "运行中" in tooltip, tooltip.replace("\n", " | "))
    check("菜单里的状态行同步", "运行中" in trayctl.act_status.text())
    check("菜单里的速率行同步", "1.2 MB/s" in trayctl.act_down.text(), trayctl.act_down.text())

    tray.settings().remove(tray.SPEED_BAR_KEY)
    signals = []
    trayctl.speed_bar_toggled.connect(signals.append)
    trayctl.act_bar.setChecked(True)
    check("勾选悬浮条会发信号", signals == [True], str(signals))
    check("勾选值写回设置",
          tray.settings().value(tray.SPEED_BAR_KEY, False, type=bool) is True)

    trayctl.act_close.setChecked(False)
    check("关闭行为写回设置",
          tray.settings().value(tray.CLOSE_TO_TRAY_KEY, True, type=bool) is False)
    check("close_to_tray() 与勾选一致", trayctl.close_to_tray() is False)
    trayctl.hide()

print()
if skips:
    print(f"跳过 {len(skips)} 项：{skips}")
print("结果:", "全部通过" if not failures else f"失败 {len(failures)} 项 {failures}")
sys.exit(1 if failures else 0)
