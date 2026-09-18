# -*- coding: utf-8 -*-
"""暴露给网页界面的接口（pywebview 的 js_api）。

前端通过 ``pywebview.api.方法名(...)`` 调用，全部返回可 JSON 序列化的结构。
所有涉及配置文件的写入都走「按行替换」，保留原文件里的注释和排版，
不把用户的 config.ini 重排。
"""

from __future__ import annotations

import configparser
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import detect
from . import license as lic
from .recorder import RecorderProcess

APP_NAME = "DouyinLiveRecorder 桌面版"
APP_VERSION = "4.0.10"
CONFIG_SECTION = "录制设置"
UPSTREAM_URL = "https://github.com/ihmily/DouyinLiveRecorder"

# 「设置」页暴露出来的配置项。key 必须和 config.ini 里的完全一致。
CONFIG_SCHEMA: list[dict[str, Any]] = [
    {"key": "直播保存路径(不填则默认)", "label": "录制保存路径", "type": "folder",
     "hint": "留空则存到程序目录下的 downloads 文件夹"},
    {"key": "视频保存格式ts|mkv|flv|mp4|mp3音频|m4a音频", "label": "保存格式", "type": "select",
     "default": "ts", "options": ["ts", "mkv", "flv", "mp4", "mp3音频", "m4a音频"],
     "hint": "建议保持 ts：录制途中就算进程被杀、断电，已经写入的部分也不会损坏；"
             "录完再由下面的「录完自动转 mp4」转成 mp4（注意：只有 ts 会触发自动转换，mkv 不会）"},
    {"key": "原画|超清|高清|标清|流畅", "label": "录制画质", "type": "select",
     "options": ["原画", "超清", "高清", "标清", "流畅"]},
    {"key": "循环时间(秒)", "label": "循环检测间隔（秒）", "type": "number",
     "hint": "每隔多久检查一次主播是否开播"},
    {"key": "同一时间访问网络的线程数", "label": "同时访问线程数", "type": "number"},
    {"key": "是否使用代理ip(是/否)", "label": "启用代理", "type": "select", "options": ["是", "否"]},
    {"key": "代理地址", "label": "代理地址", "type": "text",
     "hint": "例如 http://127.0.0.1:7897；必须是 http:// 开头，不支持 socks5://"},
    {"key": "是否跳过代理检测(是/否)", "label": "跳过代理检测", "type": "select", "options": ["是", "否"]},
    {"key": "使用代理录制的平台(逗号分隔)", "label": "走代理的平台", "type": "text",
     "hint": "例如 tiktok, twitch, youtu"},
    {"key": "分段录制是否开启", "label": "分段录制", "type": "select", "default": "否",
     "options": ["否", "是"], "hint": "开启后按下面的时长把长直播切成多个文件"},
    {"key": "视频分段时间(秒)", "label": "分段时长（秒）", "type": "number"},
    {"key": "录制完成后自动转为mp4格式", "label": "录完自动转 mp4", "type": "select",
     "default": "是", "options": ["是", "否"],
     "hint": "配合上面的 ts 使用：录制结束后自动转成 mp4"},
    {"key": "追加格式后删除原文件", "label": "转换后删除原文件", "type": "select",
     "default": "是", "options": ["是", "否"], "hint": "转成 mp4 之后把原来的 ts 删掉，省空间"},
    {"key": "mp4格式重新编码为h264", "label": "mp4 重新编码为 h264", "type": "select",
     "default": "否", "options": ["否", "是"],
     "hint": "只有播放器或剪辑软件不认默认编码时才需要开，重编码很慢"},
]


# --------------------------------------------------------------------------- #
# ini 处理：按行替换，保留注释
# --------------------------------------------------------------------------- #

def _read_ini(path: Path, section: str) -> dict[str, str]:
    parser = configparser.RawConfigParser()
    try:
        parser.read(path, encoding="utf-8-sig")
    except Exception:
        return {}
    if not parser.has_section(section):
        return {}
    return {key: value for key, value in parser.items(section)}


