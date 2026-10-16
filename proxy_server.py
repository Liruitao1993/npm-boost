# -*- coding: utf-8 -*-
"""本地 HTTP 缓存代理：转发 npm registry 请求并将元数据/tarball 缓存到磁盘。

工作原理：
  npm -> http://127.0.0.1:8046 -> 上游镜像源
                                    |
                              首次：下载并写缓存
                              再次：直接命中本地缓存（离线也能装）

加速点：
  1. 元数据缓存（带 TTL，避免每次都拉整包版本列表）
  2. tarball 永久缓存（同一包版本永远只下载一次）
  3. 元数据里的 dist.tarball 改写指向本代理，tarball 才会真正走缓存
  4. 多线程并发响应 + 上游连接池复用

正确性要点（改动前先读，别回退）：
  - 上游响应常是 gzip 压缩体，其 Content-Length 是「压缩后」长度。必须完整
    解压、按实际字节数写 Content-Length，否则客户端按错误长度读写，npm 会报
    HPE_INVALID_CONSTANT / JSON 截断。
  - 缓存一律存「解压后的明文」，并把 Content-Type / ETag 等随边车 *.meta.json
    一起存，命中时按原样回放。
  - 缓存 key 区分 npm 缩写元数据（application/vnd.npm.install-v1+json）与完整
    元数据，避免两类响应互相污染。
"""
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import requests

PROXY_PORT = 8046
CACHE_TTL_METADATA = 10 * 60  # 元数据缓存有效期 10 分钟
_DEFAULT_CACHE_ROOT = Path.home() / ".npm-accelerator-cache"
_CHUNK = 128 * 1024
# 这些响应编码由 requests 自动解压；其余编码原样透传（不解压、不缓存）
_PLAIN_ENCODINGS = (None, "", "identity", "gzip", "x-gzip", "deflate")

_state = {
    "server": None,
    "thread": None,
    "running": False,
    "upstream": "https://registry.npmmirror.com",
    "cache_root": _DEFAULT_CACHE_ROOT,
    "hits": 0,          # 缓存命中次数
    "misses": 0,        # 缓存未命中次数
    "bytes_saved": 0,   # 命中省下的下载字节数
    "rewrites": 0,      # 改写过的 tarball 地址数
    "log": [],
}
_lock = threading.Lock()

_session_obj = None
_session_lock = threading.Lock()


def _session() -> requests.Session:
    """全局共享的上游连接池（降低 TCP/TLS 握手开销）。"""
    global _session_obj
    with _session_lock:
        if _session_obj is None:
            s = requests.Session()
            adapter = requests.adapters.HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=1)
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            _session_obj = s
        return _session_obj


def proxy_stats() -> dict:
    with _lock:
        hits, misses = _state["hits"], _state["misses"]
        total = hits + misses
        return {
            "running": _state["running"],
            "upstream": _state["upstream"],
            "hits": hits,
            "misses": misses,
            "hit_rate": f"{hits / total * 100:.0f}%" if total else "—",
            "bytes_saved": _state["bytes_saved"],
            "rewrites": _state["rewrites"],
            "log": list(_state["log"][-100:]),
        }


def _log(msg: str) -> None:
    with _lock:
        stamp = time.strftime("%H:%M:%S")
        _state["log"].append(f"[{stamp}] {msg}")
        del _state["log"][:-100]


def reset_stats() -> None:
    with _lock:
        _state["hits"] = 0
        _state["misses"] = 0
        _state["bytes_saved"] = 0
        _state["rewrites"] = 0


# ==================== 缓存路径与元信息 ====================

def _is_tarball_path(path: str) -> bool:
    return urlsplit(path).path.endswith((".tgz", ".tar.gz"))


def _variant_for(accept: str) -> str:
    """npm 缩写元数据与完整元数据分开缓存，避免互相污染。"""
    if "application/vnd.npm.install-v1+json" in (accept or "").lower():
        return "abbrev"
    return "full"


def _cache_paths(cache_root: Path, url: str, variant: str, is_tarball: bool) -> tuple:
    """把 URL 映射到缓存文件与边车元信息文件路径。"""
    h = hashlib.sha256(url.encode()).hexdigest()
    sub = "tarballs" if is_tarball else "metadata"
    suffix = ".tgz" if is_tarball else f".{variant}.json"
    cpath = cache_root / sub / h[:2] / (h + suffix)
    return cpath, cpath.with_name(cpath.name + ".meta.json")


def _proxy_base() -> str:
    return f"http://127.0.0.1:{PROXY_PORT}"

def _cache_root() -> Path:
    with _lock:
        return Path(_state["cache_root"])


