# -*- coding: utf-8 -*-
"""github_boost 回归自检（全离线，不依赖外网）。

覆盖：
  1. 域名判定（防止 notgithub.com / github.com.evil.com 被误加速）
  2. DoH 响应解析与多提供方容错
  3. 优选排序与「证书验证失败则降级」逻辑
  4. 本地 CONNECT 隧道：GitHub 域名替换为优选 IP、非 GitHub 域名直连透传
  5. 明文 HTTP 正向代理转发
  6. git 代理配置的备份 / 设置 / 还原（隔离到临时 GIT_CONFIG_GLOBAL，不碰真实配置）

用法：
    "D:\\Program Files\\Python313\\python.exe" tests\\test_github_boost.py
"""
import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import github_boost as gb  # noqa: E402

tmp = Path(tempfile.mkdtemp(prefix="npm-accel-github-"))
failures = []
skips = []


def check(name, cond, detail=""):
    print(("  [OK] " if cond else "  [FAIL] ") + name + (f"  {detail}" if detail else ""))
    if not cond:
        failures.append(name)


def skip(name, reason):
    print(f"  [SKIP] {name}  {reason}")
    skips.append(name)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------- 测试用假上游 ----------
class EchoServer:
    """把收到的字节转成大写回显，用来验证隧道是原样字节转发。"""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.stop_flag = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self.stop_flag.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    @staticmethod
    def _handle(conn):
        with conn:
            while True:
                try:
                    data = conn.recv(4096)
                except OSError:
                    return
                if not data:
                    return
                conn.sendall(data.upper())

    def close(self):
        self.stop_flag.set()
        try:
            self.sock.close()
        except OSError:
            pass


class TinyHttpServer:
    """固定回一个 HTTP 响应，用来验证明文正向代理转发。"""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.stop_flag = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self.stop_flag.is_set():
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    @staticmethod
    def _handle(conn):
        with conn:
            try:
                conn.recv(8192)
                body = b"OK"
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n"
                             b"Content-Type: text/plain\r\nConnection: close\r\n\r\n" + body)
            except OSError:
                pass

    def close(self):
        self.stop_flag.set()
        try:
            self.sock.close()
        except OSError:
            pass


def connect_through_proxy(target: str, payload: bytes, timeout: float = 6.0):
    """经本地代理建 CONNECT 隧道，返回 (响应行, 回显数据)。"""
    sock = socket.create_connection((gb.PROXY_HOST, gb.PROXY_PORT), timeout=timeout)
    with sock:
        sock.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(256)
            if not chunk:
                break
            head += chunk
        line = head.split(b"\r\n")[0].decode("utf-8", "replace")
        if b"200" not in head:
            return line, b""
        sock.sendall(payload)
        echo = b""
        sock.settimeout(timeout)
        while len(echo) < len(payload):
            chunk = sock.recv(4096)
            if not chunk:
                break
            echo += chunk
        return line, echo


# ================= 1) 域名判定 =================
print("1) 域名判定")
for host in ("github.com", "API.GitHub.com", "raw.githubusercontent.com", "gist.github.io"):
    check(f"识别 GitHub 域名 {host}", gb.is_github_host(host) is True)
for host in ("notgithub.com", "github.com.evil.com", "mygithub.com", "example.com", ""):
    check(f"不误伤 {host!r}", gb.is_github_host(host) is False)

# ================= 2) DoH 解析 =================
print("2) DoH 响应解析与容错")


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


doh_payload = {"Answer": [
    {"name": "github.com", "type": 5, "data": "github.com.cdn.cloudflare.net"},
    {"name": "github.com", "type": 1, "data": "20.205.243.166"},
    {"name": "github.com", "type": 1, "data": "140.82.113.3"},
    {"name": "github.com", "type": 1, "data": "20.205.243.166"},   # 重复
    {"name": "github.com", "type": 28, "data": "2606:50c0::1"},    # IPv6，应忽略
    {"name": "github.com", "type": 1, "data": "garbage"},          # 非法，应忽略
]}
_orig_get = gb.requests.get
calls = []


def fake_get(url, **kwargs):
    calls.append(url)
    if "bad" in url:
        raise gb.requests.ConnectionError("模拟 DoH 不可用")
    return FakeResponse(doh_payload)


