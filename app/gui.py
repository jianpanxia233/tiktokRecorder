# -*- coding: utf-8 -*-
"""pywebview 窗口的创建与生命周期。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import webview

from .api import Api

WINDOW_TITLE = "DouyinLiveRecorder 桌面版"
WINDOW_SIZE = (1120, 740)
WINDOW_MIN = (900, 620)


def base_dir() -> Path:
    """exe 所在目录（打包后）或仓库根目录（源码运行）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def web_dir() -> Path:
    """前端静态文件目录，同时照顾源码运行和 PyInstaller 打包两种情况。"""
    source = Path(__file__).resolve().parent / "web"
    if source.exists():
        return source
    candidates = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / "app" / "web")
        candidates.append(base_dir() / "app" / "web")
    return next((path for path in candidates if path.exists()), source)


def run() -> int:
    api = Api(base_dir())
    index = web_dir() / "index.html"
    if not index.exists():
        raise FileNotFoundError(f"找不到界面文件：{index}")

    window = webview.create_window(
        WINDOW_TITLE,
        url=index.as_uri(),
        js_api=api,
        width=WINDOW_SIZE[0],
        height=WINDOW_SIZE[1],
        min_size=WINDOW_MIN,
        text_select=True,
    )
    api.window = window

    def on_closing():
        # 关窗前把录制子进程收干净，否则会留下跑着的 ffmpeg
        try:
            api.shutdown()
        except Exception:
            pass
        return None

    window.events.closing += on_closing

    debug = bool(os.environ.get("DLR_GUI_DEBUG"))
    webview.start(debug=debug)
    return 0
