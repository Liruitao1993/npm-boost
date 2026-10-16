# -*- coding: utf-8 -*-
"""GitHub 加速真实网络端到端自检（需要联网）。

验证的不是"函数能跑"，而是**真的把 GitHub 流量走了优选 IP**：

1. 启动本地加速代理，等优选表就绪；
2. curl 经代理访问 api.github.com / raw.githubusercontent.com，期望 200；
3. git 经代理 ls-remote 一个真实仓库，期望拿到 commit SHA；
4. 关键断言：统计里 fallbacks=0 且 last_connect 是优选 IP —— 说明全程没有回退到系统
   解析（本机 hosts 把 github.com 指向 127.0.0.1，走了系统解析就说明优选没生效）；
5. 停止代理后端口释放。

用法（退出码 0 才算通过）：
    "D:\\Program Files\\Python313\\python.exe" tests\\verify_github_boost.py
"""
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import github_boost as gb  # noqa: E402
import npm_manager  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  [OK] " if cond else "  [FAIL] ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def run(cmd, timeout=60):
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, shell=False,
                          **npm_manager.spawn_kwargs())
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


curl = shutil.which("curl.exe") or shutil.which("curl")
git = shutil.which("git.exe") or shutil.which("git")
print(f"curl = {curl}")
print(f"git  = {git}")
if not curl or not git:
    print("缺少 curl 或 git，无法做端到端自检")
    sys.exit(1)

print("\n1) 启动本地加速代理并等待优选表就绪")
gb.PROXY_PORT = 8047
ok, msg = gb.start_proxy()          # 启动后会后台开始刷新优选表
check("代理启动", ok, msg)
if not ok:
    sys.exit(1)

t0 = time.perf_counter()
while time.perf_counter() - t0 < 90:
    ready = gb.github_stats()["hosts_ready"]
    if ready >= 3:
        break
    time.sleep(1)
ready = gb.github_stats()["hosts_ready"]
print(f"   优选表就绪 {ready}/{gb.github_stats()['hosts_total']} 个域名，耗时 {time.perf_counter() - t0:.1f}s")
check("优选表已就绪（至少 3 个域名）", ready >= 3, f"{ready} 个")
for row in gb.ip_table():
    if row["ip"]:
        print(f"     {row['host']:<32} {row['ip']:<18} {row['ms']:7.1f} ms  {row['note']}")

proxy = gb.proxy_url()
hits_before = gb.github_stats()["hits"]

print("\n2) curl 经代理访问 GitHub（HTTPS CONNECT 隧道）")
code, out, err = run([curl, "-s", "-o", "NUL", "-w", "%{http_code} %{time_total} %{remote_ip}",
                      "-x", proxy, "https://api.github.com/"], timeout=60)
print(f"   api.github.com  -> {out}")
check("api.github.com 经代理返回 200", out.startswith("200"), f"{code} {out} {err[:80]}")

code, out2, _ = run([curl, "-s", "-o", "NUL", "-w", "%{http_code} %{time_total}",
                     "-x", proxy,
                     "https://raw.githubusercontent.com/octocat/Hello-World/master/README"],
                    timeout=60)
print(f"   raw.githubusercontent.com -> {out2}")
check("raw.githubusercontent.com 经代理返回 200", out2.startswith("200"), out2)

print("\n3) 统计核对：是否真的走了优选 IP（而不是回退系统解析）")
stats = gb.github_stats()
new_hits = stats["hits"] - hits_before
print(f"   本次新增优选命中 {new_hits}，回退 {stats['fallbacks']}，最近连接：{stats['last_connect']}")
check("curl 的请求命中了优选 IP", new_hits >= 2, f"{new_hits}")
check("全程没有回退到系统解析", stats["fallbacks"] == 0, f"fallbacks={stats['fallbacks']}")
preferred = {row["ip"] for row in gb.ip_table() if row["ip"]}
used_ip = stats["last_connect"].split("→")[-1].strip()
check("最近一次连接用的是优选表里的 IP", used_ip in preferred, f"{used_ip}")

print("\n4) git 经代理 ls-remote 真实仓库")
code, out3, err3 = run([git, "-c", f"{gb.GIT_PROXY_KEYS[0]}={proxy}",
                        "ls-remote", "https://github.com/octocat/Hello-World.git", "HEAD"],
                       timeout=120)
sha = out3.split()[0] if out3 else ""
print(f"   ls-remote -> {out3[:80]}")
check("git 经代理成功拿到 commit SHA",
      code == 0 and len(sha) >= 7 and all(c in "0123456789abcdef" for c in sha.lower()),
      f"rc={code} {err3[:80]}")

print("\n5) 参考对比：不经代理（当前走本机 hosts/Steam++）")
code, direct, _ = run([curl, "-s", "-o", "NUL", "-w", "%{http_code} %{time_total} %{remote_ip}",
                       "https://api.github.com/"], timeout=60)
print(f"   直连 api.github.com -> {direct}（仅供参考：直连成不成取决于本机 hosts / 其它加速器）")

print("\n6) 停止代理")
ok, msg = gb.stop_proxy()
check("代理停止", ok, msg)
time.sleep(0.3)
try:
    socket.create_connection((gb.PROXY_HOST, gb.PROXY_PORT), timeout=1).close()
    check("停止后端口已释放", False, f"{gb.PROXY_PORT} 仍可连接")
except OSError:
    check("停止后端口已释放", True)

print()
print("结果:", "全部通过" if not failures else f"失败 {len(failures)} 项 {failures}")
sys.exit(1 if failures else 0)
