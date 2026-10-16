# -*- coding: utf-8 -*-
"""GitHub 加速后端：DoH 解析候选 IP → TLS 实测优选 → 本地 CONNECT 隧道代理。

思路与 FastGithub 同源：**绕开被污染/被改写的 DNS，直连实测最快的 IP**。
但本实现**不做 TLS 中间人**——本地只开一个 CONNECT 隧道，SNI 与证书仍然由
你的客户端与 GitHub 直接协商，因此：

* 不需要生成、分发、信任自签 CA 证书（FastGithub 需要，还得让 git 关掉 sslverify）
* 不改 hosts（不需要管理员权限，也不会和 Steam++ 这类同样改写 hosts 的工具抢域名）

工作流程：

1. `resolve_candidates()`：用 DoH（阿里/腾讯/360，本机实测 Cloudflare/Google 被阻断）
   查询候选 IP，绕开本机 hosts 与运营商 DNS 污染；
2. `probe()`：对每个候选 IP 做 TCP 连接 + TLS 握手（SNI=域名、校验证书）+ 发一个
   HEAD 请求，三步都通过才算可用，按握手耗时排序；
3. `refresh_all()`：并发完成上面两步，得到「域名 → 优选 IP 列表」；
4. 本地 CONNECT 代理：命中 GitHub 域名时把目标 IP 换成优选 IP（失败自动换下一个），
   其它域名原样直连，所以把它设成系统/程序代理也不会影响别的网站。
"""
import ipaddress
import json
import select
import shutil
import socket
import ssl
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

import npm_manager

PROXY_HOST = "127.0.0.1"
PROXY_PORT = 8047            # 8046 已被 npm 缓存代理占用
IP_TTL = 600.0               # 优选结果的有效期（秒），过期后现场重新解析
PROBE_TIMEOUT = 5.0          # 单个候选 IP 的探测超时
CONNECT_TIMEOUT = 8.0        # 隧道建连超时
RELAY_IDLE = 120.0           # 隧道空闲多久后关闭
LOG_LIMIT = 200

# 需要加速的域名（github.com / api / codeload / raw / 头像 / gist / Pages …）
GITHUB_HOSTS = (
    "github.com",
    "api.github.com",
    "codeload.github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "github.githubassets.com",
    "avatars.githubusercontent.com",
    "user-images.githubusercontent.com",
    "camo.githubusercontent.com",
    "gist.github.com",
    "gist.githubusercontent.com",
    "raw.github.com",
    "uploads.github.com",
    "education.github.com",
    "githubapp.com",
    "github.io",
)
# 用于后缀匹配的域名族（注意必须按「.后缀」匹配，否则 notgithub.com 会被误判）
GITHUB_SUFFIXES = (".github.com", ".githubusercontent.com", ".githubassets.com", ".github.io")

# 实测可用的 DoH 提供方（Cloudflare / Google 在本机被阻断，故不默认启用）
DOH_PROVIDERS = (
    ("阿里 DNS", "https://dns.alidns.com/resolve"),
    ("腾讯 DNS", "https://doh.pub/dns-query"),
    ("360 DNS", "https://doh.360.cn/resolve"),
)

# git 只对 GitHub 走本地加速，不动用户其它仓库的代理设置
GIT_PROXY_KEYS = ("http.https://github.com.proxy", "http.https://gist.github.com.proxy")
GIT_PROXY_BACKUP = Path.home() / ".npm-boost-git-proxy.json"

_state = {
    "running": False,
    "server": None,
    "thread": None,
    "ip_map": {},          # host -> {"ips": [...], "detail": [...], "updated": ts}
    "stats": {"tunnels": 0, "hits": 0, "fallbacks": 0, "failed": 0,
              "bytes": 0, "bytes_down": 0, "bytes_up": 0,
              "last_connect": "", "last_error": ""},
    "ip_failures": {},     # host -> {ip: 连续失败次数}（隧道零响应则记一次，成功即清零）
    "log": [],
}
_lock = threading.RLock()
_host_locks = {}


