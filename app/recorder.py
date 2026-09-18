# -*- coding: utf-8 -*-
"""录制主程序的进程管理与日志跟读。

图形界面和录制主程序跑在两个进程里。``main.py`` 没有 ``if __name__ == "__main__"``
保护，import 即开跑，所以必须隔离；隔离开之后启停、崩溃恢复、日志清理都简单得多。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

STOP_TIMEOUT = 8                      # 优雅停止的等待秒数，超时强杀
LOG_FILES = ("PlayURL.log", "streamget.log")
BACKLOG_LINES = 300                   # 界面首次打开时最多回填多少行

_EVENT_LOG = "PlayURL.log"            # INFO 级：开播/关播等事件
_ERROR_LOG = "streamget.log"          # 非 INFO：错误与调试信息


def _tail_lines(path: Path, limit: int) -> list[str]:
    """读文件末尾若干行。日志按 300KB 轮转，直接全读也不大。"""
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = [line for line in text.splitlines() if line.strip()]
    return lines[-limit:]


def _line_time(line: str) -> datetime | None:
    """日志行开头是 ``2026-09-16 23:31:07.159``。"""
    try:
        return datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S.%f")
    except (ValueError, TypeError):
        return None


def _clean_error(line: str) -> str:
    """把日志行压成一条能给用户看的短句。"""
    text = line.strip()
    if "| ERROR" in text:
        text = text.split("| ERROR", 1)[1].lstrip(" |")
    return text[:200]


class LogTailer:
    """按行跟进日志文件，并处理 loguru 轮转导致的文件截断。"""

    def __init__(self, path: Path):
        self.path = path
        self.pos = 0

    def read_new(self) -> list[str]:
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self.pos:           # 文件被轮转或清空，回到开头重读
            self.pos = 0
        if size == self.pos:
            return []
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(self.pos)
                chunk = handle.read()
                self.pos = handle.tell()
        except OSError:
            return []
        return [line for line in chunk.splitlines() if line.strip()]

    def prime(self) -> list[str]:
        """第一次读只回填末尾若干行，避免把历史日志全灌给界面。"""
        first = self.pos == 0
        lines = self.read_new()
        return lines[-BACKLOG_LINES:] if first else lines


class RecorderProcess:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.process: subprocess.Popen | None = None
        self.started_at: float | None = None
        self.tailers = {name: LogTailer(base_dir / "logs" / name) for name in LOG_FILES}

    # ------------------------------------------------------------------ #
    # 启停
    # ------------------------------------------------------------------ #

    def _command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            return [sys.executable, "--run-recorder"]
        launcher = Path(__file__).resolve().parents[1] / "launcher.py"
        return [sys.executable, str(launcher), "--run-recorder"]

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def uptime(self) -> int:
        if not self.is_running() or self.started_at is None:
            return 0
        return int(time.time() - self.started_at)

    def _running_pids(self) -> list[int]:
        """找出所有在跑的录制器进程 pid（不只是本对象跟踪的那一个）。

        为什么需要这个：界面只跟踪自己启动的那个子进程。一旦界面被强杀/崩溃，
        录制器会变成孤儿继续录；用户重新打开界面再点「开始录制」，
        就会**两个录制器并发录同一场直播**，各自写一套文件——
        表现就是「同一时间段出现多个内容重复的碎片」。
        """
        pids: list[int] = []
        if self.is_running() and self.process is not None:
            pids.append(self.process.pid)
        if os.name != "nt":
            return pids
        try:
            out = subprocess.run(
                ["wmic", "process", "where", "name='python.exe'", "get", "ProcessId,CommandLine", "/format:csv"],
                capture_output=True, text=True, timeout=10, errors="replace",
            ).stdout
        except Exception:
            return pids                      # 查不到就算了，不要因此拦住启动
        for line in out.splitlines():
            if "run-recorder" not in line:
                continue
            tail = line.rsplit(",", 1)[-1].strip()
            if tail.isdigit():
                pid = int(tail)
                if pid not in pids:
                    pids.append(pid)
        return pids

    def start(self) -> dict:
        if self.is_running():
            return {"ok": False, "message": "录制已经在运行中"}

        others = [p for p in self._running_pids()
                  if self.process is None or p != self.process.pid]
        if others:
            return {
                "ok": False,
                "message": (f"检测到已有录制进程在运行（pid {', '.join(map(str, others))}）。"
                            f"重复启动会并发录制同一场直播、写出多套重复文件。"
                            f"请先「停止录制」，或直接结束这些进程。"),
                "pids": others,
            }

        (self.base_dir / "logs").mkdir(parents=True, exist_ok=True)

        creationflags = 0
        start_new_session = True
        if os.name == "nt":
            # 必须新建进程组，否则 CTRL_BREAK 没法只发给我们这个子进程
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            start_new_session = False
        try:
            self.process = subprocess.Popen(
                self._command(),
                cwd=str(self.base_dir),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
                start_new_session=start_new_session,
            )
        except OSError as err:
            return {"ok": False, "message": f"启动失败：{err}"}
        self.started_at = time.time()
        return {"ok": True, "message": "录制已启动", "pid": self.process.pid}

    def stop(self) -> dict:
        if not self.is_running():
            self.process = None
            return {"ok": False, "message": "当前没有在录制"}
        process = self.process
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.send_signal(signal.SIGTERM)
        except Exception:
            pass

        graceful = True
        try:
            process.wait(timeout=STOP_TIMEOUT)
        except subprocess.TimeoutExpired:
            graceful = False
            self._kill_tree(process)
        self.process = None
        self.started_at = None
        return {
            "ok": True,
            "graceful": graceful,
            "message": "录制已停止" if graceful else "停止超时，已强制结束",
        }

    @staticmethod
    def _kill_tree(process: subprocess.Popen) -> None:
        """强杀整棵进程树，避免 ffmpeg 子进程残留继续占磁盘。"""
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, check=False,
            )
        else:
            try:
                process.kill()
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #

    def read_logs(self) -> list[dict]:
        entries: list[dict] = []
        for name, tailer in self.tailers.items():
            source = "状态" if name == "PlayURL.log" else "调试"
            for line in tailer.prime():
                entries.append({"source": source, "text": line})
        return entries

    def reset_logs(self) -> None:
        for tailer in self.tailers.values():
            tailer.pos = 0

    # ------------------------------------------------------------------ #
    # 状态摘要：回答「为什么没录到东西」
    # ------------------------------------------------------------------ #

    def recent_summary(self, minutes: int = 15) -> dict:
        """最近有没有开播事件、有没有报错。

        主程序把「哪个主播正在录」只 print 到控制台、不写进日志文件，
        所以界面拿不到逐房间的实时状态；退一步用「有没有开播/关播事件」
        加「有没有错误」来回答用户真正关心的问题：到底录到了没有。
        """
        logs = self.base_dir / "logs"
        events = _tail_lines(logs / _EVENT_LOG, 200)
        errors = _tail_lines(logs / _ERROR_LOG, 400)

        deadline = datetime.now() - timedelta(minutes=minutes)
        recent_errors = [
            line for line in errors
            if "| ERROR" in line and (_line_time(line) or deadline) >= deadline
        ]

        last_event = events[-1] if events else ""
        return {
            "last_event": last_event,
            "last_event_at": (last_event[:23] if last_event else ""),
            "event_count": len(events),
            "recent_error_count": len(recent_errors),
            "last_error": _clean_error(recent_errors[-1]) if recent_errors else "",
            "window_minutes": minutes,
        }
