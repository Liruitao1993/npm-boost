# -*- coding: utf-8 -*-
"""npm_manager 回归自检：.npmrc 最小改动写入、备份/恢复、源恢复、Node 版本。

全部在临时目录里跑，不会触碰用户真实的 ~/.npmrc。

用法：
    "D:\\Program Files\\Python313\\python.exe" tests\\test_npm_manager.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import npm_manager  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="npm-accel-manager-"))
npm_manager.NPMRC_PATH = tmp / ".npmrc"
npm_manager.NPMRC_BAK = tmp / ".npmrc.bak"

failures = []


def check(name, cond, detail=""):
    print(("  [OK] " if cond else "  [FAIL] ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


npm_manager.NPMRC_PATH.write_text(
    "# 我的配置\n"
    "prefix=C:/usr/local\n"
    "registry=https://registry.npmmirror.com\n"
    "\n"
    "//private.example.com/:_authToken=SECRET\n"
    "legacy-peer-deps=true\n",
    encoding="utf-8",
)

print("1) 写入单键：保留注释/空行/顺序/未知行")
npm_manager.apply_registry("http://127.0.0.1:8046")
text = npm_manager.NPMRC_PATH.read_text(encoding="utf-8")
check("registry 已改为代理", "registry=http://127.0.0.1:8046" in text)
check("注释保留", "# 我的配置" in text)
check("空行保留", "\n\n" in text)
check("私有源鉴权行保留", "//private.example.com/:_authToken=SECRET" in text)
check("prefix 未被改动", "prefix=C:/usr/local" in text)
check("registry 仍在第 3 行", text.splitlines()[2] == "registry=http://127.0.0.1:8046")

print("2) 备份命名与「只备份一次」语义")
bak = npm_manager.backup_npmrc()
check("备份路径是 .npmrc.bak", bak.name == ".npmrc.bak", str(bak))
first_bak_text = bak.read_text(encoding="utf-8")
check("备份内容 = 备份时刻的当前配置", "legacy-peer-deps=true" in first_bak_text)
npm_manager.apply_registry("https://mirrors.cloud.tencent.com/npm/")
npm_manager.backup_npmrc()
check("再次备份不会覆盖最初备份", bak.read_text(encoding="utf-8") == first_bak_text)
npm_manager.backup_npmrc(force=True)
check("force=True 时才会覆盖", "tencent" in bak.read_text(encoding="utf-8"))

print("3) 恢复备份")
expected_bak = bak.read_text(encoding="utf-8")
npm_manager.apply_registry("https://registry.npmjs.org")
npm_manager.restore_npmrc()
check("恢复后回到备份内容", npm_manager.NPMRC_PATH.read_text(encoding="utf-8") == expected_bak)

print("4) 极速参数 / 二进制镜像 开关")
npm_manager.apply_fast_mode(True)
check("maxsockets 已写入", npm_manager.read_npmrc().get("maxsockets") == "15")
npm_manager.apply_binary_mirrors(True)
check("ELECTRON_MIRROR 已写入", "ELECTRON_MIRROR" in npm_manager.read_npmrc())
npm_manager.apply_fast_mode(False)
npm_manager.apply_binary_mirrors(False)
cfg = npm_manager.read_npmrc()
check("关闭后极速键被删除", not any(k in cfg for k in npm_manager.FAST_PRESETS))
check("关闭后二进制镜像键被删除", not any(k in cfg for k in npm_manager.BINARY_MIRRORS))
check("关闭后其它键仍在", cfg.get("prefix") == "C:/usr/local")

print("5) registry 恢复语义（None = 原本没配）")
npm_manager.remove_npmrc_keys(["registry"])
check("删除后 raw_registry 为 None", npm_manager.raw_registry() is None)
check("current_registry 回落到官方源", npm_manager.current_registry() == "https://registry.npmjs.org")
npm_manager.restore_registry("https://registry.npmmirror.com")
check("恢复指定源成功", npm_manager.raw_registry() == "https://registry.npmmirror.com")
npm_manager.restore_registry(None)
check("恢复 None 等于删除该键", npm_manager.raw_registry() is None)

print("6) 其它")
check("is_accelerated(npmmirror)", npm_manager.is_accelerated("https://registry.npmmirror.com"))
check("is_accelerated(代理)", npm_manager.is_accelerated("http://127.0.0.1:8046"))
check("is_accelerated(官方源) 为 False", not npm_manager.is_accelerated("https://registry.npmjs.org"))
nv = npm_manager.node_version()
check("Node 版本形如 vXX", nv.startswith("v") and nv[1].isdigit(), nv)
check("format_size(1536KB) -> 1.5 MB", npm_manager.format_size(1536 * 1024) == "1.5 MB")

print()
print("结果:", "全部通过" if not failures else f"失败 {len(failures)} 项 {failures}")
sys.exit(1 if failures else 0)