def set_cache_root(path=None) -> None:
    """切换代理缓存根目录；不传参数则回到默认的 ~/.npm-accelerator-cache。

    主要用于自检隔离，避免测试污染真实缓存。
    """
    with _lock:
        _state["cache_root"] = Path(path) if path else _DEFAULT_CACHE_ROOT


def _read_meta(mpath: Path, cpath: Path):
    """读取边车元信息；不存在、损坏或过期则返回 None（视为未命中）。"""
    try:
        if not (mpath.is_file() and cpath.is_file()):
            return None
        meta = json.loads(mpath.read_text(encoding="utf-8"))
        size = cpath.stat().st_size
    except (OSError, ValueError):
        return None
    if size <= 0:
        return None
    if not meta.get("is_tarball") and time.time() - cpath.stat().st_mtime >= CACHE_TTL_METADATA:
        return None
    meta["_size"] = size
    return meta


def _looks_json(content_type: str) -> bool:
    return "json" in (content_type or "").lower()


def _rewrite_tarball_urls(data: bytes, proxy_base: str, upstream: str) -> tuple:
    """把元数据里指向上游的 dist.tarball 改写到本代理，使 tarball 也走缓存。

    只改写 host 与上游一致的地址；第三方 CDN 的地址保持原样（直连但不会坏）。
    返回 (新字节, 改写条数)；解析失败时原样返回。
    """
    try:
        obj = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return data, 0
    if not isinstance(obj, dict):
        return data, 0
    upstream_hosts = {urlsplit(upstream).netloc.lower()}
    changed = 0
    versions = obj.get("versions")
    if isinstance(versions, dict):
        for item in versions.values():
            dist = item.get("dist") if isinstance(item, dict) else None
            if not isinstance(dist, dict):
                continue
            tarball = dist.get("tarball")
            if not isinstance(tarball, str):
                continue
            parts = urlsplit(tarball)
            if parts.netloc.lower() not in upstream_hosts:
                continue
            dist["tarball"] = proxy_base + parts.path + (f"?{parts.query}" if parts.query else "")
            changed += 1
    if not changed:
        return data, 0
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), changed


# ==================== HTTP 处理 ====================