# ================= 日志 =================
def _log(message: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    with _lock:
        _state["log"].append(line)
        if len(_state["log"]) > LOG_LIMIT:
            del _state["log"][: len(_state["log"]) - LOG_LIMIT]


def github_log() -> list:
    with _lock:
        return list(_state["log"])


# ================= 域名判定 =================
def is_github_host(host: str) -> bool:
    """该域名是否属于 GitHub（严格后缀匹配，避免 notgithub.com 被误加速）。"""
    if not host:
        return False
    host = host.lower().strip("[]").rstrip(".")
    if host in GITHUB_HOSTS:
        return True
    return any(host.endswith(suffix) for suffix in GITHUB_SUFFIXES)


# ================= DoH 解析 =================
def _doh_query(url: str, host: str, timeout: float = 6.0) -> list:
    """向一个 DoH 端点查询 A 记录，返回 IPv4 列表（失败抛异常，由调用方汇总）。"""
    resp = requests.get(url, params={"name": host, "type": "A"},
                        headers={"accept": "application/dns-json"},
                        timeout=(4.0, timeout))
    resp.raise_for_status()
    answers = resp.json().get("Answer", [])
    ips = []
    for item in answers:
        if item.get("type") != 1:
            continue
        data = str(item.get("data", "")).strip()
        try:
            if ipaddress.ip_address(data).version == 4 and data not in ips:
                ips.append(data)
        except ValueError:
            continue
    return ips


def resolve_candidates(host: str, timeout: float = 6.0) -> list:
    """合并多个 DoH 提供方的结果，去重且保持发现顺序。"""
    seen = []
    for _name, url in DOH_PROVIDERS:
        try:
            for ip in _doh_query(url, host, timeout=timeout):
                if ip not in seen:
                    seen.append(ip)
        except Exception:
            continue
    return seen


# ================= IP 探测（连通 / 证书 / 响应 三重确认）=================
def _ssl_context():
    """优选用系统信任链，缺失时退回 certifi（exe 里已保证 certifi 被打包）。"""
    try:
        return ssl.create_default_context()
    except Exception:
        try:
            import certifi
            return ssl.create_default_context(cafile=certifi.where())
        except Exception:
            return ssl._create_unverified_context()  # pragma: no cover - 极端兜底


def probe(host: str, ip: str, port: int = 443, timeout: float = PROBE_TIMEOUT) -> dict:
    """探测单个候选 IP，返回 {ip, tcp, tls, ms, note}。

    只有 TCP 能连、TLS 握手成功且**证书就是该域名的**、并且服务端回了 HTTP 响应，
    才认为该 IP 真能服务这个域名（有些 IP 443 通但证书不匹配，直接用会握手失败）。
    """
    result = {"ip": ip, "tcp": False, "tls": False, "ms": 0.0, "note": ""}
    started = time.perf_counter()
    try:
        raw = socket.create_connection((ip, port), timeout=timeout)
        result["tcp"] = True
    except OSError as e:
        result["note"] = f"TCP 不通（{type(e).__name__}）"
        return result
    try:
        context = _ssl_context()
        with context.wrap_socket(raw, server_hostname=host) as tls:
            tls.settimeout(timeout)
            result["ms"] = (time.perf_counter() - started) * 1000
            request = (f"HEAD / HTTP/1.1\r\nHost: {host}\r\n"
                       f"User-Agent: npm-boost\r\nConnection: close\r\n\r\n")
            tls.sendall(request.encode("ascii", "replace"))
            head = tls.recv(64)
        if not head.startswith(b"HTTP/"):
            result["note"] = f"未收到 HTTP 响应（{head[:16]!r}）"
            return result
        code = head.split(b" ")[1].decode("ascii", "replace") if b" " in head else "?"
        result["tls"] = True
        result["note"] = f"HTTP {code}"
    except ssl.SSLCertVerificationError as e:
        result["note"] = f"证书校验失败（{str(e)[:60]}）"
    except ssl.SSLError as e:
        result["note"] = f"TLS 失败（{str(e)[:50]}）"
    except OSError as e:
        result["note"] = f"传输失败（{type(e).__name__}）"
    finally:
        try:
            raw.close()
        except OSError:
            pass
    return result


def _rank(detail: list) -> tuple:
    """优选排序：证书验证通过的排前面，再按握手耗时升序。"""
    return sorted(detail, key=lambda d: (0 if d["tls"] else 1, d["ms"] if d["ms"] else 9e9))


def probe_host(host: str, ips: list = None, timeout: float = PROBE_TIMEOUT,
               workers: int = 8) -> list:
    """并发探测某个域名的所有候选 IP，返回排好序的明细。"""
    ips = list(ips if ips is not None else resolve_candidates(host))
    if not ips:
        return []
    results = []
    with ThreadPoolExecutor(max_workers=min(workers, len(ips))) as pool:
        futures = [pool.submit(probe, host, ip, 443, timeout) for ip in ips]
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:  # pragma: no cover - 探测内部已兜底
                results.append({"ip": "?", "tcp": False, "tls": False, "ms": 0.0,
                                "note": f"{type(e).__name__}"})
    return _rank(results)


def refresh_host(host: str, timeout: float = PROBE_TIMEOUT) -> dict:
    """解析 + 测速一个域名，写入优选表并返回该条目（同一域名并发时只算一次）。"""
    with _lock:
        lock = _host_locks.setdefault(host, threading.Lock())
    with lock:
        detail = probe_host(host, timeout=timeout)
        usable = [d["ip"] for d in detail if d["tls"]]
        if not usable:  # 没有证书验证通过的，退而求其次用 TCP 能连的
            usable = [d["ip"] for d in detail if d["tcp"]]
        entry = {"ips": usable, "detail": detail, "updated": time.time()}
        with _lock:
            _state["ip_map"][host] = entry
        if detail:
            best = detail[0]
            _log(f"{host} → {best['ip']}（{best['ms']:.0f} ms，{best['note']}）"
                 if best["tls"] else f"{host} → 无证书验证通过的 IP，退回 TCP 可用 {usable}")
        else:
            _log(f"{host} → 没有解析到候选 IP")
        return entry


def refresh_all(hosts=None, on_progress=None, timeout: float = PROBE_TIMEOUT,
                dns_workers: int = 8, probe_workers: int = 20) -> dict:
    """并发刷新多个域名，返回 {host: entry}。

    on_progress(done, total, label) 会在每个候选 IP 探测完成后回调，供界面显示进度。
    """
    targets = list(hosts if hosts is not None else GITHUB_HOSTS)
    resolved = {}
    with ThreadPoolExecutor(max_workers=min(dns_workers, len(targets))) as pool:
        futures = {pool.submit(resolve_candidates, h, timeout): h for h in targets}
        for future in as_completed(futures):
            host = futures[future]
            try:
                resolved[host] = future.result()
            except Exception:
                resolved[host] = []
    tasks = [(host, ip) for host in targets for ip in resolved.get(host, [])]
    total = len(tasks)
    done = 0
    if on_progress:
        on_progress(0, total, "开始探测候选 IP…")
    detail_by_host = {host: [] for host in targets}
    if tasks:
        with ThreadPoolExecutor(max_workers=min(probe_workers, total)) as pool:
            futures = {pool.submit(probe, host, ip, 443, timeout): (host, ip)
                       for host, ip in tasks}
            for future in as_completed(futures):
                host, ip = futures[future]
                try:
                    detail_by_host[host].append(future.result())
                except Exception:
                    pass
                done += 1
                if on_progress:
                    on_progress(done, total, f"{host} {ip}")
    summary = {}
    for host in targets:
        detail = _rank(detail_by_host[host])
        usable = [d["ip"] for d in detail if d["tls"]] or [d["ip"] for d in detail if d["tcp"]]
        entry = {"ips": usable, "detail": detail, "updated": time.time()}
        with _lock:
            _state["ip_map"][host] = entry
        summary[host] = entry
    ok_hosts = [h for h in targets if summary[h]["ips"]]
    _log(f"优选完成：{len(ok_hosts)}/{len(targets)} 个域名可用，"
         f"共探测 {total} 个候选 IP")
    return summary


def ip_table() -> list:
    """给界面用的优选结果表：[{host, ip, ms, note, tls}]，按域名固定顺序。"""
    rows = []
    with _lock:
        ip_map = dict(_state["ip_map"])
    for host in GITHUB_HOSTS:
        entry = ip_map.get(host)
        if not entry:
            rows.append({"host": host, "ip": "", "ms": 0.0, "note": "尚未测速", "tls": False})
            continue
        usable = entry["ips"]
        best = next((d for d in entry["detail"] if d["ip"] in usable), None) if usable else None
        if best:
            rows.append({"host": host, "ip": best["ip"], "ms": best["ms"],
                         "note": best["note"], "tls": best["tls"]})
        else:
            detail = entry["detail"][0] if entry["detail"] else None
            rows.append({"host": host, "ip": "", "ms": 0.0,
                         "note": detail["note"] if detail else "无可用 IP", "tls": False})
    return rows


# ================= 隧道 =================
def _candidate_ips(host: str) -> list:
    """取该域名的优选 IP（过期、缺失或上次全部失败时现场重新解析测速）。

    非 GitHub 域名返回空表（调用方会直连）。最近「连上却拿不到任何响应」的 IP
    排在后面；连续失败两次的暂时不用，但若所有候选都失败过就原样返回 ——
    宁可再试一次，也不要让这个域名彻底不可用。
    """
    if not is_github_host(host):
        return []
    with _lock:
        entry = _state["ip_map"].get(host)
    if not entry or not entry["ips"] or (time.time() - entry["updated"]) >= IP_TTL:
        entry = refresh_host(host)
    if not entry or not entry["ips"]:
        return []
    with _lock:
        failures = dict(_state["ip_failures"].get(host, {}))
    ordered = sorted(entry["ips"], key=lambda ip: failures.get(ip, 0))
    fresh = [ip for ip in ordered if failures.get(ip, 0) < 2]
    return fresh or ordered


def _judge_tunnel(host: str, ip: str, from_upstream: int) -> None:
    """隧道结束后判断这个优选 IP 到底能不能用。

    上游一个字节都没回，说明它实际不可用（TCP 通、TLS 无响应）：记一次失败，
    后续连接就会优先换用别的候选；有正常回包则清掉失败记录。
    """
    if not ip:
        return
    with _lock:
        per_host = _state["ip_failures"].setdefault(host, {})
        if from_upstream > 0:
            per_host.pop(ip, 0)
            return
        per_host[ip] = per_host.get(ip, 0) + 1
        count = per_host[ip]
    _log(f"{host} 的优选 IP {ip} 连上后零响应（第 {count} 次），已标记待换")


class _RelayBytes(int):
    """转发字节数（int 子类），额外记住「上游回了多少字节」。

    上游一个字节都没回，说明这个 IP 虽然 TCP 连得上、实际却不工作（实测
    raw.githubusercontent.com 就有这种 IP）：据此把它标记下来，下次换别的候选。
    """

    def __new__(cls, total: int, from_upstream: int = 0, from_client: int = 0):
        obj = super().__new__(cls, total)
        obj.from_upstream = from_upstream
        obj.from_client = from_client
        return obj


def _relay(client: socket.socket, upstream: socket.socket, idle: float = RELAY_IDLE) -> _RelayBytes:
    """双向转发；任一端 EOF 只做半关闭，让另一端把剩余数据读完。

    返回转发总字节数（int 子类 _RelayBytes，附带 from_upstream / from_client）。
    """
    remaining = [client, upstream]
    total = 0
    from_upstream = 0
    from_client = 0
    last = time.time()
    while remaining:
        try:
            readable, _, errored = select.select(remaining, [], remaining, 2.0)
        except (OSError, ValueError):
            break
        for sock in errored:
            if sock in remaining:
                remaining.remove(sock)
        for src in readable:
            dst = upstream if src is client else client
            try:
                data = src.recv(65536)
            except OSError:
                data = b""
            if not data:
                if src in remaining:
                    remaining.remove(src)
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                continue
            try:
                dst.sendall(data)
            except OSError:
                remaining = []
                break
            total += len(data)
            if src is upstream:
                from_upstream += len(data)   # 上游→客户端：下载
            else:
                from_client += len(data)     # 客户端→上游：上传（如 git push）
            last = time.time()
        if time.time() - last > idle:
            break
    return _RelayBytes(total, from_upstream, from_client)


class _TunnelHandler(BaseHTTPRequestHandler):
    """只做 CONNECT 隧道（HTTPS）；其它方法按明文正向代理直连透传。"""

    protocol_version = "HTTP/1.1"
    server_version = "npm-boost-github"

    def log_message(self, fmt, *args):  # 收进我们自己的日志缓冲区
        try:
            _log("代理 " + (fmt % args))
        except Exception:
            pass

    def _open_upstream(self, host: str, port: int) -> socket.socket:
        """建到上游的连接：GitHub 域名优先用优选 IP，其余直连。

        实际用到的优选 IP 记在 self.used_ip，隧道结束后据此判断这个 IP 是否真能用。
        """
        last_error = ""
        ip_tried = []
        self.used_ip = None
        for ip in _candidate_ips(host):
            ip_tried.append(ip)
            try:
                sock = socket.create_connection((ip, port), timeout=CONNECT_TIMEOUT)
                with _lock:
                    stats = _state["stats"]
                    stats["tunnels"] += 1
                    stats["hits"] += 1
                    stats["last_connect"] = f"{host}:{port} → {ip}"
                self.used_ip = ip
                return sock
            except OSError as e:
                last_error = f"{ip}: {type(e).__name__}"
                continue
        # 非 GitHub 域名走到这里属于正常透传；GitHub 域名才是「优选全失败后的回退」
        is_gh = is_github_host(host)
        try:
            sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
            with _lock:
                stats = _state["stats"]
                stats["tunnels"] += 1
                stats["last_connect"] = (f"{host}:{port} → 系统解析（回退）" if is_gh
                                        else f"{host}:{port} → 直连")
                if is_gh:
                    stats["fallbacks"] += 1
            if ip_tried:
                _log(f"{host} 优选 IP 全部失败（{last_error}），回退系统解析")
            return sock
        except OSError as e:
            with _lock:
                stats = _state["stats"]
                stats["failed"] += 1
                stats["last_error"] = f"{host}:{port} {type(e).__name__}"
            _log(f"连接 {host}:{port} 失败：{type(e).__name__}")
            return None

    def do_CONNECT(self):
        host, _, port_text = self.path.partition(":")
        host = host.strip().lower().strip("[]")
        try:
            port = int(port_text or 443)
        except ValueError:
            self.send_error(400, "CONNECT target invalid")
            return
        upstream = self._open_upstream(host, port)
        if upstream is None:
            self.send_error(502, "无法连接上游")
            return
        try:
            self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self.wfile.flush()
        except OSError:
            upstream.close()
            return
        self.close_connection = True
        try:
            moved = _relay(self.connection, upstream)
            with _lock:
                _state["stats"]["bytes"] += moved
                _state["stats"]["bytes_down"] += getattr(moved, "from_upstream", 0)
                _state["stats"]["bytes_up"] += getattr(moved, "from_client", 0)
        finally:
            try:
                upstream.close()
            except OSError:
                pass
        _judge_tunnel(host, getattr(self, "used_ip", None), getattr(moved, "from_upstream", 0))

    def _plain_forward(self):
        """明文 HTTP 正向代理：直连透传（GitHub 全站走 HTTPS，这里只为不破坏其它站点）。"""
        if not self.path.lower().startswith("http://"):
            self.send_error(400, "仅支持绝对 URI")
            return
        rest = self.path[7:]
        host_port, _, path = rest.partition("/")
        host, _, port_text = host_port.partition(":")
        try:
            port = int(port_text or 80)
        except ValueError:
            self.send_error(400, "目标端口无效")
            return
        upstream = self._open_upstream(host, port)
        if upstream is None:
            self.send_error(502, "无法连接上游")
            return
        try:
            head = f"{self.command} /{path} {self.request_version}\r\n"
            for key, value in self.headers.items():
                if key.lower() in ("proxy-connection", "connection"):
                    continue
                head += f"{key}: {value}\r\n"
            head += "Connection: close\r\n\r\n"
            upstream.sendall(head.encode("utf-8", "replace"))
            body_len = int(self.headers.get("Content-Length") or 0)
            if body_len:
                upstream.sendall(self.rfile.read(body_len))
            self.close_connection = True
            moved = _relay(self.connection, upstream)
            with _lock:
                _state["stats"]["bytes"] += moved
                _state["stats"]["bytes_down"] += getattr(moved, "from_upstream", 0)
                _state["stats"]["bytes_up"] += getattr(moved, "from_client", 0)
        finally:
            try:
                upstream.close()
            except OSError:
                pass

    do_GET = _plain_forward
    do_POST = _plain_forward
    do_HEAD = _plain_forward
    do_PUT = _plain_forward
    do_DELETE = _plain_forward


class _TunnelServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# ================= 启停 / 状态 =================
def proxy_url() -> str:
    return f"http://{PROXY_HOST}:{PROXY_PORT}"


def start_proxy(refresh: bool = True) -> tuple:
    """启动本地加速代理；成功后按需后台刷新优选 IP。"""
    with _lock:
        if _state["running"]:
            return True, "GitHub 加速已在运行"
    try:
        server = _TunnelServer((PROXY_HOST, PROXY_PORT), _TunnelHandler)
    except OSError as e:
        return False, f"端口 {PROXY_PORT} 无法监听：{e}"
    thread = threading.Thread(target=server.serve_forever, name="github-tunnel", daemon=True)
    thread.start()
    with _lock:
        _state.update({"running": True, "server": server, "thread": thread})
    _log(f"GitHub 加速已启动：{proxy_url()}")
    if refresh:
        threading.Thread(target=_safe_refresh_all, name="github-refresh", daemon=True).start()
    return True, f"GitHub 加速已启动：{proxy_url()}"


def _safe_refresh_all():
    try:
        refresh_all()
    except Exception as e:  # pragma: no cover
        _log(f"优选刷新失败：{type(e).__name__}: {str(e)[:60]}")


def stop_proxy() -> tuple:
    with _lock:
        running = _state["running"]
        server = _state.get("server")
        thread = _state.get("thread")
        _state["running"] = False
    if server is not None:
        try:
            server.shutdown()
            server.server_close()
        except OSError:
            pass
    if thread is not None and thread.is_alive():
        thread.join(timeout=5)
    with _lock:
        _state["server"] = None
        _state["thread"] = None
    _log("GitHub 加速已停止")
    return (True, "GitHub 加速已停止") if running else (True, "GitHub 加速本来就没在运行")


def github_stats() -> dict:
    with _lock:
        stats = dict(_state["stats"])
        return {
            "running": _state["running"],
            "port": PROXY_PORT,
            "url": proxy_url(),
            "tunnels": stats["tunnels"],
            "hits": stats["hits"],
            "fallbacks": stats["fallbacks"],
            "failed": stats["failed"],
            "bytes": stats["bytes"],
            "bytes_down": stats["bytes_down"],   # 上游→客户端（下载）
            "bytes_up": stats["bytes_up"],       # 客户端→上游（上传，如 git push）
            "last_connect": stats["last_connect"],
            "last_error": stats["last_error"],
            "hosts_ready": sum(1 for e in _state["ip_map"].values() if e.get("ips")),
            "hosts_total": len(GITHUB_HOSTS),
            "log": list(_state["log"]),
        }


# ================= git 配置（只让 GitHub 走本地加速）=================
def _git(args, timeout: float = 20.0) -> tuple:
    git = shutil.which("git") or shutil.which("git.exe") or "git"
    try:
        proc = subprocess.run([git, "config", "--global"] + list(args),
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, shell=False,
                              **npm_manager.spawn_kwargs())
    except Exception as e:
        return 1, "", f"{type(e).__name__}: {str(e)[:60]}"
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def git_proxy_status() -> dict:
    values = {}
    for key in GIT_PROXY_KEYS:
        code, out, _ = _git(["--get", key])
        values[key] = out if code == 0 and out else None
    configured = [v for v in values.values() if v]
    return {
        "values": values,
        "configured": values[GIT_PROXY_KEYS[0]],
        "points_to_us": bool(values[GIT_PROXY_KEYS[0]]) and
        values[GIT_PROXY_KEYS[0]].rstrip("/") == proxy_url().rstrip("/"),
        "backup_exists": GIT_PROXY_BACKUP.is_file(),
    }


def git_proxy_command() -> str:
    return f"git config --global {GIT_PROXY_KEYS[0]} {proxy_url()}"


def apply_git_proxy() -> tuple:
    """把 git 的 GitHub 请求指向本地加速代理。

    首次设置前会把 git 里原本的代理配置备份到 ~/.npm-boost-git-proxy.json，
    之后不会覆盖这份备份，保证「还原」能回到用户本来的状态。
    """
    url = proxy_url()
    if not GIT_PROXY_BACKUP.is_file():
        original = {}
        for key in GIT_PROXY_KEYS:
            code, out, _ = _git(["--get", key])
            original[key] = out if code == 0 and out else None
        try:
            GIT_PROXY_BACKUP.write_text(json.dumps(original, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
        except OSError as e:
            return False, f"无法写入备份文件：{e}"
    for key in GIT_PROXY_KEYS:
        code, _, err = _git([key, url])
        if code != 0:
            return False, f"设置 {key} 失败：{err}"
    _log(f"已让 git 的 GitHub 请求走本地加速（{url}）")
    return True, f"已配置 git 走本地加速：{url}"


def restore_git_proxy() -> tuple:
    if not GIT_PROXY_BACKUP.is_file():
        # 没备份也允许硬还原：直接删掉我们可能写过的键
        for key in GIT_PROXY_KEYS:
            _git(["--unset", key])
        return True, "已清除 git 的 GitHub 代理配置（无备份文件）"
    try:
        original = json.loads(GIT_PROXY_BACKUP.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return False, f"备份文件无法读取：{e}"
    for key, value in original.items():
        if value:
            _git([key, value])
        else:
            _git(["--unset", key])
    try:
        GIT_PROXY_BACKUP.unlink()
    except OSError:
        pass
    _log("已还原 git 代理配置")
    return True, "已还原 git 代理配置"


# ================= 自检（--selftest 用）=================
def self_test() -> dict:
    """验证 DoH 解析 + 优选测速链路是否真的可用（需要联网）。"""
    lines = []
    usable = 0
    for host in ("github.com", "api.github.com", "raw.githubusercontent.com"):
        candidates = resolve_candidates(host)
        detail = probe_host(host, candidates) if candidates else []
        verified = [d for d in detail if d["tls"]]
        if verified:
            usable += 1
            best = verified[0]
            lines.append(f"{host} → {best['ip']} {best['ms']:.0f} ms（候选 {len(candidates)} 个）")
        elif detail:
            lines.append(f"{host} → 无证书验证通过的 IP（候选 {len(candidates)} 个）")
        else:
            lines.append(f"{host} → DoH 没有解析到候选 IP")
    ok = usable > 0
    return {"ok": ok, "usable": usable, "msg": "；".join(lines)}