gb.requests.get = fake_get
try:
    ips = gb._doh_query("https://ok.example/resolve", "github.com")
    check("只取 A 记录、去重、丢弃非法值",
          ips == ["20.205.243.166", "140.82.113.3"], str(ips))
    gb.DOH_PROVIDERS = (
        ("坏节点", "https://bad.example/resolve"),
        ("好节点", "https://ok.example/resolve"),
    )
    ips2 = gb.resolve_candidates("github.com")
    check("单个 DoH 提供方失败不影响整体", ips2 == ["20.205.243.166", "140.82.113.3"], str(ips2))
    gb.DOH_PROVIDERS = (("坏节点", "https://bad.example/resolve"),)
    check("全部 DoH 失败时返回空表而不是抛异常", gb.resolve_candidates("github.com") == [])
finally:
    gb.requests.get = _orig_get
    gb.DOH_PROVIDERS = (
        ("阿里 DNS", "https://dns.alidns.com/resolve"),
        ("腾讯 DNS", "https://doh.pub/dns-query"),
        ("360 DNS", "https://doh.360.cn/resolve"),
    )

# ================= 3) 优选排序 / 降级 =================
print("3) 优选排序与降级")
mixed = [
    {"ip": "a", "tcp": True, "tls": False, "ms": 0.0, "note": "证书校验失败"},
    {"ip": "b", "tcp": True, "tls": True, "ms": 320.0, "note": "HTTP 200"},
    {"ip": "c", "tcp": True, "tls": True, "ms": 120.0, "note": "HTTP 200"},
    {"ip": "d", "tcp": False, "tls": False, "ms": 0.0, "note": "TCP 不通"},
]
ranked = gb._rank(mixed)
check("证书通过的排前且按耗时升序",
      [d["ip"] for d in ranked] == ["c", "b", "a", "d"], str([d["ip"] for d in ranked]))

_orig_probe = gb.probe
_orig_resolve = gb.resolve_candidates
gb.resolve_candidates = lambda host, timeout=6.0: ["bad", "good"]
gb.probe = lambda host, ip, port=443, timeout=5.0: {
    "ip": ip, "tcp": True, "tls": ip != "bad", "ms": 100.0 if ip == "good" else 900.0,
    "note": "HTTP 200" if ip != "bad" else "证书校验失败"}
try:
    entry = gb.refresh_host("github.com", timeout=1)
    check("refresh_host 只保留证书验证通过的 IP", entry["ips"] == ["good"], str(entry["ips"]))
    gb.probe = lambda host, ip, port=443, timeout=5.0: {
        "ip": ip, "tcp": True, "tls": False, "ms": 10.0 if ip == "good" else 500.0,
        "note": "证书校验失败"}
    entry = gb.refresh_host("github.com", timeout=1)
    check("全部证书失败时降级为 TCP 可用 IP", entry["ips"] == ["good", "bad"], str(entry["ips"]))
    gb._state["ip_map"].clear()
    check("未测速时 ip_table 给出占位行",
          gb.ip_table()[0]["note"] == "尚未测速", gb.ip_table()[0]["note"])
finally:
    gb.probe = _orig_probe
    gb.resolve_candidates = _orig_resolve

