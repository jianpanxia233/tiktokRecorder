# -*- coding: utf-8 -*-
"""离线卡密校验模块。

卡密 = 7 字节明文载荷 + 8 字节 HMAC-SHA256 截断，Base32 编码成 24 个字符，
显示成 ``DLR-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX``。

载荷布局
--------
===========  ====  ==========================================
ver          1B    格式版本
key_id       2B    卡号，方便发卡方对账
expire_day   2B    过期日（自 2025-01-01 的天数），0 表示永久
machine_tag  2B    机器码标记，0 表示不绑机
===========  ====  ==========================================

安全模型（请如实理解，不要高估）
--------------------------------
* **对称校验**：程序里必须带着和发卡端一样的密钥（``app/secret.py``），
  所以**有能力逆向 exe 的人可以把密钥挖出来，进而伪造任意卡密**。
  代码里对密钥做了一层异或混淆，能挡住 ``strings``/grep 这类土办法，
  挡不住真正会逆向的人。
* **能防住的**：瞎猜卡密（64 位 MAC 的搜索空间）、改一两位蒙混过关、
  把一张卡拿去多台机器共用（绑机器码 + 本地加密存储）。
* **防不住的**：直接改程序把校验函数改成恒真、挖出密钥批量伪造。
* 结论：离线卡密是"防君子不防小人"。要真正防伪造和防破解，只能上在线验证。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import platform
import re
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from Crypto.Cipher import AES

try:  # 由 tools/gen_secret.py 生成；没生成时也要能 import
    from .secret import SECRET_BLOB_HEX, SECRET_MASK_HEX
except Exception:  # pragma: no cover - 首次部署时还没生成密钥
    SECRET_BLOB_HEX = ""
    SECRET_MASK_HEX = ""

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

KEY_PREFIX = "DLR"
KEY_EPOCH = date(2025, 1, 1)      # 载荷里日期的起点
KEY_VERSION = 1

PAYLOAD_LEN = 7                   # ver(1) + key_id(2) + expire_day(2) + machine_tag(2)
SIG_LEN = 8                       # HMAC-SHA256 截断长度
KEY_BYTES = PAYLOAD_LEN + SIG_LEN # 15
KEY_CHARS = 24                    # 15 字节 Base32 编码后的字符数
MACHINE_TAG_LEN = 2

_PAYLOAD_FMT = ">BHHH"
_GROUP = 4
_CLOCK_TOLERANCE = 24 * 3600      # 允许的系统时间回拨秒数
_TOUCH_INTERVAL = 3600            # last_seen 最快写入频率，避免频繁写盘

# 用户容易打错的字符 -> base32 合法字符
_TYPO_MAP = str.maketrans({"0": "O", "1": "I"})


class Status(str, Enum):
    OK = "ok"
    NOT_ACTIVATED = "not_activated"
    NOT_CONFIGURED = "not_configured"
    FORMAT_ERROR = "format_error"
    BAD_SIGNATURE = "bad_signature"
    EXPIRED = "expired"
    MACHINE_MISMATCH = "machine_mismatch"
    CLOCK_TAMPERED = "clock_tampered"
    STORAGE_ERROR = "storage_error"


MESSAGES: dict[Status, str] = {
    Status.OK: "卡密有效",
    Status.NOT_ACTIVATED: "尚未激活，请输入卡密",
    Status.NOT_CONFIGURED: "发卡密钥未配置，请先运行 tools/gen_secret.py",
    Status.FORMAT_ERROR: "卡密格式不正确，请检查是否输错或漏字符",
    Status.BAD_SIGNATURE: "卡密无效，请确认是否复制完整",
    Status.EXPIRED: "卡密已过期",
    Status.MACHINE_MISMATCH: "此卡密已绑定其他机器",
    Status.CLOCK_TAMPERED: "系统时间异常，请校准后重试",
    Status.STORAGE_ERROR: "授权信息保存失败",   # 真实原因会拼在括号里
}


# --------------------------------------------------------------------------- #
# 发卡密钥
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=1)
def _secret() -> bytes | None:
    """把混淆过的密钥还原回来。失败返回 None（未配置）。"""
    if not SECRET_BLOB_HEX or not SECRET_MASK_HEX:
        return None
    try:
        blob = bytes.fromhex(SECRET_BLOB_HEX)
        mask = bytes.fromhex(SECRET_MASK_HEX)
    except ValueError:
        return None
    if not blob or len(blob) != len(mask):
        return None
    return bytes(a ^ b for a, b in zip(blob, mask))


def secret_configured() -> bool:
    return _secret() is not None


def compute_mac(payload: bytes, secret: bytes) -> bytes:
    return hmac.new(secret, payload, hashlib.sha256).digest()[:SIG_LEN]


# --------------------------------------------------------------------------- #
# 机器码
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=1)
def machine_code() -> str:
    """返回 ``XXXX-XXXX-XXXX-XXXX`` 形式的机器码。

    只取跨重装系统/重命名电脑都不变的硬件标识，避免因为用户改个电脑名字
    就把自己的卡锁死。
    """
    parts = [p for p in _raw_machine_ids() if p]
    if not parts:
        parts = ["fallback:" + platform.node()]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).digest()
    return _grouped(base64.b32encode(digest[:10]).decode().rstrip("="))


def _raw_machine_ids() -> list[str]:
    system = platform.system()
    ids: list[str] = []
    if system == "Windows":
        ids.append("guid:" + _windows_machine_guid())
        ids.append("vol:" + _windows_volume_serial())
    elif system == "Darwin":
        ids.append("uuid:" + _macos_platform_uuid())
    else:
        ids.append("mid:" + _linux_machine_id())
    ids.append("mac:" + _mac_address())
    return ids


def _windows_machine_guid() -> str:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
            return str(winreg.QueryValueEx(key, "MachineGuid")[0])
    except Exception:
        return ""


def _windows_volume_serial() -> str:
    try:
        import ctypes
        from ctypes import wintypes

        serial = wintypes.DWORD(0)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            "C:\\", None, 0, ctypes.byref(serial), None, None, None, 0
        )
        return f"{serial.value:08X}" if ok else ""
    except Exception:
        return ""


def _macos_platform_uuid() -> str:
    try:
        out = subprocess.run(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out)
        return match.group(1) if match else ""
    except Exception:
        return ""


def _linux_machine_id() -> str:
    try:
        return Path("/etc/machine-id").read_text().strip()
    except Exception:
        return ""


def _mac_address() -> str:
    return f"{_uuid_getnode():012X}"


def _uuid_getnode() -> int:
    """``uuid.getnode()`` 在拿不到真实网卡时会返回随机值（并置多播位）。"""
    import uuid

    node = uuid.getnode()
    if (node >> 40) & 0x01:  # 多播位 = 随机生成，不稳定，丢弃
        return 0
    return node


def machine_tag(code: str | None = None) -> bytes:
    """从机器码派生 2 字节标记，发卡端和客户端用同一套算法。"""
    normalized = (code or machine_code()).replace("-", "").upper()
    return hashlib.sha256(("dlr-tag|" + normalized).encode("utf-8")).digest()[:MACHINE_TAG_LEN]


# --------------------------------------------------------------------------- #
# 编解码
# --------------------------------------------------------------------------- #

def _grouped(text: str) -> str:
    return "-".join(text[i:i + _GROUP] for i in range(0, len(text), _GROUP))


def encode_b32(data: bytes) -> str:
    return base64.b32encode(data).decode().rstrip("=")


def decode_b32(text: str) -> bytes:
    padded = text + "=" * (-len(text) % 8)
    return base64.b32decode(padded)


def normalize_key(text: str) -> str:
    """去掉分隔符和大写化，并容忍 ``0``/``1`` 这类常见手误。"""
    cleaned = re.sub(r"[^0-9A-Za-z]", "", (text or "").upper()).translate(_TYPO_MAP)
    if len(cleaned) == KEY_CHARS + len(KEY_PREFIX) and cleaned.startswith(KEY_PREFIX):
        cleaned = cleaned[len(KEY_PREFIX):]
    return cleaned


def format_key(body: str) -> str:
    return f"{KEY_PREFIX}-{_grouped(body)}"


def build_payload(key_id: int, expire_day: int, machine_tag_bytes: bytes = b"\x00\x00") -> bytes:
    return struct.pack(_PAYLOAD_FMT, KEY_VERSION, key_id, expire_day, int.from_bytes(machine_tag_bytes, "big"))


def encode_key(payload: bytes, mac: bytes) -> str:
    return format_key(encode_b32(payload + mac))


def parse_payload(payload: bytes) -> dict[str, Any]:
    version, key_id, expire_day, tag = struct.unpack(_PAYLOAD_FMT, payload)
    expire_date = (KEY_EPOCH + timedelta(days=expire_day)).isoformat() if expire_day else None
    return {
        "version": version,
        "key_id": key_id,
        "expire_day": expire_day,
        "expire_date": expire_date,
        "permanent": expire_day == 0,
        "machine_tag": tag.to_bytes(MACHINE_TAG_LEN, "big"),
        "bound": tag != 0,
    }


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #

@dataclass
class LicenseInfo:
    status: Status
    key_id: int | None = None
    expire_date: str | None = None
    permanent: bool = False
    days_left: int | None = None
    bound: bool = False
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.status is Status.OK


def verify_key(text: str, code: str | None = None, *, today: date | None = None) -> LicenseInfo:
    """纯函数：只校验卡密本身，不碰磁盘。"""
    secret = _secret()
    if secret is None:
        return LicenseInfo(Status.NOT_CONFIGURED, message=MESSAGES[Status.NOT_CONFIGURED])

    body = normalize_key(text)
    if len(body) != KEY_CHARS:
        return LicenseInfo(Status.FORMAT_ERROR, message=MESSAGES[Status.FORMAT_ERROR])
    try:
        raw = decode_b32(body)
    except Exception:
        return LicenseInfo(Status.FORMAT_ERROR, message=MESSAGES[Status.FORMAT_ERROR])
    if len(raw) != KEY_BYTES:
        return LicenseInfo(Status.FORMAT_ERROR, message=MESSAGES[Status.FORMAT_ERROR])

    payload, mac = raw[:PAYLOAD_LEN], raw[PAYLOAD_LEN:]
    if not hmac.compare_digest(compute_mac(payload, secret), mac):
        return LicenseInfo(Status.BAD_SIGNATURE, message=MESSAGES[Status.BAD_SIGNATURE])

    info = parse_payload(payload)
    if info["version"] != KEY_VERSION:
        return LicenseInfo(Status.FORMAT_ERROR, message=MESSAGES[Status.FORMAT_ERROR])

    if info["bound"] and info["machine_tag"] != machine_tag(code):
        return LicenseInfo(Status.MACHINE_MISMATCH, message=MESSAGES[Status.MACHINE_MISMATCH])

    days_left: int | None = None
    if not info["permanent"]:
        current = (today or date.today()) - KEY_EPOCH
        days_left = info["expire_day"] - current.days
        if days_left < 0:
            return LicenseInfo(
                Status.EXPIRED, key_id=info["key_id"], expire_date=info["expire_date"],
                bound=info["bound"], message=f"{MESSAGES[Status.EXPIRED]}（{info['expire_date']}）",
            )

    return LicenseInfo(
        Status.OK,
        key_id=info["key_id"],
        expire_date=info["expire_date"],
        permanent=info["permanent"],
        days_left=days_left,
        bound=info["bound"],
        message=MESSAGES[Status.OK],
    )


# --------------------------------------------------------------------------- #
# 本地存储
# --------------------------------------------------------------------------- #

def _storage_candidates() -> list[Path]:
    """按优先级列出可以放授权文件的目录。

    首选系统标准位置；万不得已再退到程序目录旁边 —— 有些机器上
    ``%APPDATA%`` 会因为企业策略或杀毒软件拦截而写不进去，
    退到绿色版目录至少还能用。
    """
    candidates: list[Path] = []
    system = platform.system()
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(Path(appdata) / "DouyinLiveRecorder")
        candidates.append(Path.home() / "AppData" / "Roaming" / "DouyinLiveRecorder")
    elif system == "Darwin":
        candidates.append(Path.home() / "Library" / "Application Support" / "DouyinLiveRecorder")
    else:
        candidates.append(
            Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "DouyinLiveRecorder"
        )
    try:
        app_dir = (
            Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[1]
        )
        candidates.append(app_dir / "license")
    except Exception:
        pass
    return candidates


_storage_dir: Path | None = None


def _write_probe(directory: Path) -> None:
    """真写一个文件试试 —— 只看权限位在 Windows 上并不可靠。"""
    probe = directory / ".write-test"
    probe.write_bytes(b"ok")
    probe.unlink()


def storage_dir() -> Path:
    """返回一个确实可写的目录；全都不行就抛出带原因的 OSError。"""
    global _storage_dir
    if _storage_dir is not None:
        return _storage_dir

    problems: list[str] = []
    for candidate in _storage_candidates():
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            _write_probe(candidate)
        except Exception as err:
            problems.append(f"{candidate} ({type(err).__name__}: {err})")
            continue
        _storage_dir = candidate
        return candidate

    raise OSError("没有可写的目录，试过：" + "；".join(problems))


def storage_file() -> Path:
    return storage_dir() / "license.dat"


def storage_info() -> dict:
    """给界面和售后看的：授权文件存在哪、到底能不能写。"""
    candidates = [str(path) for path in _storage_candidates()]
    try:
        directory = storage_dir()
        return {"ok": True, "dir": str(directory), "file": str(directory / "license.dat"),
                "candidates": candidates}
    except Exception as err:
        return {"ok": False, "dir": "", "file": "", "error": str(err), "candidates": candidates}


def _stream_key(code: str) -> bytes:
    return hashlib.sha256(("dlr-license-v1|" + code.replace("-", "").upper()).encode("utf-8")).digest()


def _write_record(record: dict[str, Any], code: str) -> None:
    cipher = AES.new(_stream_key(code), AES.MODE_GCM)
    ciphertext, tag = cipher.encrypt_and_digest(json.dumps(record).encode("utf-8"))
    storage_file().write_bytes(cipher.nonce + tag + ciphertext)


def load_record(code: str | None = None) -> dict[str, Any] | None:
    try:
        path = storage_file()
        if not path.exists():
            return None
        blob = path.read_bytes()
        nonce, tag, ciphertext = blob[:16], blob[16:32], blob[32:]
        cipher = AES.new(_stream_key(code or machine_code()), AES.MODE_GCM, nonce=nonce)
        return json.loads(cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8"))
    except Exception:
        return None


def activate(text: str, code: str | None = None) -> LicenseInfo:
    """校验通过后把卡密加密落盘。"""
    code = code or machine_code()
    info = verify_key(text, code)
    if not info.ok:
        return info
    now = int(time.time())
    try:
        _write_record(
            {"key": normalize_key(text), "code": code, "activated_at": now, "last_seen": now},
            code,
        )
    except Exception as err:
        # 这里以前是 `except Exception:` 把原因吞了，用户只看到一句"读取失败"，
        # 售后完全没法查。现在把真实原因带在括号里。
        detail = f"{type(err).__name__}: {err}"
        return LicenseInfo(Status.STORAGE_ERROR, message=f"{MESSAGES[Status.STORAGE_ERROR]}（{detail[:220]}）")
    return info


def clear_activation() -> None:
    try:
        storage_file().unlink(missing_ok=True)
    except Exception:
        pass


def current_status(*, touch: bool = True, code: str | None = None) -> LicenseInfo:
    """读取本地授权并校验，同时做系统时间回拨检测。"""
    code = code or machine_code()
    record = load_record(code)
    if not record:
        return LicenseInfo(Status.NOT_ACTIVATED, message=MESSAGES[Status.NOT_ACTIVATED])

    info = verify_key(record.get("key", ""), record.get("code") or code)
    if not info.ok:
        return info

    now = int(time.time())
    last_seen = int(record.get("last_seen") or 0)
    if now + _CLOCK_TOLERANCE < last_seen:
        return LicenseInfo(
            Status.CLOCK_TAMPERED, key_id=info.key_id, expire_date=info.expire_date,
            permanent=info.permanent, days_left=info.days_left, bound=info.bound,
            message=MESSAGES[Status.CLOCK_TAMPERED],
        )

    if touch and now - last_seen > _TOUCH_INTERVAL:
        record["last_seen"] = now
        try:
            _write_record(record, code)
        except Exception:
            pass  # 写盘失败不影响本次使用
    return info


def require_license() -> LicenseInfo:
    """给 GUI 用：拿不到有效授权就直接返回失败状态，由调用方决定怎么拦。"""
    return current_status()


def describe(info: LicenseInfo) -> str:
    if info.status is not Status.OK:
        return info.message or MESSAGES.get(info.status, "未知状态")
    if info.permanent:
        return "永久授权"
    return f"有效期至 {info.expire_date}（剩余 {info.days_left} 天）"
