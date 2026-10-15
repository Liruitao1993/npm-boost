# -*- coding: utf-8 -*-
"""真实 npm 端到端验证：本地缓存代理是否真的加速（需要联网）。

模拟两台机器（各自全新的 npm 缓存）安装同一个包：
  第一次：代理回源（MISS）
  第二次：代理命中本地缓存（HIT），且上游不再被请求

全程使用独立临时缓存目录，不会动真实代理缓存，也不会改 ~/.npmrc。

用法（退出码 0 才算通过）：
    "D:\\Program Files\\Python313\\python.exe" tests\\verify_proxy_install.py
"""
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import npm_manager  # noqa: E402
import proxy_server  # noqa: E402

UPSTREAM = "https://registry.npmmirror.com"
root = Path(tempfile.mkdtemp(prefix="npm-accel-verify-"))
old_cache_root = None
failures = []


def check(name, cond, detail=""):
    print(("  [OK] " if cond else "  [FAIL] ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def install(project: str) -> tuple:
    work = root / project
    work.mkdir(parents=True, exist_ok=True)
    (work / "package.json").write_text('{"name":"probe","version":"1.0.0"}', encoding="utf-8")
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    t0 = time.perf_counter()
    proc = subprocess.run(
        [npm, "install", "lodash", "--registry", proxy_server.proxy_registry_url(),
         "--cache", str(work / ".npmcache"), "--no-audit", "--no-fund"],
        cwd=str(work), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=420, **npm_manager.spawn_kwargs())
    dt = time.perf_counter() - t0
    print(f"  [{project}] 退出码 {proc.returncode}，耗时 {dt:.1f}s")
    if proc.returncode != 0:
        print("    " + "\n    ".join(proc.stderr.strip().splitlines()[-6:]))
    return proc.returncode, (work / "node_modules").exists()


try:
    old_cache_root = proxy_server._cache_root()
    proxy_server.set_cache_root(root / "proxycache")
    ok, msg = proxy_server.start_proxy(UPSTREAM)
    print("  代理:", msg)
    check("代理已启动", ok)

    code1, installed1 = install("machineA")
    st1 = proxy_server.proxy_stats()
    print(f"  第一次后：hits={st1['hits']} misses={st1['misses']} rewrites={st1['rewrites']}")
    check("第一次安装成功", code1 == 0 and installed1)
    check("第一次全部回源（缓存为空）", st1["misses"] > 0 and st1["hits"] == 0)
    check("元数据里的 tarball 已被改写到代理", st1["rewrites"] > 0)

    code2, installed2 = install("machineB")
    st2 = proxy_server.proxy_stats()
    print(f"  第二次后：hits={st2['hits']} misses={st2['misses']}")
    check("第二次安装成功", code2 == 0 and installed2)
    check("第二次命中代理缓存", st2["hits"] - st1["hits"] > 0,
          f"新增命中 {st2['hits'] - st1['hits']}")
    check("第二次没有新增回源", st2["misses"] == st1["misses"])
    print("  代理日志尾部：")
    for line in st2["log"][-6:]:
        print("    " + line)
finally:
    proxy_server.stop_proxy()
    if old_cache_root is not None:
        proxy_server.set_cache_root(old_cache_root)
    shutil.rmtree(root, ignore_errors=True)

print()
print("结果:", "全部通过" if not failures else f"失败 {len(failures)} 项：{failures}")
sys.exit(1 if failures else 0)
