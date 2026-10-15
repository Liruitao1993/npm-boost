# -*- coding: utf-8 -*-
"""npm 配置管理后端：读写 .npmrc、镜像源管理、测速、npm 命令执行。"""
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

# Windows 下 npm 配置文件位置
NPMRC_PATH = Path.home() / ".npmrc"
# 备份文件名固定为 ~/.npmrc.bak（UI 与 README 都按这个名字提示）
NPMRC_BAK = NPMRC_PATH.with_name(NPMRC_PATH.name + ".bak")

# 内置镜像源列表（官方 + 国内常用镜像）
REGISTRIES = [
    {"name": "npmmirror（淘宝，国内推荐）", "url": "https://registry.npmmirror.com", "region": "cn"},
    {"name": "腾讯云", "url": "https://mirrors.cloud.tencent.com/npm/", "region": "cn"},
    {"name": "华为云", "url": "https://repo.huaweicloud.com/repository/npm/", "region": "cn"},
    {"name": "cnpm", "url": "https://r.cnpmjs.org", "region": "cn"},
    {"name": "npm 官方源", "url": "https://registry.npmjs.org", "region": "global"},
    {"name": "Yarn 官方源", "url": "https://registry.yarnpkg.com", "region": "global"},
]

# 常用二进制包的国内镜像（node-sass / electron / puppeteer 等下载慢的元凶）
BINARY_MIRRORS = {
    "ELECTRON_MIRROR": ("electron", "https://npmmirror.com/mirrors/electron/"),
    "SASS_BINARY_SITE": ("node-sass", "https://npmmirror.com/mirrors/node-sass/"),
    "PHANTOMJS_CDNURL": ("phantomjs", "https://npmmirror.com/mirrors/phantomjs/"),
    "PUPPETEER_DOWNLOAD_BASE_URL": ("puppeteer", "https://npmmirror.com/mirrors/chrome-for-testing"),
    "CHROMEDRIVER_CDNURL": ("chromedriver", "https://npmmirror.com/mirrors/chromedriver/"),
    "NODEJS_ORG_MIRROR": ("Node.js 本体", "https://npmmirror.com/mirrors/node/"),
    "NVM_NODEJS_ORG_MIRROR": ("nvm-node", "https://npmmirror.com/mirrors/node/"),
}

# 「极速模式」写入 .npmrc 的性能参数
FAST_PRESETS = {
    "fetch-retries": "3",
    "fetch-retry-mintimeout": "10000",
    "fetch-retry-maxtimeout": "60000",
    "fetch-timeout": "60000",
    "maxsockets": "15",
    "prefer-offline": "true",
    "audit": "false",
    "fund": "false",
    "progress": "true",
}

_OFFICIAL_REGISTRY = "https://registry.npmjs.org"


def read_npmrc() -> dict:
    """解析 ~/.npmrc 为键值对（忽略注释与空行）。"""
    result = {}
    if NPMRC_PATH.exists():
        try:
            text = NPMRC_PATH.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return result
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                result[k.strip()] = v.strip().strip('"')
    return result


def _update_npmrc(values: dict, removals=()) -> None:
    """按行最小改动写入 ~/.npmrc。

    只改动目标键：保留注释、空行、键顺序以及其它未知行（如私有源的鉴权行），
    避免整体重写配置文件带来的意外损失。
    """
    lines = []
    if NPMRC_PATH.exists():
        try:
            lines = NPMRC_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
    removals = set(removals)
    remaining = dict(values)
    out = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", ";")) and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in removals:
                continue
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)
    for key, value in remaining.items():
        out.append(f"{key}={value}")
    text = "\n".join(out)
    if text and not text.endswith("\n"):
        text += "\n"
    NPMRC_PATH.write_text(text, encoding="utf-8")


def write_npmrc(values: dict) -> None:
    """写入/更新若干键（保留文件其余内容）。"""
    _update_npmrc(values)


def remove_npmrc_keys(keys: list) -> None:
    """从 .npmrc 中删除指定键（保留文件其余内容）。"""
    _update_npmrc({}, keys)


def backup_npmrc(force: bool = False) -> Path:
    """备份 .npmrc 到 ~/.npmrc.bak，返回备份路径。

    默认只在备份不存在时创建，保证「恢复备份」永远能回到最初那次配置；
    force=True 时才覆盖已有备份。
    """
    try:
        if NPMRC_PATH.exists() and (force or not NPMRC_BAK.exists()):
            shutil.copy2(NPMRC_PATH, NPMRC_BAK)
    except OSError:
        pass
    return NPMRC_BAK