class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "npm-accelerator/1.1"

    def log_message(self, *args):  # 静默默认日志
        pass

    def do_GET(self):
        self._handle(head_only=False)

    def do_HEAD(self):
        self._handle(head_only=True)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Allow", "GET, HEAD, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------- 主流程 ----------
    def _handle(self, head_only: bool):
        with _lock:
            upstream = _state["upstream"].rstrip("/")
            cache_root = _state["cache_root"]
        target = upstream + self.path
        is_tarball = _is_tarball_path(self.path)
        variant = "raw" if is_tarball else _variant_for(self.headers.get("Accept", ""))
        cpath, mpath = _cache_paths(cache_root, target, variant, is_tarball)
        range_header = self.headers.get("Range")

        if not range_header:
            meta = _read_meta(mpath, cpath)
            if meta is not None:
                self._serve_hit(cpath, meta, head_only)
                return

        self._fetch_and_serve(target, cpath, mpath, is_tarball, range_header, head_only, upstream)

    def _serve_hit(self, cpath: Path, meta: dict, head_only: bool):
        size = meta["_size"]
        with _lock:
            _state["hits"] += 1
            _state["bytes_saved"] += size
        self.send_response(200)
        self.send_header("Content-Type", meta.get("content_type") or "application/octet-stream")
        for key in ("ETag", "Last-Modified", "Cache-Control"):
            if meta.get(key):
                self.send_header(key, meta[key])
        self.send_header("Content-Length", str(size))
        self.send_header("X-Cache", "HIT")
        self.end_headers()
        try:
            if not head_only:
                with open(cpath, "rb") as f:
                    shutil.copyfileobj(f, self.wfile, _CHUNK)
        except OSError:
            pass
        _log(f"缓存命中 {self.path[:70]} ({size // 1024} KB)")

    def _fetch_and_serve(self, target, cpath, mpath, is_tarball, range_header, head_only, upstream):
        fwd = {}
        for key in ("Accept", "Accept-Language", "User-Agent", "Authorization",
                    "Range", "If-None-Match", "If-Modified-Since"):
            value = self.headers.get(key)
            if value:
                fwd[key] = value
        fwd["Accept-Encoding"] = "gzip, deflate"  # 压缩传输省带宽，本地统一解压
        try:
            resp = _session().get(target, headers=fwd, stream=True, timeout=(10, 120))
        except Exception as e:
            _log(f"回源失败 {self.path[:60]}: {str(e)[:80]}")
            self._fail(502, f"upstream error: {str(e)[:60]}")
            return

        with _lock:
            _state["misses"] += 1

        encoding = (resp.headers.get("Content-Encoding") or "").strip().lower()
        tmp_path = None
        try:
            if encoding not in _PLAIN_ENCODINGS:
                # 罕见的不可解压编码：原样透传，不缓存
                raw = resp.raw.read(decode_content=False) or b""
                self.send_response(resp.status_code)
                for key in ("Content-Type", "Content-Range", "Accept-Ranges", "ETag",
                            "Last-Modified", "Cache-Control", "Content-Encoding", "Location"):
                    if key in resp.headers:
                        self.send_header(key, resp.headers[key])
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("X-Cache", "MISS")
                self.end_headers()
                if not head_only and raw:
                    self.wfile.write(raw)
                _log(f"回源(透传 {encoding}) {self.path[:60]} -> {resp.status_code}")
                return

            # ---- 明文路径：先落盘拿到真实长度，再决定缓存与回送 ----
            tmp_path = cpath.with_name(f"{cpath.name}.part{os.getpid()}-{threading.get_ident()}")
            tmp_path.parent.mkdir(parents=True, exist_ok=True)
            total = 0
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(_CHUNK):
                    if chunk:
                        f.write(chunk)
                        total += len(chunk)

            content_type = resp.headers.get("Content-Type", "")
            cacheable = resp.status_code == 200 and not range_header and total > 0
            if cacheable and not is_tarball and _looks_json(content_type):
                data = tmp_path.read_bytes()
                new_data, count = _rewrite_tarball_urls(data, _proxy_base(), upstream)
                if count:
                    tmp_path.write_bytes(new_data)
                    total = len(new_data)
                    with _lock:
                        _state["rewrites"] += count
                    _log(f"改写 {count} 个 tarball 地址指向本代理")
            if cacheable:
                try:
                    os.replace(tmp_path, cpath)
                    tmp_path = None
                    mpath.write_text(json.dumps({
                        "url": target,
                        "content_type": content_type,
                        "etag": resp.headers.get("ETag", ""),
                        "last_modified": resp.headers.get("Last-Modified", ""),
                        "cache_control": resp.headers.get("Cache-Control", ""),
                        "is_tarball": bool(is_tarball),
                        "size": total,
                        "created": time.time(),
                    }, ensure_ascii=False), encoding="utf-8")
                except OSError:
                    pass  # 缓存失败不能影响代理主流程

            body_path = cpath if tmp_path is None else tmp_path
            self.send_response(resp.status_code)
            for key in ("Content-Type", "Content-Range", "Accept-Ranges", "ETag",
                        "Last-Modified", "Cache-Control", "Location"):
                if key in resp.headers:
                    self.send_header(key, resp.headers[key])
            self.send_header("Content-Length", str(total))
            self.send_header("X-Cache", "MISS")
            self.end_headers()
            _log(f"回源 {self.path[:70]} -> {resp.status_code} ({total // 1024} KB)")
            if not head_only and total:
                with open(body_path, "rb") as f:
                    shutil.copyfileobj(f, self.wfile, _CHUNK)
        except OSError as e:
            _log(f"传输中断 {self.path[:60]}: {str(e)[:60]}")
        finally:
            try:
                resp.close()
            except Exception:
                pass
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def _fail(self, code: int, message: str):
        try:
            self.send_error(code, message)
        except OSError:
            pass


# ==================== 启停与维护 ====================

class _QuietServer(ThreadingHTTPServer):
    """代理服务器：客户端提前断开（npm 常态）时不打印堆栈。"""

    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        pass


def start_proxy(upstream: str = None) -> tuple:
    """启动代理服务器，返回 (成功?, 消息)。"""
    with _lock:
        if _state["running"]:
            return True, f"代理已在运行（端口 {PROXY_PORT}）"
        if upstream:
            _state["upstream"] = upstream.rstrip("/")
        try:
            srv = _QuietServer(("127.0.0.1", PROXY_PORT), ProxyHandler)
        except OSError as e:
            return False, f"端口 {PROXY_PORT} 被占用：{e}"
        _state["server"] = srv
        _state["thread"] = threading.Thread(target=srv.serve_forever, daemon=True)
        _state["thread"].start()
        _state["running"] = True
        upstream_now = _state["upstream"]
    _log(f"代理已启动 127.0.0.1:{PROXY_PORT} -> {upstream_now}")
    return True, f"代理已启动：http://127.0.0.1:{PROXY_PORT}（上游 {upstream_now}）"


