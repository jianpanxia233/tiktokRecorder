# -*- coding: utf-8 -*-
"""PyInstaller 打包后处理：把运行时该有的东西摆到 exe 同级目录。

为什么需要这一步：PyInstaller 6 的 onedir 把 ``datas`` 全放进 ``_internal/``，
但上游代码是按 **exe 同级目录** 找东西的：

===================================  ==========================================
上游代码                              期望的位置
===================================  ==========================================
``main.py`` / ``src/logger.py``      ``<exe>/config/config.ini``、``<exe>/logs/``
``ffmpeg_install.py``                ``<exe>/ffmpeg/ffmpeg.exe``（会加进 PATH）
``src/__init__.py``                  ``<exe>/node/node.exe``（同上）
``i18n.py``                          已自己兼容 ``_internal/i18n``
===================================  ==========================================

不跑这一步，新用户机器上会：

* 找不到 config，界面上改设置不生效
* ffmpeg 缺失 → ``main.py`` 直接 ``sys.exit(1)`` 退出
* node 缺失 → ``src/__init__.py`` 去 npmmirror 现下载，卡启动且规则一失效就装不上

用法::

    python pack/post_build.py --app-dir dist/DouyinLiveRecorder --ffmpeg --node
"""

from __future__ import annotations

import argparse
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

# Windows 控制台默认不是 UTF-8，print 中文会抛 UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parents[1]
DIST_NAME = "DouyinLiveRecorder"

# 内置的 ffmpeg / node 都是 Windows 版本；版本号写死便于复现
FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
NODE_VERSION = "v22.11.0"
NODE_URL = f"https://npmmirror.com/mirrors/node/{NODE_VERSION}/node-{NODE_VERSION}-win-x64.zip"

USAGE_TXT = """\
DouyinLiveRecorder 桌面版 —— 使用说明
========================================

一、第一次使用
  1. 双击 DouyinLiveRecorder.exe
  2. 把卖家给你的卡密填进激活页，点「激活」
  3. 到「录制」页添加直播间地址，点「开始录制」

二、目录说明
  config/     配置文件。界面上的「设置」改的就是这里，不用手动编辑
  node/       内置的 JS 运行环境，不要删
  ffmpeg/     内置的录制组件，不要删
  logs/       运行日志，出问题时把这个目录发给我
  downloads/  默认保存目录（在「设置」里可以改）

三、常见问题
  1. 显示「代理端口没有在监听」
     说明你填的代理地址没有程序在跑。到「设置 → 代理 / VPN」点「自动检测」，
     它会找出本机正在运行的代理，选一个点「使用这个」。
     改完设置要停止录制再重新开始，才会生效。

  2. 显示「没有房间开播」
     这是正常的 —— 只有主播正在直播时才会真正开始录制并生成文件。
     默认每 300 秒检查一次是否开播。

  3. 录制文件在哪里
     看「录制」页底部显示的保存目录；点右上角「打开保存目录」可以直接打开。

  4. 想换成 mp4
     默认就是「录 ts → 录完自动转 mp4」。中途断电或强杀进程时，
     ts 已经写入的部分不会损坏，这是比直接录 mp4 稳的做法。

四、版权
  本软件基于开源项目 DouyinLiveRecorder 构建，遵循 MIT 协议，
  保留原作者 Hmily 的版权声明。项目地址：
  https://github.com/ihmily/DouyinLiveRecorder
"""


def log(message: str) -> None:
    print(f"  {message}")


# --------------------------------------------------------------------------- #

def ensure_runtime_dirs(app_dir: Path) -> None:
    for name in ("logs", "downloads", "config", "ffmpeg", "node"):
        (app_dir / name).mkdir(parents=True, exist_ok=True)
    log("已创建 logs/ downloads/ config/ ffmpeg/ node/")


