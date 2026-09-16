# -*- coding: utf-8 -*-
"""自动探测本机正在运行的代理/VPN。

用户电脑上开着什么客户端、监听哪个端口，不该让用户自己去查。这里做三件事：

1. 读**系统代理设置**（Windows 注册表 / macOS scutil），这是最可信的来源
2. 扫一遍**常见代理客户端的默认端口**，判断每个口是 HTTP 还是 SOCKS5
3. 逐个**真正发一次请求**验证能不能用，可选再验证能不能访问 TikTok

另外还检测 **TUN 全局模式**：这种情况下没有本地端口，流量在网络层就被接走了，
录制器不需要设代理。
"""

from __future__ import annotations

import os
import platform
import re
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field

import httpx

# 常见代理客户端的监听端口 -> 说明
KNOWN_PORTS: dict[int, str] = {
    7897: "Clash Verge / mihomo 混合端口",
    7890: "Clash / ClashX 混合端口",
    7891: "Clash SOCKS",
    10809: "v2rayN HTTP",
    10808: "v2rayN SOCKS",
    10887: "WWSS privoxy HTTP",
    10886: "WWSS SOCKS",
    1087: "V2rayU / ShadowsocksX-NG HTTP",
    1086: "ShadowsocksX-NG SOCKS",
    8118: "privoxy",
    6152: "Surge / Quantumult X HTTP",
    6153: "Surge SOCKS",
    8888: "Charles / Fiddler",
    8080: "通用 HTTP 代理",
    1080: "Shadowsocks SOCKS",
    2080: "sing-box",
    20171: "Netch SOCKS",
    20172: "Netch HTTP",
    9910: "通用 HTTP 代理",
    8889: "通用 HTTP 代理",
}

LIVENESS_URL = "http://www.gstatic.com/generate_204"   # 用 http 免掉证书干扰
TIKTOK_URL = "https://www.tiktok.com/"
TIKTOK_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

TIKTOK_OK = "ok"                 # 拿到真页面
TIKTOK_CHALLENGE = "challenge"   # 被 WAF 挑战页拦住
TIKTOK_FAIL = "fail"             # 根本连不上


@dataclass
class Candidate:
    address: str
    port: int
    scheme: str                       # http / socks5
    client: str = ""
    source: str = "scan"              # system / env / scan
    alive: bool = False
    latency_ms: int | None = None
    status: int | None = None
    error: str = ""
    tiktok: str = ""                  # TIKTOK_OK / CHALLENGE / FAIL
    tiktok_note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- #
# 基础探测
# --------------------------------------------------------------------------- #

def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, port)) == 0
    except OSError:
        return False


def _is_socks5(port: int, host: str = "127.0.0.1", timeout: float = 0.6) -> bool:
    """发一个 SOCKS5 握手，看对方答不答应。"""

    def attempt(methods: bytes) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect((host, port))
                sock.sendall(methods)
                reply = sock.recv(2)
                return len(reply) == 2 and reply[0] == 0x05
        except OSError:
            return False

    # 先试「无需认证」，再试「账号密码」，两者任一被接受都说明是 SOCKS5
    return attempt(b"\x05\x01\x00") or attempt(b"\x05\x02\x00\x02")


def _probe(address: str, url: str = LIVENESS_URL, timeout: float = 4.0) -> tuple[bool, int | None, str]:
    try:
        with httpx.Client(proxy=address, timeout=timeout, follow_redirects=True) as client:
            response = client.get(url, headers={"user-agent": TIKTOK_UA})
            return True, response.status_code, ""
    except Exception as err:
        return False, None, f"{type(err).__name__}: {str(err)[:120]}"


# --------------------------------------------------------------------------- #
# 系统代理 / 环境变量 / TUN
# --------------------------------------------------------------------------- #

def system_proxy_address() -> str:
    system = platform.system()
    if system == "Windows":
        return _system_proxy_windows()
    if system == "Darwin":
        return _system_proxy_macos()
    return ""


def _system_proxy_windows() -> str:
    try:
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            enabled = winreg.QueryValueEx(key, "ProxyEnable")[0]
            if not enabled:
                return ""
            raw = str(winreg.QueryValueEx(key, "ProxyServer")[0])
    except Exception:
        return ""
    # 可能是 "127.0.0.1:7890"，也可能带协议前缀 "http=...;https=..."
    if "=" in raw:
        for part in raw.split(";"):
            if part.lower().startswith(("http=", "https=")):
                raw = part.split("=", 1)[1]
                break
    return _normalize(raw)