def has_backup() -> bool:
    return NPMRC_BAK.exists()


def restore_npmrc() -> bool:
    """从 ~/.npmrc.bak 恢复。"""
    try:
        if NPMRC_BAK.exists():
            shutil.copy2(NPMRC_BAK, NPMRC_PATH)
            return True
    except OSError:
        pass
    return False


def raw_registry():
    """返回 .npmrc 里显式配置的 registry；未配置时为 None。"""
    return read_npmrc().get("registry")


def current_registry() -> str:
    return raw_registry() or _OFFICIAL_REGISTRY


def is_accelerated(registry: str = None) -> bool:
    """判断当前源是否算「已加速」：国内镜像或本地缓存代理。"""
    value = (registry if registry is not None else current_registry()) or ""
    if "127.0.0.1" in value or "localhost" in value:
        return True
    return any(reg["url"].rstrip("/") == value.rstrip("/") for reg in REGISTRIES if reg["region"] == "cn")


def apply_registry(url: str) -> None:
    write_npmrc({"registry": url})


def restore_registry(previous) -> None:
    """把 registry 恢复到某个先前状态；previous 为 None 表示原本没配 registry。"""
    if previous is None:
        remove_npmrc_keys(["registry"])
    else:
        write_npmrc({"registry": previous})


def apply_fast_mode(enable: bool) -> None:
    """开启/关闭极速模式（性能参数 + 关闭 audit/fund 检查）。"""
    if enable:
        write_npmrc(dict(FAST_PRESETS))
    else:
        remove_npmrc_keys(list(FAST_PRESETS.keys()))


def apply_binary_mirrors(enable: bool) -> None:
    """开启/关闭二进制包国内镜像（写入 .npmrc 环境变量风格键）。"""
    if enable:
        write_npmrc({k: v[1] for k, v in BINARY_MIRRORS.items()})
    else:
        remove_npmrc_keys(list(BINARY_MIRRORS.keys()))


def measure_registry(url: str, timeout: float = 6.0) -> dict:
    """测量镜像源延迟（ms）与状态。

    优先探测标准探活端点 /-/ping（6 家镜像实测均支持，且比拉元数据轻量）；
    只有「拿到了 HTTP 状态码但不是 200」才回退 /px 与根路径。连接层错误（网络、
    证书、超时）会立刻返回——换路径必然同样失败，这样失败源最多只占用一个
    timeout，不会把整轮测速拖到超过调用方的等待上限。

    ⚠ 别再依赖「根路径」判活：腾讯云根路径返回 404，会把能用的源误判为失败。
    """
    base = url.rstrip("/")
    t0 = time.perf_counter()
    last = {"ok": False, "ms": 0, "status": "无响应"}
    for path in ("/-/ping", "/px", "/"):
        try:
            resp = requests.get(base + path, timeout=timeout,
                                headers={"User-Agent": "npm-accelerator/1.1"})
        except Exception as e:
            return {"ok": False, "ms": int((time.perf_counter() - t0) * 1000),
                    "status": str(e)[:60]}
        last = {"ok": resp.status_code == 200,
                "ms": int((time.perf_counter() - t0) * 1000),
                "status": resp.status_code}
        if last["ok"]:
            return last
    return last


def measure_all(timeout: float = 8.0) -> list:
    """并发测量所有镜像源，返回结果列表（应在后台线程里调用）。"""
    results = [None] * len(REGISTRIES)

    def worker(idx, reg):
        r = measure_registry(reg["url"], timeout=timeout)
        r.update(reg)
        results[idx] = r

    threads = [threading.Thread(target=worker, args=(i, reg), daemon=True)
               for i, reg in enumerate(REGISTRIES)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout + 6)
    return [r for r in results if r is not None]


def measure_all_async(callback) -> None:
    """并发测速并在完成后回调 callback(results)；兼容旧调用方。"""
    def worker():
        callback(measure_all())
    threading.Thread(target=worker, daemon=True).start()


