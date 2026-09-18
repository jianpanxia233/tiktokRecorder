# -*- coding: utf-8 -*-
"""生成卡密校验用的发卡密钥（HMAC 对称密钥）。

用法::

    python tools/gen_secret.py            # 首次生成；已存在则拒绝覆盖
    python tools/gen_secret.py --force    # 强制重新生成

生成两个文件：

``tools/license_secret.txt``
    **发卡密钥明文**（64 位十六进制）。只留在你自己手里：备份好、不要外泄、
    不要提交到 git。有了它就能无限生成卡密。

``app/secret.py``
    内嵌进客户端程序的同一把密钥，做了一层异或混淆 —— 能挡住 ``strings``
    和 grep 这类土办法，但**挡不住真正会逆向的人**。这是离线卡密的固有弱点，
    不是这个脚本没写好。

注意：``--force`` 重新生成密钥后，**之前发出的所有卡密都会失效**，
因为程序里的密钥换了。
"""

from __future__ import annotations

import argparse
import os
import platform
import stat
import sys
from pathlib import Path

# Windows 控制台默认是 cp1252 / cp936，直接 print 中文会抛 UnicodeEncodeError，
# 在 CI 里会把整个构建打断。统一把输出流切成 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parents[1]
SECRET_FILE = ROOT / "tools" / "license_secret.txt"
EMBED_FILE = ROOT / "app" / "secret.py"

SECRET_BYTES = 32

EMBED_TEMPLATE = '''# -*- coding: utf-8 -*-
"""由 tools/gen_secret.py 自动生成，请勿手改。

这里内嵌的是卡密校验用的对称密钥（异或混淆后）。
客户端校验卡密要用它，所以它必然存在于程序里 —— 逆向能力足够的人可以还原出来。
这是离线卡密的固有限制。
"""

SECRET_BLOB_HEX = "{blob}"
SECRET_MASK_HEX = "{mask}"
'''


def restrict_to_owner(path: Path) -> None:
    """把文件权限收紧到「只有当前用户可以读写」。

    注意：**Windows 上 ``os.chmod`` 改了等于没改** —— 它只能切只读位，
    POSIX 权限位在 Windows 上不参与访问控制，``st_mode`` 一直是 0o666。
    所以这里必须走各平台真正的机制：

    * Windows：用 .NET 的 ACL API 去掉继承，只留「当前用户」一条 FullControl。
      不做这一步的话，密钥文件会从父目录继承到
      ``NT AUTHORITY\\Authenticated Users:(M)``，同一台机器上**任何**账户都能读，
      拿到它就等于拿到了发卡能力。
    * 其它平台：``os.chmod(0o600)``，本来就是对的。
    """
    if platform.system() == "Windows":
        try:
            import clr  # noqa: F401  必须先 import clr，pythonnet 才会注册 System 命名空间
            from System.IO import File  # type: ignore[import-not-found]
            from System.Security.AccessControl import (  # type: ignore[import-not-found]
                AccessControlType,
                FileSystemAccessRule,
                FileSystemRights,
                InheritanceFlags,
                PropagationFlags,
            )
            from System.Security.Principal import (  # type: ignore[import-not-found]
                SecurityIdentifier,
                WindowsIdentity,
            )

            # InheritanceFlags.None / PropagationFlags.None 不能直接写点号取 ——
            # None 是 Python 关键字，`X.None` 是语法错误，只能用 getattr 取。
            inherit_none = getattr(InheritanceFlags, "None")
            propagate_none = getattr(PropagationFlags, "None")

            identity = WindowsIdentity.GetCurrent()
            owner = identity.User
            if owner is None:                       # 极少数取不到 SID 的情况
                owner = SecurityIdentifier(identity.Owner.Value)

            full_path = str(Path(path).resolve())
            info = File.GetAccessControl(full_path)
            # 断开继承，并且不把继承来的规则复制过来
            info.SetAccessRuleProtection(True, False)
            for rule in list(info.GetAccessRules(True, True, SecurityIdentifier)):
                info.RemoveAccessRule(rule)
            info.AddAccessRule(
                FileSystemAccessRule(
                    owner,
                    FileSystemRights.FullControl,
                    inherit_none,
                    propagate_none,
                    AccessControlType.Allow,
                )
            )
            File.SetAccessControl(full_path, info)
            return
        except Exception as err:  # pragma: no cover - 兜底：至少别把生成流程搞挂
            print(f"警告：收紧 Windows ACL 失败（{type(err).__name__}: {err}），"
                  f"请手动确认 {path.name} 没有被其他账户读取")

    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="生成卡密发卡密钥")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的密钥（会让已发出的卡全部失效）")
    parser.add_argument("--from-hex", metavar="HEX",
                        help="使用已有的密钥（64 位十六进制）而不是随机生成；CI 里从仓库 Secret 注入时用")
    args = parser.parse_args()

    if SECRET_FILE.exists() and not args.force and not args.from_hex:
        print(f"密钥已存在：{SECRET_FILE.relative_to(ROOT)}")
        print("如需重新生成，加 --force —— 但要清楚：")
        print("  1. 之前发出的所有卡密会全部失效")
        print("  2. 你还需要重新打包发布客户端，否则老程序验不了新卡")
        return 1

    if args.from_hex:
        try:
            secret = bytes.fromhex(args.from_hex.strip())
        except ValueError:
            print("--from-hex 不是合法的十六进制")
            return 1
        if len(secret) != SECRET_BYTES:
            print(f"--from-hex 长度不对：应为 {SECRET_BYTES} 字节（{SECRET_BYTES * 2} 个十六进制字符），实际 {len(secret)} 字节")
            return 1
    else:
        secret = os.urandom(SECRET_BYTES)

    mask = os.urandom(SECRET_BYTES)
    blob = bytes(a ^ b for a, b in zip(secret, mask))

    SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    SECRET_FILE.write_text(secret.hex() + "\n", encoding="utf-8")
    restrict_to_owner(SECRET_FILE)

    EMBED_FILE.parent.mkdir(parents=True, exist_ok=True)
    EMBED_FILE.write_text(
        EMBED_TEMPLATE.format(blob=blob.hex(), mask=mask.hex()), encoding="utf-8"
    )

    print("发卡密钥生成完成")
    print(f"  密钥明文（保密！务必备份）：{SECRET_FILE.relative_to(ROOT)}")
    print(f"  程序内嵌（随程序分发）    ：{EMBED_FILE.relative_to(ROOT)}")
    print()
    print("接下来：")
    print("  python tools/keygen.py --days 30 --count 5     # 发几张测试卡")
    return 0


if __name__ == "__main__":
    sys.exit(main())
