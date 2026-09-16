#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DouyinLiveRecorder 桌面版入口。

::

    python launcher.py                  # 打开图形界面
    python launcher.py --run-recorder   # 内部使用：以子进程方式跑录制主程序

图形界面和录制主程序刻意分成两个进程：``main.py`` 没有 ``if __name__ == "__main__"``
保护，import 就会开始录制，而且会拉起一堆常驻线程和 ffmpeg 子进程。
隔离之后，启停、崩溃恢复、日志清理都好处理得多。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parent


def base_dir() -> Path:
    """exe 所在目录（打包后）或仓库根目录（源码运行）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return SOURCE_ROOT


# --------------------------------------------------------------------------- #
# 打包后的 Windows 兜底
#
# exe 是以「无控制台」方式打包的（否则界面后面会挂一个黑框）。这带来两个问题，
# 都在这里一次性解决，不用去改上游那几十处 print / subprocess 调用：
#
#   1. sys.stdout / sys.stderr 变成 None，而 main.py 里有 sys.stdout.flush()、
#      loguru 的 sink 也指着 sys.stderr —— 不兜住会直接崩。这里接到日志文件上。
#   2. 没有控制台时，每次起子进程（ffmpeg、node）Windows 都会弹一个新的黑框，
#      os.system('cls') 更是会在循环里反复闪。这里统一加上 CREATE_NO_WINDOW。
# --------------------------------------------------------------------------- #

def _ensure_std_streams() -> None:
    if not getattr(sys, "frozen", False):
        return
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        log_dir = base_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handle = open(log_dir / "console.log", "a", encoding="utf-8", errors="replace")
    except OSError:
        handle = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = handle
    if sys.stderr is None:
        sys.stderr = handle


def _silence_subprocess_windows() -> None:
    if os.name != "nt":
        return
    flag = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

    original_popen = subprocess.Popen

    def quiet_popen(*args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | flag
        return original_popen(*args, **kwargs)

    # run/call/check_output/check_call 内部都走 Popen，改这一个就够
    subprocess.Popen = quiet_popen

    if getattr(sys, "frozen", False):
        def quiet_system(command):
            # 没控制台时清屏没意义，而 os.system('cls') 会闪黑框；
            # 转成带 CREATE_NO_WINDOW 的 subprocess，行为不变（仍返回退出码）
            return subprocess.call(command, shell=True)

        os.system = quiet_system


_ensure_std_streams()
_silence_subprocess_windows()


def _prepare_import_path() -> Path:
    directory = base_dir()
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    return directory


def run_recorder() -> None:
    """执行原录制主程序。

    因为 ``main.py`` 没有 ``__main__`` 保护，导入它本身就会跑起整个录制循环 ——
    这里正是利用这一点。也正因为如此，它必须独占一个进程。
    """
    directory = _prepare_import_path()
    try:
        os.chdir(directory)          # 固定工作目录，避免相对路径飘到别处
    except OSError:
        pass
    import main  # noqa: F401  导入即开始录制


def run_gui() -> int:
    _prepare_import_path()
    from app.gui import run

    return run()


def main() -> None:
    if "--run-recorder" in sys.argv:
        run_recorder()
    else:
        sys.exit(run_gui())


if __name__ == "__main__":
    main()