# ================= 4) 隧道 =================
print("4) 本地隧道（GitHub 域名替换 IP / 非 GitHub 直连）")
echo = EchoServer()
gb.PROXY_PORT = free_port()
gb.GITHUB_HOSTS = gb.GITHUB_HOSTS + ("github.test",)
gb._state["ip_map"]["github.test"] = {
    "ips": ["127.0.0.1"],
    "detail": [{"ip": "127.0.0.1", "tcp": True, "tls": True, "ms": 1.0, "note": "HTTP 200"}],
    "updated": time.time(),
}
ok, msg = gb.start_proxy(refresh=False)
check("代理启动", ok, msg)
try:
    line, echoed = connect_through_proxy(f"github.test:{echo.port}", b"hello")
    check("GitHub 域名隧道字节原样转发（IP 已被换成优选 IP）",
          echoed == b"HELLO", f"{line} / {echoed!r}")
    stats = gb.github_stats()
    check("命中计数 +1", stats["hits"] == 1, str(stats["hits"]))
    check("last_connect 记录了优选 IP", "127.0.0.1" in stats["last_connect"], stats["last_connect"])

    line2, echoed2 = connect_through_proxy(f"127.0.0.1:{echo.port}", b"world")
    check("非 GitHub 域名直连透传", echoed2 == b"WORLD", f"{line2} / {echoed2!r}")
    stats2 = gb.github_stats()
    check("非 GitHub 直连不计入「回退」", stats2["fallbacks"] == 0, str(stats2["fallbacks"]))

    # 优选 IP 不可用时必须回退到「系统解析」。这里用映射函数模拟系统解析结果，
    # 断言就不依赖本机 DNS / hosts 的实际内容。
    _real_create = gb.socket.create_connection

    def _fake_create(address, *args, **kwargs):
        if address[0] == "github.test":
            return _real_create(("127.0.0.1", address[1]), *args, **kwargs)
        return _real_create(address, *args, **kwargs)

    gb._state["ip_map"]["github.test"] = {
        "ips": ["203.0.113.7"],  # TEST-NET-3，必然连不上
        "detail": [{"ip": "203.0.113.7", "tcp": False, "tls": False, "ms": 0.0, "note": "TCP 不通"}],
        "updated": time.time(),
    }
    _orig_timeout = gb.CONNECT_TIMEOUT
    gb.CONNECT_TIMEOUT = 1.5
    gb.socket.create_connection = _fake_create
    try:
        line3, echoed3 = connect_through_proxy(f"github.test:{echo.port}", b"hi", timeout=15)
        stats3 = gb.github_stats()
        check("优选 IP 不可用时回退系统解析并打通",
              echoed3 == b"HI" and stats3["fallbacks"] == 1,
              f"{line3} / {echoed3!r} / fallbacks={stats3['fallbacks']}")
        check("last_connect 标明走了回退",
              "系统解析" in stats3["last_connect"], stats3["last_connect"])
    finally:
        gb.socket.create_connection = _real_create
        gb.CONNECT_TIMEOUT = _orig_timeout

    http_srv = TinyHttpServer()
    try:
        conn = http.client.HTTPConnection(gb.PROXY_HOST, gb.PROXY_PORT, timeout=6)
        conn.request("GET", f"http://127.0.0.1:{http_srv.port}/demo")
        resp = conn.getresponse()
        check("明文 HTTP 正向代理转发正常", resp.status == 200 and resp.read() == b"OK",
              f"{resp.status}")
        conn.close()
    finally:
        http_srv.close()
finally:
    gb.stop_proxy()
    echo.close()
time.sleep(0.2)
try:
    socket.create_connection((gb.PROXY_HOST, gb.PROXY_PORT), timeout=1).close()
    check("停止后端口已释放", False, f"{gb.PROXY_PORT} 仍可连接")
except OSError:
    check("停止后端口已释放", True)

# ================= 5) git 代理配置 =================
print("5) git 代理配置（隔离到临时 GIT_CONFIG_GLOBAL）")
tmp_gitconfig = tmp / "gitconfig-global"
tmp_gitconfig.write_text(
    "[user]\n\tname = npm-boost-test-user\n"
    "[http \"https://github.com\"]\n\tproxy = http://127.0.0.1:9999\n",
    encoding="utf-8")
tmp_backup = tmp / "git-proxy-backup.json"
os.environ["GIT_CONFIG_GLOBAL"] = str(tmp_gitconfig)
gb.GIT_PROXY_BACKUP = tmp_backup
real_gitconfig = Path.home() / ".gitconfig"
real_before = real_gitconfig.stat().st_mtime_ns if real_gitconfig.exists() else None

code, out, _ = gb._git(["--list", "--show-origin"])
# git 打印路径时会把反斜杠转义并加引号，所以用临时文件里的独有内容判断是否真读到了它
honored = "npm-boost-test-user" in out
if not honored:
    skip("git 配置读写", "当前 git 不支持 GIT_CONFIG_GLOBAL，跳过以免动到真实配置")
