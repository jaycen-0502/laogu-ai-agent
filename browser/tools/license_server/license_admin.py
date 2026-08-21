#!/usr/bin/env python3
"""老谷浏览器离线授权签发工具（Windows/Linux，UTF-8）。"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
except ImportError as exc:
    raise SystemExit(
        "缺少依赖 cryptography，请执行：python -m pip install -r requirements.txt"
    ) from exc


REQUEST_PREFIX = "LGREQ1."
ACTIVATION_PREFIX = "LGACT1."
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_KEY_PATH = SCRIPT_DIR / "Laogu-License-Issuer.pem"
DEFAULT_LEDGER_PATH = SCRIPT_DIR / "Laogu-License-Ledger.json"
DEFAULT_PASSWORD_PATH = SCRIPT_DIR / "Laogu-License-Password.txt"


def configure_console() -> None:
    """尽量让 Windows 和 Linux 终端统一使用 UTF-8。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def b64std_raw(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii").rstrip("=")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def secure_write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def read_password(password_path: Path, create: bool = False) -> bytes:
    env_password = os.environ.get("LAOGU_LICENSE_KEY_PASSWORD")
    if env_password:
        return env_password.encode("utf-8")
    if password_path.exists():
        password = password_path.read_text(encoding="utf-8").strip()
        if not password:
            raise ValueError(f"私钥密码文件为空：{password_path}")
        return password.encode("utf-8")
    if create:
        password = secrets.token_urlsafe(48)
        secure_write(password_path, password)
        print(f"已生成随机私钥密码文件：{password_path}")
        return password.encode("utf-8")
    password = getpass.getpass("管理员私钥密码：")
    if not password:
        raise ValueError("私钥密码不能为空")
    return password.encode("utf-8")


def decode_request(code: str) -> dict[str, Any]:
    normalized = "".join(code.strip().split())
    if not normalized.startswith(REQUEST_PREFIX):
        raise ValueError("请求码前缀无效")
    try:
        payload = json.loads(b64url_decode(normalized[len(REQUEST_PREFIX) :]).decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"请求码解析失败：{exc}") from exc
    required = ("deviceId", "installPublicKey", "nonce", "requestedAt")
    if payload.get("version") != 1 or any(not str(payload.get(key, "")).strip() for key in required):
        raise ValueError("请求码内容不完整")
    return payload


def generate_key(key_path: Path, password: bytes) -> str:
    if key_path.exists():
        raise ValueError(f"私钥文件已存在：{key_path}")
    private_key = Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(password),
    )
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(pem)
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    public_raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return b64std_raw(public_raw)


def load_private_key(key_path: Path, password: bytes) -> Ed25519PrivateKey:
    if not key_path.exists():
        raise ValueError(f"未找到管理员私钥：{key_path}")
    try:
        private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=password)
    except (ValueError, TypeError) as exc:
        raise ValueError("私钥密码错误或私钥文件损坏") from exc
    if not isinstance(private_key, Ed25519PrivateKey):
        raise ValueError("私钥类型不是 Ed25519")
    return private_key


def load_ledger(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"licenses": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"读取签发台账失败：{exc}") from exc
    if not isinstance(data.get("licenses"), dict):
        data["licenses"] = {}
    return data