def spawn_kwargs() -> dict:
    """启动控制台子进程时要附加的参数（Windows）。

    GUI 程序（尤其打包后的 --windowed exe）自身没有控制台，直接调用 npm.cmd /
    taskkill 这类控制台程序，Windows 会为它们新建一个黑色控制台窗口，在用户眼前
    一闪一闪。附加 CREATE_NO_WINDOW 就不会有窗口，顺带还更快（省掉创建控制台宿主
    conhost 的开销：实测 npm --version 1.5s → 0.7s）。
    """
    if sys.platform != "win32":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}


def run_npm(args: list, cwd: str = None, timeout: int = 600) -> tuple:
    """执行 npm 命令，返回 (exit_code, 合并输出文本)。"""
    npm_cmd = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
    try:
        proc = subprocess.run(
            [npm_cmd] + args,
            cwd=cwd, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, shell=False, **spawn_kwargs(),
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out.strip()
    except FileNotFoundError:
        return 1, "未找到 npm 命令，请确认 Node.js 已安装并在 PATH 中。"
    except subprocess.TimeoutExpired:
        return 1, f"命令超时（>{timeout}s），已终止。"
    except Exception as e:
        return 1, f"执行出错：{e}"


def node_version() -> str:
    """读取 Node.js 版本（以前这里错拿 npm --version 当 Node 版本）。"""
    node_cmd = shutil.which("node") or shutil.which("node.exe")
    if not node_cmd:
        return "未安装"
    try:
        proc = subprocess.run([node_cmd, "--version"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=20, shell=False,
                              **spawn_kwargs())
        out = (proc.stdout or "").strip()
        return out or "未知"
    except Exception as e:
        return f"读取失败：{str(e)[:30]}"


def _dir_size_limited(root: Path, max_seconds: float = 3.0, max_files: int = 80000) -> int:
    """统计目录体积，带时间与文件数上限，避免大缓存目录拖住界面。"""
    total = 0
    count = 0
    deadline = time.perf_counter() + max_seconds
    try:
        for path in root.rglob("*"):
            if path.is_file():
                try:
                    total += path.stat().st_size
                except OSError:
                    pass
                count += 1
                if count >= max_files or time.perf_counter() > deadline:
                    break
    except OSError:
        pass
    return total


def format_size(num_bytes: int) -> str:
    if num_bytes >= 1024 ** 3:
        return f"{num_bytes / 1024 ** 3:.2f} GB"
    return f"{num_bytes / 1024 ** 2:.1f} MB"


def ca_bundle_status() -> tuple:
    """检查 requests 使用的 CA 证书包是否存在，返回 (ok, 路径或原因)。

    打包时若漏掉 certifi 的 cacert.pem，exe 里所有 HTTPS 请求都会失败：
    镜像测速全红、本地代理回源全 502。启动时检查一次，好在界面上明确提示。
    """
    try:
        import certifi
        path = certifi.where()
        return (True, path) if Path(path).is_file() else (False, path)
    except Exception as e:
        return False, f"certifi 不可用：{str(e)[:60]}"


def npm_info_summary() -> dict:
    """采集 npm 环境：版本、node 版本、当前源、缓存目录、缓存大小。"""
    code, ver = run_npm(["--version"], timeout=60)
    cfg = read_npmrc()
    cache_dir = ""
    code3, cache_out = run_npm(["config", "get", "cache"], timeout=60)
    if code3 == 0 and cache_out:
        cache_dir = cache_out.splitlines()[-1].strip()
    cache_size = ""
    if cache_dir:
        cache_path = Path(cache_dir)
        if cache_path.exists():
            cache_size = format_size(_dir_size_limited(cache_path))
    ca_ok, ca_path = ca_bundle_status()
    return {
        "npm": ver.splitlines()[-1] if code == 0 and ver else "未知",
        "node": node_version(),
        "registry": current_registry(),
        "registry_configured": "registry" in cfg,
        "cache_dir": cache_dir,
        "cache_size": cache_size,
        "ca_ok": ca_ok,
        "ca_path": ca_path,
        "config": cfg,
    }


def clear_cache(callback=None) -> None:
    """清理 npm 缓存（后台线程执行）。"""
    def worker():
        run_npm(["cache", "clean", "--force"], timeout=600)
        if callback:
            callback()
    threading.Thread(target=worker, daemon=True).start()


def verify_cache(callback=None) -> None:
    """校验并修复 npm 缓存。"""
    def worker():
        run_npm(["cache", "verify"], timeout=600)
        if callback:
            callback()
    threading.Thread(target=worker, daemon=True).start()
