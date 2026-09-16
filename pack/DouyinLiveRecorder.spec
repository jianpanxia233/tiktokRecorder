# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir 模式）。

在**仓库根目录**执行：

    pyinstaller pack/DouyinLiveRecorder.spec --noconfirm

产物在 ``dist/DouyinLiveRecorder/``，还需要跑一次后处理把 config/ffmpeg/node
摆到 exe 同级（见 ``pack/post_build.py``）。

为什么是 onedir 而不是 onefile：包体里要带 ffmpeg 和 node，onefile 每次启动都
要把上百 MB 解压到临时目录，启动要等十几秒；onedir 解压即用，秒开。
"""

from pathlib import Path
import sys

ROOT = Path(SPECPATH).resolve().parent        # SPECPATH 指向 pack/，上一级是仓库根
IS_WINDOWS = sys.platform.startswith("win")

# --------------------------------------------------------------------------- #
# 随包一起带上的静态资源
# --------------------------------------------------------------------------- #
datas = [
    (str(ROOT / "app" / "web"), "app/web"),                   # 图形界面
    (str(ROOT / "src" / "javascript"), "src/javascript"),     # execjs 要读的 JS
    (str(ROOT / "i18n"), "i18n"),                             # 翻译
]

# --------------------------------------------------------------------------- #
# 静态分析看不到的 import
# --------------------------------------------------------------------------- #
hiddenimports = [
    # 上游录制主程序：launcher.py 里 `import main` 触发，main 又会拉起下面这些
    "main",
    "msg_push",
    "ffmpeg_install",
    "i18n",
    "src",
    "src.spider",
    "src.stream",
    "src.room",
    "src.utils",
    "src.proxy",
    "src.ab_sign",
    "src.logger",
    "src.initializer",
    "src.http_clients",
    "src.http_clients.async_http",
    "src.http_clients.sync_http",
    # 桌面版自身
    "app",
    "app.api",
    "app.gui",
    "app.license",
    "app.recorder",
    "app.detect",
    "app.secret",          # 卡密密钥，CI 里由 GitHub Secret 生成
    # pywebview 的后端是按操作系统动态 import 的，静态分析发现不了
    "webview",
    "webview.platforms.edgechromium",
    "webview.platforms.cocoa",
    "webview.platforms.gtk",
    "webview.platforms.qt",
    "bottle",
    "proxy_tools",
    # httpx 只在走 socks5 代理时才 import 它
    "socksio",
    "execjs",
]

icon_file = Path(SPECPATH) / "app.ico"
version_file = Path(SPECPATH) / "version_info.txt"

a = Analysis(
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DouyinLiveRecorder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # 不要控制台窗口。副作用（stdout 为 None、子进程闪黑框）已经在
    # launcher.py 的 _ensure_std_streams / _silence_subprocess_windows 里兜住了。
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # 图标和版本资源是 Windows 专用的（.ico / VERSIONINFO），
    # 在 macOS 上本地试打包时不要传，否则会报错
    icon=str(icon_file) if (IS_WINDOWS and icon_file.exists()) else None,
    version=str(version_file) if (IS_WINDOWS and version_file.exists()) else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="DouyinLiveRecorder",
)