def seed_config(app_dir: Path) -> None:
    """放一份配置模板，但**不覆盖**已有的 —— 升级时不能把用户的设置冲掉。"""
    for name in ("config.ini", "URL_config.ini"):
        target = app_dir / "config" / name
        source = ROOT / "config" / name
        if target.exists():
            log(f"config/{name} 已存在，保留不动")
            continue
        if source.exists():
            shutil.copy2(source, target)
            log(f"已放入 config/{name}")


def copy_notices(app_dir: Path) -> None:
    license_file = ROOT / "LICENSE"
    if license_file.exists():
        shutil.copy2(license_file, app_dir / "LICENSE")
        log("已放入 LICENSE")
    (app_dir / "使用说明.txt").write_text(USAGE_TXT, encoding="utf-8")
    log("已生成 使用说明.txt")


# --------------------------------------------------------------------------- #

def download(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 0:
        log(f"{dest.name} 已存在，跳过下载")
        return dest
    log(f"下载 {dest.name} …（{url}）")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as response, dest.open("wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while chunk := response.read(1 << 20):
            handle.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r    {done * 100 // total}% ({done >> 20}/{total >> 20} MB)", end="")
    print()
    return dest


def extract_named(zip_path: Path, wanted: str, target: Path) -> bool:
    """从 zip 里挑出文件名结尾是 ``wanted`` 的成员，解到 ``target``。"""
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.namelist():
            if member.replace("\\", "/").endswith(wanted):
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                log(f"已放入 {target.relative_to(target.parents[1])}")
                return True
    return False


def place_ffmpeg(app_dir: Path) -> bool:
    target = app_dir / "ffmpeg" / "ffmpeg.exe"
    if target.exists():
        log("ffmpeg/ffmpeg.exe 已存在，跳过")
        return True
    zip_path = ROOT / "build-cache" / "ffmpeg-win64.zip"
    download(FFMPEG_URL, zip_path)
    if not extract_named(zip_path, "/bin/ffmpeg.exe", target):
        log("!! 压缩包里没找到 ffmpeg.exe")
        return False
    extract_named(zip_path, "/bin/ffprobe.exe", app_dir / "ffmpeg" / "ffprobe.exe")
    return True


def place_node(app_dir: Path) -> bool:
    target = app_dir / "node" / "node.exe"
    if target.exists():
        log("node/node.exe 已存在，跳过")
        return True
    zip_path = ROOT / "build-cache" / f"node-{NODE_VERSION}-win-x64.zip"
    download(NODE_URL, zip_path)
    if not extract_named(zip_path, "/node.exe", target):
        log("!! 压缩包里没找到 node.exe")
        return False
    return True


# --------------------------------------------------------------------------- #

def main() -> int:
    parser = argparse.ArgumentParser(description="PyInstaller 打包后处理")
    parser.add_argument("--app-dir", required=True, help="PyInstaller 产物目录，如 dist/DouyinLiveRecorder")
    parser.add_argument("--ffmpeg", action="store_true", help="下载并内置 Windows 版 ffmpeg")
    parser.add_argument("--node", action="store_true", help="下载并内置 Windows 版 node")
    parser.add_argument("--no-notices", action="store_true", help="不生成 LICENSE / 使用说明.txt")
    args = parser.parse_args()

    app_dir = Path(args.app_dir)
    if not app_dir.is_dir():
        print(f"找不到产物目录：{app_dir}")
        return 1

    print(f"后处理 {app_dir}")
    ensure_runtime_dirs(app_dir)
    seed_config(app_dir)
    if not args.no_notices:
        copy_notices(app_dir)

    problems = []
    if args.ffmpeg and not place_ffmpeg(app_dir):
        problems.append("ffmpeg 内置失败 —— 客户端会在启动时直接退出（main.py 检测不到 ffmpeg 就 sys.exit(1)）")
    if args.node and not place_node(app_dir):
        problems.append("node 内置失败 —— 依赖 JS 签名的平台（斗鱼等）会不可用")

    total = sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file())
    print(f"\n完成，产物大小 {total / 1024 / 1024:.1f} MB")

    if problems:
        print("\n以下步骤失败：")
        for item in problems:
            print(f"  !! {item}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