def _system_proxy_macos() -> str:
    try:
        out = subprocess.run(["scutil", "--proxy"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""
    for host_key, port_key in (("HTTPProxy", "HTTPPort"), ("HTTPSProxy", "HTTPSPort")):
        host = re.search(rf"{host_key}\s*:\s*(\S+)", out)
        port = re.search(rf"{port_key}\s*:\s*(\d+)", out)
        if host and port:
            return _normalize(f"{host.group(1)}:{port.group(1)}")
    return ""


def env_proxy_address() -> str:
    for name in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        value = os.environ.get(name)
        if value:
            normalized = _normalize(value)
            if normalized:
                return normalized
    return ""


def _normalize(raw: str) -> str:
    """统一成 ``http://host:port``（已带协议的原样返回）。"""
    raw = (raw or "").strip().rstrip("/")
    if not raw:
        return ""
    if re.match(r"^(http|https|socks4|socks5|socks5h)://", raw, re.I):
        return raw
    if ":" not in raw:
        return ""
    return f"http://{raw}"


def tun_interface() -> str:
    """检测有没有 TUN 接口（全局模式 VPN）。"""
    try:
        if platform.system() == "Windows":
            out = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=6).stdout
            for block in out.split("\n\n"):
                head = block.strip().splitlines()[0] if block.strip() else ""
                if re.search(r"TUN|Wintun|WireGuard|TAP", head, re.I) and "IPv4" in block:
                    return head.strip()[:40]
            return ""
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=6).stdout
        current = ""
        for line in out.splitlines():
            if line and not line[0].isspace():
                current = line.split(":")[0]
            elif current and re.match(r"\s+inet\s+\d", line) and re.match(r"(utun|tun|wg|tap)", current):
                return current
    except Exception:
        pass
    return ""


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #

def address_status(address: str) -> dict:
    """便宜的本地检查：只看端口在不在监听，不发网络请求。"""
    if not address:
        return {"configured": False, "listening": False, "host": "", "port": 0}
    match = re.match(r"^(\w+)://([^:/]+):(\d+)", address)
    if not match:
        return {"configured": True, "listening": False, "host": "", "port": 0, "error": "地址格式不对"}
    host, port = match.group(2), int(match.group(3))
    if host not in ("127.0.0.1", "localhost", "::1"):
        # 远端代理没法用本地端口判断，只能算"未验证"
        return {"configured": True, "listening": None, "host": host, "port": port}
    return {"configured": True, "listening": _port_open(port), "host": host, "port": port}


def test(address: str, timeout: float = 6.0) -> dict:
    """测一个代理地址通不通，返回延迟。"""
    start = time.perf_counter()
    ok, status_code, error = _probe(address, LIVENESS_URL, timeout)
    return {
        "ok": ok,
        "status": status_code,
        "latency_ms": int((time.perf_counter() - start) * 1000) if ok else None,
        "error": error,
    }


def check_tiktok(address: str, timeout: float = 15.0) -> dict:
    """这个代理能不能真正访问 TikTok（是拿到真页面还是被 WAF 拦）。"""
    ok, status_code, error = _probe(address, TIKTOK_URL, timeout)
    if not ok:
        return {"result": TIKTOK_FAIL, "note": error, "status": None, "bytes": 0}
    try:
        with httpx.Client(proxy=address, timeout=timeout, follow_redirects=True) as client:
            response = client.get(TIKTOK_URL, headers={"user-agent": TIKTOK_UA})
            body = response.text
    except Exception as err:
        return {"result": TIKTOK_FAIL, "note": str(err)[:120], "status": status_code, "bytes": 0}

    size = len(body)
    if "_wafchallengeid" in body or "slardar" in body.lower():
        return {"result": TIKTOK_CHALLENGE, "note": "被 TikTok 风控挑战页拦住", "status": status_code, "bytes": size}
    if "__UNIVERSAL_DATA_FOR_REHYDRATION__" in body or "tiktok-live" in body:
        return {"result": TIKTOK_OK, "note": "可以正常访问 TikTok", "status": status_code, "bytes": size}
    return {"result": TIKTOK_FAIL, "note": f"返回内容异常（{size} 字节）", "status": status_code, "bytes": size}