def _update_ini(path: Path, section: str, updates: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8-sig")
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()

    header = f"[{section}]"
    start = next((i for i, line in enumerate(lines) if line.strip() == header), None)
    if start is None:
        raise ValueError(f"配置文件里找不到 [{section}] 段")
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")),
        len(lines),
    )

    remaining = dict(updates)
    for i in range(start + 1, end):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw:
            continue
        key = raw.split("=", 1)[0].strip()
        if key in remaining:
            lines[i] = f"{key} = {remaining.pop(key)}"
    for key, value in remaining.items():      # 原文件里没有的键，补在段末
        lines.insert(end, f"{key} = {value}")
        end += 1

    path.write_text(eol.join(lines) + eol, encoding="utf-8-sig")


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #

class Api:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.config_file = base_dir / "config" / "config.ini"
        self.room_file = base_dir / "config" / "URL_config.ini"
        self.recorder = RecorderProcess(base_dir)
        self.window = None            # 由 gui.py 创建窗口后回填

    # ---------------------------------------------------------------- 卡密

    def _license_payload(self, info: lic.LicenseInfo) -> dict:
        return {
            "ok": info.ok,
            "status": info.status.value,
            "message": info.message,
            "describe": lic.describe(info),
            "machine_code": lic.machine_code(),
            "permanent": info.permanent,
            "expire_date": info.expire_date,
            "days_left": info.days_left,
            "bound": info.bound,
            "key_id": info.key_id,
            # 授权文件存在哪、能不能写 —— 售后排查用得上
            "storage": lic.storage_info(),
        }

    def license_state(self) -> dict:
        return self._license_payload(lic.current_status())

    def activate(self, key: str) -> dict:
        info = lic.activate(key or "")
        return self._license_payload(info)

    def license_configured(self) -> bool:
        return lic.secret_configured()

    # ---------------------------------------------------------------- 录制

    def status(self) -> dict:
        return {
            "running": self.recorder.is_running(),
            "uptime": self.recorder.uptime(),
            "room_count": len(self.get_rooms()),
            "save_path": self._effective_save_path(),
            "summary": self.recorder.recent_summary(),
            "proxy": self.proxy_state(),
        }

    def poll(self) -> dict:
        """界面轮询专用：一次调用同时拿回状态和新日志。

        pywebview 每次 JS→Python 调用都要走一次桥接开销，把两个请求并成一个
        能明显减轻点击按钮时的卡顿感。
        """
        return {"status": self.status(), "lines": self.recorder.read_logs()}

    def start(self) -> dict:
        if not lic.current_status().ok:
            return {"ok": False, "message": "卡密无效或已过期，无法启动录制"}
        if not self.get_rooms():
            return {"ok": False, "message": "还没有添加直播间，请先在「录制」页添加"}
        return self.recorder.start()

    def stop(self) -> dict:
        return self.recorder.stop()

    def logs(self) -> dict:
        return {"lines": self.recorder.read_logs(), "running": self.recorder.is_running()}

    # ---------------------------------------------------------------- 房间

    def get_rooms(self) -> list[dict]:
        if not self.room_file.exists():
            return []
        try:
            text = self.room_file.read_text(encoding="utf-8-sig", errors="ignore")
        except OSError:
            return []
        rooms: list[dict] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            url, _, name = line.partition(",")
            name = name.strip()
            if name.startswith("主播:"):
                name = name[len("主播:"):].strip()
            url = url.strip()
            if url:
                rooms.append({"url": url, "name": name or url})
        return rooms

    def add_room(self, url: str, name: str = "") -> dict:
        url = (url or "").strip().rstrip(",")
        if not url:
            return {"ok": False, "message": "请输入直播间地址"}
        if not re.match(r"^https?://", url, re.I):
            return {"ok": False, "message": "地址要以 http:// 或 https:// 开头"}
        if any(room["url"] == url for room in self.get_rooms()):
            return {"ok": False, "message": "这个地址已经在列表里了"}

        name = (name or "").strip()
        self.room_file.parent.mkdir(parents=True, exist_ok=True)
        needs_leading_newline = False
        if self.room_file.exists():
            tail = self.room_file.read_bytes()[-1:]
            needs_leading_newline = bool(tail) and tail not in (b"\n", b"\r")
        with self.room_file.open("a", encoding="utf-8") as handle:
            if needs_leading_newline:
                handle.write("\n")
            handle.write(f"{url},主播: {name or url}\n")
        return {"ok": True, "message": "已添加", "rooms": self.get_rooms()}

    def del_room(self, url: str) -> dict:
        url = (url or "").strip()
        rooms = [room for room in self.get_rooms() if room["url"] != url]
        self._write_rooms(rooms)
        return {"ok": True, "message": "已删除", "rooms": rooms}

    def clear_rooms(self) -> dict:
        self._write_rooms([])
        return {"ok": True, "message": "已清空", "rooms": []}

    def _write_rooms(self, rooms: list[dict]) -> None:
        self.room_file.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"{room['url']},主播: {room['name']}" for room in rooms]
        body = "\n".join(lines) + ("\n" if lines else "")
        self.room_file.write_text(body, encoding="utf-8-sig")

    # ---------------------------------------------------------------- 配置

    def get_config(self) -> dict:
        values = _read_ini(self.config_file, CONFIG_SECTION)
        return {
            "schema": CONFIG_SCHEMA,
            "values": {item["key"]: values.get(item["key"], "") for item in CONFIG_SCHEMA},
            "all": values,
            "config_file": str(self.config_file),
        }

    def save_config(self, values: dict) -> dict:
        known = {item["key"] for item in CONFIG_SCHEMA}
        updates = {
            key: str(value).strip()
            for key, value in (values or {}).items()
            if key in known
        }
        if not updates:
            return {"ok": False, "message": "没有需要保存的配置"}
        try:
            _update_ini(self.config_file, CONFIG_SECTION, updates)
        except Exception as err:
            return {"ok": False, "message": f"保存失败：{err}"}
        return {"ok": True, "message": "设置已保存", "values": self.get_config()["values"]}

    def _effective_save_path(self) -> str:
        configured = _read_ini(self.config_file, CONFIG_SECTION).get("直播保存路径(不填则默认)", "")
        return configured.strip() or str(self.base_dir / "downloads")

    # ---------------------------------------------------------------- 代理

    def proxy_state(self) -> dict:
        """当前配置的代理 + 本地端口在不在监听。

        只做一次本机 TCP 连接检查，不发网络请求，所以轮询里可以随便调。
        """
        values = _read_ini(self.config_file, CONFIG_SECTION)
        enabled = (values.get("是否使用代理ip(是/否)") or "否").strip() == "是"
        address = (values.get("代理地址") or "").strip()
        status = detect.address_status(address) if address else {"configured": False, "listening": False}
        return {
            "enabled": enabled,
            "address": address,
            "listening": status.get("listening"),
            "host": status.get("host", ""),
            "port": status.get("port", 0),
            "tun": detect.tun_interface(),
            "platforms": values.get("使用代理录制的平台(逗号分隔)", ""),
        }

    def detect_proxies(self, check_tiktok: bool = True) -> dict:
        """扫本机正在跑的代理，可选顺带验证能不能上 TikTok。"""
        try:
            result = detect.detect(check_tiktok_too=bool(check_tiktok))
        except Exception as err:
            return {"ok": False, "message": f"检测失败：{err}", "candidates": []}
        result["ok"] = True
        result["current"] = self.proxy_state()
        return result

    def test_proxy(self, address: str) -> dict:
        address = (address or "").strip()
        if not address:
            return {"ok": False, "message": "没有填代理地址"}
        local = detect.address_status(address)
        if local.get("listening") is False:
            return {"ok": False, "message": f"{address} 没有在监听，VPN 可能没开", "listening": False}
        result = detect.test(address)
        if not result["ok"]:
            return {"ok": False, "message": f"连不上：{result['error']}", "listening": True}
        tiktok = detect.check_tiktok(address)
        return {
            "ok": True,
            "message": f"代理可用（{result['latency_ms']}ms）；TikTok：{tiktok['note']}",
            "latency_ms": result["latency_ms"],
            "tiktok": tiktok["result"],
            "tiktok_note": tiktok["note"],
        }

    def apply_proxy(self, address: str) -> dict:
        """写进 config.ini，并保证 tiktok 在「走代理的平台」列表里。"""
        address = (address or "").strip()
        if not address:
            return {"ok": False, "message": "代理地址为空"}
        updates = {"是否使用代理ip(是/否)": "是", "代理地址": address}

        current = _read_ini(self.config_file, CONFIG_SECTION).get("使用代理录制的平台(逗号分隔)", "")
        platforms = [item.strip() for item in re.split(r"[,，]", current) if item.strip()]
        if "tiktok" not in {item.lower() for item in platforms}:
            platforms.append("tiktok")
            updates["使用代理录制的平台(逗号分隔)"] = ", ".join(platforms)

        try:
            _update_ini(self.config_file, CONFIG_SECTION, updates)
        except Exception as err:
            return {"ok": False, "message": f"写入配置失败：{err}"}
        return {"ok": True, "message": f"已启用代理 {address}", "proxy": self.proxy_state()}

    def disable_proxy(self) -> dict:
        """TUN 全局模式下不用再设代理，流量在网络层已经被接走了。"""
        try:
            _update_ini(self.config_file, CONFIG_SECTION, {"是否使用代理ip(是/否)": "否"})
        except Exception as err:
            return {"ok": False, "message": f"写入配置失败：{err}"}
        return {"ok": True, "message": "已关闭录制器代理（改走系统全局路由）", "proxy": self.proxy_state()}

    # ---------------------------------------------------------------- 系统

    def pick_folder(self) -> str:
        if self.window is None:
            return ""
        try:
            import webview

            result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
        except Exception:
            return ""
        if not result:
            return ""
        return result[0] if isinstance(result, (list, tuple)) else str(result)

    def open_path(self, path: str = "") -> dict:
        target = (path or "").strip() or self._effective_save_path()
        if not os.path.exists(target):
            return {"ok": False, "message": f"路径不存在：{target}"}
        try:
            if os.name == "nt":
                os.startfile(target)          # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
        except Exception as err:
            return {"ok": False, "message": f"打开失败：{err}"}
        return {"ok": True, "message": "已打开"}

    def open_url(self, url: str) -> dict:
        if not re.match(r"^https?://", url or "", re.I):
            return {"ok": False, "message": "链接不合法"}
        return self.open_path_external(url)

    def open_path_external(self, url: str) -> dict:
        try:
            if os.name == "nt":
                os.startfile(url)             # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", url])
            else:
                subprocess.Popen(["xdg-open", url])
        except Exception as err:
            return {"ok": False, "message": f"打开失败：{err}"}
        return {"ok": True, "message": "已在浏览器中打开"}

    def about(self) -> dict:
        return {
            "name": APP_NAME,
            "version": APP_VERSION,
            "upstream_name": "DouyinLiveRecorder",
            "upstream_author": "Hmily",
            "upstream_url": UPSTREAM_URL,
            "copyright": "Copyright (c) 2023-2025 Hmily",
            "license": "MIT License",
            "notice": (
                "本软件基于开源项目 DouyinLiveRecorder 构建，遵循 MIT 协议，"
                "保留原作者的版权声明。原项目地址见上方链接。"
            ),
            "python": platform.python_version(),
            "system": f"{platform.system()} {platform.release()}",
        }

    def shutdown(self) -> dict:
        """关窗前把录制子进程收干净，避免留下孤儿进程。"""
        if self.recorder.is_running():
            self.recorder.stop()
        return {"ok": True}