def stop_proxy() -> tuple:
    with _lock:
        if not _state["running"]:
            return True, "代理未在运行"
        srv = _state["server"]
        thread = _state["thread"]
        _state["running"] = False
        _state["server"] = None
        _state["thread"] = None
    if srv is not None:
        try:
            srv.shutdown()
            srv.server_close()
        except OSError:
            pass
    if thread is not None and thread.is_alive():
        thread.join(timeout=5)
    _log("代理已停止")
    return True, "代理已停止"


def proxy_registry_url() -> str:
    return _proxy_base()


def clear_proxy_cache() -> tuple:
    """清空代理缓存目录。"""
    root = _cache_root()
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
    reset_stats()
    _log("代理缓存已清空")
    return True, "代理缓存已清空"


# ==================== 自检 ====================

def self_test() -> dict:
    """离线自检：用 gzip 压缩的 mock 上游验证缓存、长度与 tarball 改写。

    覆盖历史缺陷：上游 gzip 响应的 Content-Length 是压缩后长度，直接转发会让
    客户端按错误长度读取，npm 报 HPE_INVALID_CONSTANT / JSON 截断。
    """
    import gzip
    import http.server as hs
    import urllib.request

    workdir = Path(tempfile.mkdtemp(prefix="npm-accel-selftest-"))
    workdir = Path(tempfile.mkdtemp(prefix="npm-accel-selftest-"))
    tarball_body = b"MOCK-TARBALL-CONTENT" * 64
    counts = {"meta": 0, "tarball": 0}
    info = {"port": 0}

    def upstream_payload() -> bytes:
        """mock 上游的元数据：dist.tarball 指向上游自身，等代理改写。"""
        return json.dumps({
            "name": "mock-pkg",
            "dist-tags": {"latest": "1.0.0"},
            "versions": {"1.0.0": {"name": "mock-pkg", "version": "1.0.0",
                                   "dist": {"tarball": f"http://127.0.0.1:{info['port']}/mock-pkg/-/mock-pkg-1.0.0.tgz",
                                            "integrity": "sha512-mock"}}},
        }).encode()

    class Mock(hs.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            if self.path.endswith(".tgz"):
                counts["tarball"] += 1
                body, ctype = tarball_body, "application/octet-stream"
            else:
                counts["meta"] += 1
                body = upstream_payload()
                ctype = "application/json"
            gz = gzip.compress(body)
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(gz)))  # 压缩后长度：旧实现就死在这
            self.end_headers()
            self.wfile.write(gz)

        def log_message(self, *a):
            pass

    mock_srv = hs.ThreadingHTTPServer(("127.0.0.1", 0), Mock)
    mock_srv.daemon_threads = True
    mock_port = mock_srv.server_address[1]
    info["port"] = mock_port
    threading.Thread(target=mock_srv.serve_forever, daemon=True).start()

    old_root = _cache_root()
    set_cache_root(workdir)
    stop_proxy()
    reset_stats()
    result = {"ok": False, "msg": "未开始"}
    try:
        ok, msg = start_proxy(f"http://127.0.0.1:{mock_port}")
        if not ok:
            return {"ok": False, "msg": msg}
        base = proxy_registry_url()
        meta_url = f"{base}/mock-pkg"
        # 第一次回源（MISS），第二次命中缓存（HIT）
        first = urllib.request.urlopen(meta_url, timeout=10).read()
        second = urllib.request.urlopen(meta_url, timeout=10).read()
        tarball_url = json.loads(first)["versions"]["1.0.0"]["dist"]["tarball"]
        # tarball 地址应已被改写到本代理，且第二次命中缓存
        tb_first = urllib.request.urlopen(tarball_url, timeout=10).read()
        tb_second = urllib.request.urlopen(tarball_url, timeout=10).read()
        st = proxy_stats()
        result = {
            "ok": (first == second
                   and tarball_url.startswith(base)
                   and tb_first == tb_second == tarball_body
                   and st["hits"] >= 2
                   and counts["meta"] == 1
                   and counts["tarball"] == 1),
            "hits": st["hits"], "misses": st["misses"],
            "upstream_meta_requests": counts["meta"],
            "upstream_tarball_requests": counts["tarball"],
            "tarball_rewritten_to": tarball_url,
            "msg": (f"元数据命中 {st['hits']} 次，上游只被请求 {counts['meta']} 次；"
                    f"tarball 上游只被请求 {counts['tarball']} 次"),
        }
    except Exception as e:
        result = {"ok": False, "msg": f"{type(e).__name__}: {e}"}
    finally:
        set_cache_root(old_root)
        mock_srv.shutdown()
        mock_srv.server_close()
        shutil.rmtree(workdir, ignore_errors=True)
    return result
