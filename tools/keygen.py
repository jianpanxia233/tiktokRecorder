# -*- coding: utf-8 -*-
"""卡密生成器 —— 只给你自己发卡用，**不要打包进发给客户的程序**。

用法::

    python tools/keygen.py --days 30 --count 10            # 10 张 30 天卡
    python tools/keygen.py --days 365 --count 10 --out keys.txt
    python tools/keygen.py --permanent --count 3           # 永久卡
    python tools/keygen.py --days 30 --bind 6Q2X-8J5R-P4NC-7TWD   # 一机一码
    python tools/keygen.py --check DLR-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX
    python tools/keygen.py --check-file keys.txt

一机一码怎么用：让客户在软件「激活」页把机器码发给你，再用 ``--bind`` 生成，
生成的卡密只能在那台机器上用。
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import license as lic  # noqa: E402

SECRET_FILE = ROOT / "tools" / "license_secret.txt"


def load_secret() -> bytes:
    if not SECRET_FILE.exists():
        print(f"找不到发卡密钥：{SECRET_FILE}")
        print("先运行：python tools/gen_secret.py")
        sys.exit(1)
    try:
        secret = bytes.fromhex(SECRET_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        print(f"发卡密钥格式不对（应为十六进制）：{SECRET_FILE}")
        sys.exit(1)
    if len(secret) != 32:
        print(f"发卡密钥长度不对（应为 32 字节）：{SECRET_FILE}")
        sys.exit(1)
    return secret


def make_key(secret: bytes, days: int | None, bind: str | None) -> tuple[str, dict]:
    key_id = random.randint(1, 0xFFFF)
    if days is None:
        expire_day, expire_date = 0, None
    else:
        expire_date = date.today() + timedelta(days=days)
        expire_day = (expire_date - lic.KEY_EPOCH).days
        if expire_day <= 0 or expire_day > 0xFFFF:
            raise ValueError("有效期超出可编码范围")
    tag = lic.machine_tag(bind) if bind else b"\x00\x00"
    payload = lic.build_payload(key_id, expire_day, tag)
    return lic.encode_key(payload, lic.compute_mac(payload, secret)), {
        "key_id": key_id,
        "expire_date": expire_date.isoformat() if expire_date else "永久",
        "bound": bind or "不绑机",
    }


def cmd_check(target: str) -> int:
    info = lic.verify_key(target)
    body = lic.normalize_key(target)
    print(f"卡密  : {lic.format_key(body) if len(body) == lic.KEY_CHARS else target}")
    print(f"结果  : {info.status.value} - {info.message}")
    if info.key_id is not None:
        print(f"卡号  : {info.key_id}")
        print(f"有效期: {'永久' if info.permanent else info.expire_date}")
        print(f"剩余  : {'--' if info.permanent else f'{info.days_left} 天'}")
        print(f"绑定  : {'是（仅限指定机器）' if info.bound else '不绑机'}")
    return 0 if info.ok else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="卡密生成器（仅发卡方使用）")
    period = parser.add_mutually_exclusive_group()
    period.add_argument("--days", type=int, help="有效天数")
    period.add_argument("--permanent", action="store_true", help="永久有效")
    parser.add_argument("--count", type=int, default=1, help="生成数量，默认 1")
    parser.add_argument("--bind", metavar="机器码", help="绑定指定机器码（一机一码）")
    parser.add_argument("--out", metavar="文件", help="把卡密逐行写入文件")
    parser.add_argument("--check", metavar="卡密", help="校验一张卡密并退出")
    parser.add_argument("--check-file", metavar="文件", help="批量校验文件里的卡密")
    args = parser.parse_args()

    if args.check:
        return cmd_check(args.check)
    if args.check_file:
        lines = [ln.strip() for ln in Path(args.check_file).read_text(encoding="utf-8").splitlines()]
        return max((cmd_check(ln) for ln in lines if ln), default=0)

    if not args.permanent and args.days is None:
        parser.error("请指定 --days N 或 --permanent")

    secret = load_secret()
    results = []
    for _ in range(max(1, args.count)):
        results.append(make_key(secret, None if args.permanent else args.days, args.bind))

    for key, meta in results:
        print(f"{key}   卡号={meta['key_id']:<6} 到期={meta['expire_date']:<12} 绑定={meta['bound']}")

    if args.out:
        Path(args.out).write_text("\n".join(k for k, _ in results) + "\n", encoding="utf-8")
        print(f"\n已写入 {args.out}（共 {len(results)} 张）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