else:
    check("GIT_CONFIG_GLOBAL 生效（隔离安全）", honored, "已读到临时文件里的 user.name")
    status = gb.git_proxy_status()
    check("识别到用户原有的 GitHub 代理设置",
          status["configured"] == "http://127.0.0.1:9999" and not status["points_to_us"],
          str(status["configured"]))

    ok, msg = gb.apply_git_proxy()
    check("apply_git_proxy 成功", ok, msg)
    status = gb.git_proxy_status()
    check("设置后指向本地加速代理", status["points_to_us"] is True, str(status["configured"]))
    check("备份文件已生成且记录了原值",
          tmp_backup.is_file() and json.loads(tmp_backup.read_text(encoding="utf-8"))
          [gb.GIT_PROXY_KEYS[0]] == "http://127.0.0.1:9999")
    check("gist 也一并配置",
          gb.git_proxy_status()["values"][gb.GIT_PROXY_KEYS[1]] == gb.proxy_url())

    first_backup = tmp_backup.read_text(encoding="utf-8")
    gb.apply_git_proxy()
    check("重复设置不会覆盖最初的备份",
          tmp_backup.read_text(encoding="utf-8") == first_backup)

    ok, msg = gb.restore_git_proxy()
    check("restore_git_proxy 成功", ok, msg)
    status = gb.git_proxy_status()
    check("还原回用户原本的值（而不是直接删掉）",
          status["configured"] == "http://127.0.0.1:9999", str(status["configured"]))
    check("备份文件已清理", not tmp_backup.is_file())
    check("还原后 gist 键已清除",
          gb.git_proxy_status()["values"][gb.GIT_PROXY_KEYS[1]] is None)
    real_after = real_gitconfig.stat().st_mtime_ns if real_gitconfig.exists() else None
    check("测试全程未触碰真实 ~/.gitconfig", real_before == real_after,
          f"{real_gitconfig}")

print("6) 优选 IP 失败标记与轮换（上游零响应的自愈）")
gb._state["ip_map"]["github.test"] = {
    "ips": ["10.0.0.1", "10.0.0.2"],
    "detail": [],
    "updated": time.time(),
}
gb._state["ip_failures"].clear()
check("初始按优选顺序返回",
      gb._candidate_ips("github.test") == ["10.0.0.1", "10.0.0.2"],
      str(gb._candidate_ips("github.test")))
gb._judge_tunnel("github.test", "10.0.0.1", 0)        # 上游一个字节都没回
check("失败过的 IP 被排到最后",
      gb._candidate_ips("github.test") == ["10.0.0.2", "10.0.0.1"],
      str(gb._candidate_ips("github.test")))
gb._judge_tunnel("github.test", "10.0.0.1", 0)        # 再失败一次 → 累计 2 次
check("连续失败两次的 IP 暂时不用",
      gb._candidate_ips("github.test") == ["10.0.0.2"],
      str(gb._candidate_ips("github.test")))
gb._judge_tunnel("github.test", "10.0.0.2", 0)
gb._judge_tunnel("github.test", "10.0.0.2", 0)        # 两个候选都累计 2 次
check("失败记录按 IP 累计",
      gb._state["ip_failures"]["github.test"] == {"10.0.0.1": 2, "10.0.0.2": 2},
      str(gb._state["ip_failures"]["github.test"]))
check("所有候选都失败过时仍返回全部（不至于彻底不可用）",
      sorted(gb._candidate_ips("github.test")) == ["10.0.0.1", "10.0.0.2"],
      str(gb._candidate_ips("github.test")))
gb._judge_tunnel("github.test", "10.0.0.1", 1234)     # 10.0.0.1 有正常回包
check("恢复的 IP 立刻回到候选，仍失败两次的被排除",
      gb._candidate_ips("github.test") == ["10.0.0.1"],
      str(gb._candidate_ips("github.test")))
gb._judge_tunnel("github.test", None, 0)
check("没用优选 IP（直连/回退）时不产生标记",
      gb._state["ip_failures"]["github.test"] == {"10.0.0.2": 2},
      str(gb._state["ip_failures"].get("github.test")))
gb._state["ip_failures"].clear()
gb._state["ip_map"].pop("github.test", None)

print()
print()
if skips:
    print(f"跳过 {len(skips)} 项：{skips}")
print("结果:", "全部通过" if not failures else f"失败 {len(failures)} 项 {failures}")
sys.exit(1 if failures else 0)