def save_ledger(path: Path, ledger: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def issue_activation(
    request_code: str,
    private_key: Ed25519PrivateKey,
    license_id: str,
    customer: str,
    days: int,
) -> tuple[str, dict[str, Any]]:
    request = decode_request(request_code)
    if not 1 <= days <= 3650:
        raise ValueError("授权天数必须在 1 到 3650 之间")
    issued_at = utc_now()
    payload = {
        "version": 1,
        "licenseId": license_id,
        "customer": customer.strip(),
        "deviceId": request["deviceId"],
        "installPublicKey": request["installPublicKey"],
        "requestNonce": request["nonce"],
        "issuedAt": isoformat(issued_at),
        "expiresAt": isoformat(issued_at + timedelta(days=days)),
        "features": ["browser", "playwright", "external_api"],
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    signature = private_key.sign(raw)
    return f"{ACTIVATION_PREFIX}{b64url_encode(raw)}.{b64url_encode(signature)}", payload


def command_init(args: argparse.Namespace) -> None:
    public_key = generate_key(args.key, read_password(args.password_file, create=True))
    print(f"管理员私钥已生成：{args.key}")
    print("请离线备份私钥和密码；丢失后无法为现有客户端续期。")
    print(f"客户端公钥：{public_key}")


def command_inspect(args: argparse.Namespace) -> None:
    print(json.dumps(decode_request(args.request), ensure_ascii=False, indent=2))


def command_issue(args: argparse.Namespace) -> None:
    request = decode_request(args.request)
    license_id = args.license.strip() if args.license else ""
    if not license_id:
        timestamp = utc_now().strftime("%Y%m%d%H%M%S")
        license_id = f"AUTO-{timestamp}-{secrets.token_hex(3).upper()}"
    ledger = load_ledger(args.ledger)
    existing = ledger["licenses"].get(license_id)
    if existing and existing.get("deviceId") and existing["deviceId"] != request["deviceId"]:
        raise ValueError("该许可证已绑定其他电脑，请先执行 unbind 管理员解绑")
    private_key = load_private_key(args.key, read_password(args.password_file))
    code, payload = issue_activation(args.request, private_key, license_id, args.customer, args.days)
    ledger["licenses"][license_id] = {
        "licenseId": license_id,
        "customer": args.customer.strip(),
        "deviceId": request["deviceId"],
        "issuedAt": payload["issuedAt"],
        "expiresAt": payload["expiresAt"],
    }
    save_ledger(args.ledger, ledger)
    print(f"许可证：{license_id}")
    print(f"客户：{args.customer.strip()}")
    print(f"到期时间：{payload['expiresAt']}")
    print("激活码：")
    print(code)


def command_unbind(args: argparse.Namespace) -> None:
    ledger = load_ledger(args.ledger)
    record = ledger["licenses"].get(args.license)
    if not record:
        raise ValueError("未找到该许可证编号")
    record["deviceId"] = ""
    record["unboundAt"] = isoformat(utc_now())
    save_ledger(args.ledger, ledger)
    print(f"已解绑许可证：{args.license}")


def command_list(args: argparse.Namespace) -> None:
    ledger = load_ledger(args.ledger)
    print(json.dumps(ledger, ensure_ascii=False, indent=2))


def command_interactive(args: argparse.Namespace) -> None:
    print("老谷浏览器离线授权工具（Python）")
    request_code = input("粘贴客户请求码：").strip()
    days_text = input("授权天数 [7]：").strip()
    days = int(days_text) if days_text else 7
    customer = input("客户备注（可留空）：").strip()
    license_id = input("许可证编号（可留空，留空自动生成）：").strip()
    command_issue(
        argparse.Namespace(
            request=request_code,
            days=days,
            customer=customer,
            license=license_id,
            key=args.key,
            ledger=args.ledger,
            password_file=args.password_file,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="老谷浏览器离线授权签发工具")
    parser.add_argument("--key", type=Path, default=DEFAULT_KEY_PATH, help="管理员加密私钥路径")
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER_PATH, help="签发台账路径")
    parser.add_argument("--password-file", type=Path, default=DEFAULT_PASSWORD_PATH, help="私钥密码文件路径")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("init", help="首次生成加密 Ed25519 私钥")

    inspect_parser = subparsers.add_parser("inspect", help="查看客户请求码")
    inspect_parser.add_argument("--request", required=True)

    issue_parser = subparsers.add_parser("issue", help="签发激活码")
    issue_parser.add_argument("--request", required=True)
    issue_parser.add_argument("--days", type=int, default=7)
    issue_parser.add_argument("--customer", default="")
    issue_parser.add_argument("--license", default="", help="可选；留空时自动生成")

    unbind_parser = subparsers.add_parser("unbind", help="管理员解绑许可证")
    unbind_parser.add_argument("--license", required=True)

    subparsers.add_parser("list", help="查看签发台账")
    subparsers.add_parser("interactive", help="交互式签发")
    return parser


def main() -> int:
    configure_console()
    parser = build_parser()
    args = parser.parse_args()
    command = args.command or "interactive"
    try:
        {
            "init": command_init,
            "inspect": command_inspect,
            "issue": command_issue,
            "unbind": command_unbind,
            "list": command_list,
            "interactive": command_interactive,
        }[command](args)
        return 0
    except (ValueError, OSError, KeyboardInterrupt) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