def _probe_port(port: int, host: str = "127.0.0.1", timeout: float = 5.0) -> Candidate | None:
    """端口开着的话，判断它是 HTTP 还是 SOCKS5，并实测一次。

    HTTP 和 SOCKS5 没法从端口号本身看出来，所以按经验顺序试；正常情况
    第一次就命中，只有失败时才会多试一种。
    """
    client = KNOWN_PORTS.get(port, "未知代理")
    order = ["socks5", "http"] if "SOCKS" in client.upper() else ["http", "socks5"]
    fallback: Candidate | None = None
    for scheme in order:
        if scheme == "socks5" and not _is_socks5(port, host):
            continue
        candidate = _measure(
            Candidate(address=f"{scheme}://{host}:{port}", port=port, scheme=scheme, client=client),
            timeout,
        )
        if candidate.alive:
            return candidate
        fallback = fallback or candidate
    return fallback


def _measure(candidate: Candidate, timeout: float = 5.0) -> Candidate:
    start = time.perf_counter()
    ok, status_code, error = _probe(candidate.address, LIVENESS_URL, timeout)
    candidate.alive = ok
    candidate.status = status_code
    candidate.error = error
    candidate.latency_ms = int((time.perf_counter() - start) * 1000) if ok else None
    return candidate


def detect(check_tiktok_too: bool = False, timeout: float = 5.0, max_workers: int = 8) -> dict:
    """扫一遍本机，返回所有候选代理。"""
    found: dict[str, Candidate] = {}

    # 1) 系统代理 / 环境变量 —— 优先，可信度最高
    for address, source in ((system_proxy_address(), "system"), (env_proxy_address(), "env")):
        if not address:
            continue
        port_match = re.search(r":(\d+)$", address)
        port = int(port_match.group(1)) if port_match else 0
        scheme = "socks5" if address.lower().startswith("socks") else "http"
        found[address] = Candidate(address=address, port=port, scheme=scheme,
                                   client="系统代理设置" if source == "system" else "环境变量",
                                   source=source)

    # 2) 扫常见端口（_probe_port 里已经实测过，不用再测一遍）
    open_ports = [port for port in KNOWN_PORTS if _port_open(port)]
    if open_ports:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for candidate in pool.map(lambda port: _probe_port(port, timeout=timeout), open_ports):
                if candidate:
                    found.setdefault(candidate.address, candidate)

    # 3) 系统/环境变量来的还没实测过，补一次
    pending = [c for c in found.values() if c.source in ("system", "env") and c.latency_ms is None]
    if pending:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            list(pool.map(lambda c: _measure(c, timeout), pending))
    candidates = list(found.values())

    alive = [c for c in candidates if c.alive]
    dead = [c for c in candidates if not c.alive]
    alive.sort(key=lambda c: (c.latency_ms or 99999))

    # 4) 可选：验证 TikTok 可达性（只测能用的）
    best = ""
    if check_tiktok_too:
        with ThreadPoolExecutor(max_workers=min(4, max(1, len(alive)))) as pool:
            for candidate, verdict in zip(alive, pool.map(lambda c: check_tiktok(c.address), alive)):
                candidate.tiktok = verdict["result"]
                candidate.tiktok_note = verdict["note"]
        good = [c for c in alive if c.tiktok == TIKTOK_OK]
        best = (good or alive[0]).address if (good or alive) else ""

    return {
        "candidates": [c.to_dict() for c in alive + dead],
        "working": len(alive),
        "best": best,
        "system_proxy": system_proxy_address(),
        "env_proxy": env_proxy_address(),
        "tun": tun_interface(),
        "checked_tiktok": check_tiktok_too,
    }


def pick_best(result: dict) -> str:
    """挑一个最合适的：优先能访问 TikTok 的，其次延迟最低的。"""
    candidates = result.get("candidates") or []
    for candidate in candidates:
        if candidate.get("tiktok") == TIKTOK_OK:
            return candidate["address"]
    for candidate in candidates:
        if candidate.get("alive"):
            return candidate["address"]
    return ""
