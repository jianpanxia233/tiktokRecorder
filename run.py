#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目本地启动入口。

嵌入式 Python 因为带了 ``python312._pth``，会进入 isolated 模式：
既不会把脚本所在目录加进 ``sys.path``，也不认 ``PYTHONPATH``，
所以直接 ``python main.py`` 会报 ``No module named 'src'``。

这里手动把项目根目录补进 ``sys.path``、切好工作目录，再执行目标脚本。

::

    python run.py              # 跑录制主程序 main.py
    python run.py launcher.py  # 打开桌面图形界面
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> int:
    target = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "main.py")
    if not target.exists():
        print(f"找不到要执行的脚本: {target}", file=sys.stderr)
        return 2

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    # main.py / src 里大量使用相对路径，工作目录必须是项目根
    import os

    os.chdir(ROOT)

    sys.argv = [str(target)] + sys.argv[2:]
    runpy.run_path(str(target), run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
