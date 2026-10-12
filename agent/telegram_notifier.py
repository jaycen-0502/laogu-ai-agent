# -*- coding: utf-8 -*-
"""Telegram 自动化战报通知服务模块 (多租户单机器人分流与私密投递)."""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.parse
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("agent.telegram_notifier")

# 官方统一默认机器人 Token (管理员提供，客户无需自行创建)
DEFAULT_OFFICIAL_BOT_TOKEN = "8719098983:AAF26d8CuPcBnTErgLghmbCv50oM1BXpQWw"
DEFAULT_OFFICIAL_BOT_USERNAME = "@xchengxutz_bot"


@dataclass
class TelegramConfig:
    """Telegram 通知配置."""
    enabled: bool = False
    use_custom_bot: bool = False
    bot_token: str = ""
    chat_id: str = ""
    proxy_url: str = ""
    notify_on_account_finish: bool = True
    notify_on_all_finish: bool = True
    notify_on_risk_alert: bool = True

    def get_effective_token(self) -> str:
        """获取生效的 Bot Token (优先自定义，其次使用官方统一 Token)."""
        if self.use_custom_bot and self.bot_token.strip():
            return self.bot_token.strip()
        return DEFAULT_OFFICIAL_BOT_TOKEN

    @classmethod
    def from_mapping(cls, mapping: Optional[dict[str, Any]]) -> TelegramConfig:
        if not mapping or not isinstance(mapping, dict):
            return cls()
        return cls(
            enabled=bool(mapping.get("enabled", False)),
            use_custom_bot=bool(mapping.get("use_custom_bot", False)),
            bot_token=str(mapping.get("bot_token") or "").strip(),
            chat_id=str(mapping.get("chat_id") or "").strip(),
            proxy_url=str(mapping.get("proxy_url") or "").strip(),
            notify_on_account_finish=bool(mapping.get("notify_on_account_finish", True)),
            notify_on_all_finish=bool(mapping.get("notify_on_all_finish", True)),
            notify_on_risk_alert=bool(mapping.get("notify_on_risk_alert", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# 常见测试/无效占位 ID 黑名单 (严禁作为默认私密 ID)
KNOWN_PLACEHOLDER_IDS = {
    "112233", "123456789", "987654321", "111222", "555666",
    "12345", "123456", "0", "", "none", "null", "undefined",
}


def is_valid_user_chat_id(val: Any) -> bool:
    """检查是否为真实有效的 Telegram 私人数字 ID."""
    s = str(val or "").strip()
    if not s or s.lower() in KNOWN_PLACEHOLDER_IDS:
        return False
    if not s.isdigit():
        return False
    if len(s) < 6 or len(s) > 15:
        return False
    return True


def get_local_active_proxy() -> str:
    """自动探测本机活跃代理端口 (支持 v2rayN / Clash / Shadowsocks 等)."""
    import socket
    candidates = [
        (10809, "http://127.0.0.1:10809"),    # v2rayN / Xray HTTP
        (10808, "socks5://127.0.0.1:10808"),  # v2rayN / Xray SOCKS5
        (7890,  "http://127.0.0.1:7890"),     # Clash / Clash Verge HTTP
        (7897,  "http://127.0.0.1:7897"),     # Clash Verge Rev HTTP
        (1080,  "socks5://127.0.0.1:1080"),   # Shadowsocks / 通用 SOCKS5
    ]
    for port, url in candidates:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return url
        except Exception:
            continue
    return ""


def _extract_valid_chat_id_from_file(path: Path) -> str:
    """从单个 JSON 配置文件中尝试提取有效的非占位 chat_id."""
    try:
        if not path.is_file():
            return ""
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
        if isinstance(data, dict):
            cid = data.get("chat_id")
            if is_valid_user_chat_id(cid):
                return str(cid).strip()
    except Exception:
        pass
    return ""


def find_local_cached_chat_id() -> str:
    """深度穿透扫描本机历史配置文件与备份，提取用户真实有效的 Telegram 私人 ID."""
    import os
    search_dirs = [
        Path.cwd(),
        Path(__file__).resolve().parent.parent,
        Path(os.environ.get("USERPROFILE", "C:/Users/Administrator")) / "Desktop",
        Path(os.environ.get("APPDATA", "C:/Users/Administrator/AppData/Roaming")),
    ]
    target_filenames = ["telegram_config.json", "tg_config.json"]
    visited_paths = set()

    for base_dir in search_dirs:
        if not base_dir.exists():
            continue
        # 1. 优先检查当前及一级 config 子目录
        for tf in target_filenames:
            for p in (base_dir / tf, base_dir / "config" / tf):
                try:
                    resolved = p.resolve()
                    if resolved not in visited_paths and p.is_file():
                        visited_paths.add(resolved)
                        cid = _extract_valid_chat_id_from_file(p)
                        if cid:
                            return cid
                except Exception:
                    pass

        # 2. 深度扫描历史备份与桌面发行包目录
        try:
            for entry in base_dir.iterdir():
                if not entry.is_dir():
                    continue
                entry_name = entry.name.lower()
                if any(k in entry_name for k in ("x_agent", "laogu", "control", "backup", "备份", "02_")):
                    for root, dirs, files in os.walk(str(entry)):
                        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__", "venv", ".venv")]
                        for f in files:
                            if f in target_filenames:
                                fp = Path(root) / f
                                try:
                                    resolved = fp.resolve()
                                    if resolved not in visited_paths and fp.is_file():
                                        visited_paths.add(resolved)
                                        cid = _extract_valid_chat_id_from_file(fp)
                                        if cid:
                                            return cid
                                except Exception:
                                    pass
        except Exception:
            pass
    return ""


import sys


class TelegramNotifier:
    """Telegram 战报推送引擎 (轻量级原生实现，支持代理与多租户分流)."""

    CONFIG_FILE_NAME = "telegram_config.json"

    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or Path.cwd()
        self.config_file = self.config_dir / self.CONFIG_FILE_NAME
        self.config = self.load_config()

    def load_config(self) -> TelegramConfig:
        """从本地加载 TG 配置 (支持多候选路径查找并自动继承本地真实私人ID)."""
        candidate_paths = [
            self.config_file,
            self.config_dir / "config" / self.CONFIG_FILE_NAME,
        ]
        project_root = Path(__file__).resolve().parent.parent
        candidate_paths.extend([
            project_root / self.CONFIG_FILE_NAME,
            project_root / "config" / self.CONFIG_FILE_NAME,
        ])
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).resolve().parent
            candidate_paths.extend([
                exe_dir / self.CONFIG_FILE_NAME,
                exe_dir / "config" / self.CONFIG_FILE_NAME,
            ])

        for path in candidate_paths:
            if path.is_file():
                try:
                    content = path.read_text(encoding="utf-8")
                    data = json.loads(content)
                    self.config_file = path
                    cfg = TelegramConfig.from_mapping(data)
                    # 关键增强：若 chat_id 无效或为占位符，自动继承本地真实有效 ID
                    if not is_valid_user_chat_id(cfg.chat_id):
                        cached_id = find_local_cached_chat_id()
                        if cached_id:
                            cfg.chat_id = cached_id
                            cfg.enabled = True
                            logger.info("Telegram 配置自动继承本地历史真实私人 ID 并默认启用: %s", cached_id)
                    # 关键增强：若 proxy_url 为空，自动感知本地活跃代理
                    if not cfg.proxy_url:
                        active_proxy = get_local_active_proxy()
                        if active_proxy:
                            cfg.proxy_url = active_proxy
                    return cfg
                except Exception as exc:
                    logger.warning("加载 Telegram 配置失败 (%s): %s", path, exc)

        # 默认无配置文件时，自动探测并预填本地真实 ID 与代理
        default_cfg = TelegramConfig()
        cached_id = find_local_cached_chat_id()
        if cached_id:
            default_cfg.chat_id = cached_id
            default_cfg.enabled = True
        active_proxy = get_local_active_proxy()
        if active_proxy:
            default_cfg.proxy_url = active_proxy
        return default_cfg

    def save_config(self, config: TelegramConfig) -> bool:
        """保存 TG 配置到本地."""
        self.config = config
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            self.config_file.write_text(json.dumps(config.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            alt_path = self.config_dir / "config" / self.CONFIG_FILE_NAME
            if alt_path.resolve() != self.config_file.resolve():
                try:
                    alt_path.parent.mkdir(parents=True, exist_ok=True)
                    alt_path.write_text(json.dumps(config.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    pass
            return True
        except Exception as exc:
            logger.error("保存 Telegram 配置失败: %s", exc)
            return False

    @staticmethod
    def _format_telegram_api_error(resp_text: str) -> str:
        """将 Telegram API 返回的错误转为清晰易懂的中文诊断."""
        desc = resp_text
        try:
            data = json.loads(resp_text)
            desc = str(data.get("description") or resp_text)
        except Exception:
            pass

        desc_lower = desc.lower()
        if "chat not found" in desc_lower:
            return (
                "Telegram 报错: 未找到该 Chat ID 对应的会话 (chat not found)。\n\n"
                "💡 解决办法：Telegram 机制规定机器人不能主动发起新会话。\n"
                "1. 请打开手机/电脑 Telegram，在搜索栏搜索官方通知机器人：@xchengxutz_bot\n"
                "2. 点击进入机器人对话框，点击底部的【Start】(开始) 或发送一条消息\n"
                "3. 回到控制中心再次点击【发送测试消息】即可成功！"
            )
        elif "unauthorized" in desc_lower:
            return "Telegram 报错: Bot Token 无效或未授权 (Unauthorized)。请检查自定义机器人 Token 是否复制正确。"
        elif "blocked" in desc_lower:
            return "Telegram 报错: 机器人已被该用户拉黑 (bot was blocked by the user)。请在 Telegram 中解除拉黑。"
        elif "chat_id is empty" in desc_lower:
            return "Telegram 报错: Chat ID 不能为空，请输入您的 Telegram 纯数字 ID。"
        return f"Telegram 报错: {desc}"

    @classmethod
    def _request_via_socks5(
        cls,
        proxy_url: str,
        path: str,
        method: str = "GET",
        json_data: Optional[dict[str, Any]] = None,
        timeout: int = 15,
    ) -> tuple[bool, Any, str]:
        """使用原生 Socket 实现标准 SOCKS5/SOCKS5h 传输执行通用 Telegram API 请求."""
        import socket
        import ssl

        norm_url = proxy_url if "://" in proxy_url else f"socks5://{proxy_url}"
        parsed = urllib.parse.urlparse(norm_url)
        proxy_host = parsed.hostname or "127.0.0.1"
        proxy_port = parsed.port or 10808
        username = parsed.username
        password = parsed.password

        try:
            sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
        except Exception as exc:
            return (
                False,
                {},
                f"无法连接到本地代理服务器 ({proxy_host}:{proxy_port}): {exc}。\n"
                "💡 提示：如果您在科学上网软件中开启了【TUN 虚拟网卡模式】，所有流量已自动代理，请直接将网络代理输入框【留空】再试！",
            )

        try:
            sock.settimeout(timeout)
            # 1. SOCKS5 握手认证协商 (RFC 1928 / RFC 1929)
            if username and password:
                sock.sendall(b"\x05\x02\x00\x02")
                auth_resp = sock.recv(2)
                if len(auth_resp) < 2 or auth_resp[0] != 5:
                    sock.close()
                    return False, {}, f"SOCKS5 握手失败: 代理端响应异常 ({auth_resp!r})"
                if auth_resp[1] == 2:
                    u_bytes = username.encode("utf-8")
                    p_bytes = password.encode("utf-8")
                    sock.sendall(b"\x01" + bytes([len(u_bytes)]) + u_bytes + bytes([len(p_bytes)]) + p_bytes)
                    auth_res = sock.recv(2)
                    if len(auth_res) < 2 or auth_res[1] != 0:
                        sock.close()
                        return False, {}, "SOCKS5 代理账号或密码认证失败"
                elif auth_resp[1] != 0:
                    sock.close()
                    return False, {}, f"SOCKS5 认证协商失败: 代理要求不支持的认证方式 ({auth_resp[1]})"
            else:
                sock.sendall(b"\x05\x01\x00")
                auth_resp = sock.recv(2)
                if len(auth_resp) < 2 or auth_resp != b"\x05\x00":
                    sock.close()
                    return (
                        False,
                        {},
                        f"SOCKS5 握手失败 (响应: {auth_resp!r})。\n"
                        f"提示：v2rayN 默认 SOCKS5 端口通常为 10808，HTTP 端口为 10809。请确认端口是否匹配。",
                    )

            # 2. SOCKS5 CONNECT 请求 (使用远程域名解析 SOCKS5h 协议，避开本地 DNS 污染)
            target_host = b"api.telegram.org"
            target_port = 443
            connect_req = (
                b"\x05\x01\x00\x03"
                + bytes([len(target_host)])
                + target_host
                + target_port.to_bytes(2, "big")
            )
            sock.sendall(connect_req)
            conn_resp = sock.recv(10)
            if len(conn_resp) < 4 or conn_resp[1] != 0:
                rep_code = conn_resp[1] if len(conn_resp) >= 2 else -1
                sock.close()
                return False, {}, f"SOCKS5 代理建立到 Telegram 链路失败 (错误码: {rep_code})，请检查代理节点是否通畅。"

            # 3. 升级 TLS 加密通道
            ssl_ctx = ssl.create_default_context()
            tls_sock = ssl_ctx.wrap_socket(sock, server_hostname="api.telegram.org")

            # 4. 发送 HTTP 请求
            if json_data is not None:
                payload = json.dumps(json_data).encode("utf-8")
                http_data = (
                    f"{method} {path} HTTP/1.1\r\n"
                    f"Host: api.telegram.org\r\n"
                    f"User-Agent: LaoguControlCenter/2026.1\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(payload)}\r\n"
                    f"Connection: close\r\n\r\n"
                ).encode("utf-8") + payload
            else:
                http_data = (
                    f"{method} {path} HTTP/1.1\r\n"
                    f"Host: api.telegram.org\r\n"
                    f"User-Agent: LaoguControlCenter/2026.1\r\n"
                    f"Connection: close\r\n\r\n"
                ).encode("utf-8")
            tls_sock.sendall(http_data)

            # 5. 接收完整响应
            chunks = []
            while True:
                chunk = tls_sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            tls_sock.close()

            raw_resp = b"".join(chunks).decode("utf-8", errors="replace")
            _, _, body_part = raw_resp.partition("\r\n\r\n")
            try:
                res = json.loads(body_part)
            except Exception:
                res = {}

            if res.get("ok"):
                return True, res.get("result", {}), "发送成功"
            return False, res, cls._format_telegram_api_error(body_part)

        except Exception as exc:
            try:
                sock.close()
            except Exception:
                pass
            return False, {}, f"SOCKS5 通信异常: {exc}"

    @classmethod
    def _send_via_socks5(
        cls,
        proxy_url: str,
        token: str,
        target_chat: str,
        text: str,
        parse_mode: str = "Markdown",
        timeout: int = 15,
    ) -> tuple[bool, str]:
        """使用原生 Socket 实现标准 SOCKS5/SOCKS5h 发送消息."""
        path = f"/bot{token}/sendMessage"
        payload = {
            "chat_id": target_chat,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        ok, _, msg = cls._request_via_socks5(
            proxy_url=proxy_url,
            path=path,
            method="POST",
            json_data=payload,
            timeout=timeout,
        )
        return ok, msg

    def send_raw_message(
        self,
        chat_id: str,
        text: str,
        bot_token: Optional[str] = None,
        proxy_url: Optional[str] = None,
        parse_mode: str = "Markdown",
    ) -> tuple[bool, str]:
        """同步发送 Telegram 消息 (支持直连、HTTP/HTTPS 代理以及原生 SOCKS5 代理)."""
        token = (bot_token or self.config.get_effective_token()).strip()
        target_chat = (chat_id or self.config.chat_id).strip()
        proxy = (proxy_url if proxy_url is not None else self.config.proxy_url).strip()

        if not token:
            return False, "Bot Token 不能为空"
        if not target_chat:
            return False, "接收者 Chat ID 不能为空"

        # 判断是否指定了 SOCKS5 协议
        proxy_lower = proxy.lower()
        is_socks = (
            proxy_lower.startswith("socks5://")
            or proxy_lower.startswith("socks5h://")
            or proxy_lower.startswith("socks://")
            or proxy_lower.startswith("socks4://")
            or (":10808" in proxy_lower and not proxy_lower.startswith("http"))
        )

        if is_socks:
            return self._send_via_socks5(
                proxy_url=proxy,
                token=token,
                target_chat=target_chat,
                text=text,
                parse_mode=parse_mode,
            )

        # 走标准 urllib (适用于直连、TUN模式、或 HTTP/HTTPS 代理)
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": target_chat,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "LaoguControlCenter/2026.1"},
        )

        handlers = []
        if proxy:
            norm_proxy = proxy if "://" in proxy else f"http://{proxy}"
            handlers.append(urllib.request.ProxyHandler({"http": norm_proxy, "https": norm_proxy}))
        opener = urllib.request.build_opener(*handlers)

        try:
            with opener.open(req, timeout=15) as resp:
                resp_text = resp.read().decode("utf-8")
                res = json.loads(resp_text)
                if res.get("ok"):
                    return True, "发送成功"
                return False, self._format_telegram_api_error(resp_text)
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            return False, self._format_telegram_api_error(body or str(exc))
        except urllib.error.URLError as exc:
            err_reason = str(exc.reason)
            logger.warning("Telegram 网络连接失败: %s", err_reason)
            if proxy:
                return (
                    False,
                    f"代理服务器连接失败: {err_reason}。\n"
                    "💡 提示：如果您在 v2rayN 中开启了【TUN 虚拟网卡模式】，请尝试将网络代理输入框【留空】再试！",
                )
            return (
                False,
                f"无法连接 Telegram 服务器: {err_reason}。\n"
                "💡 提示：请确保科学上网软件已开启并连接节点，或填写本地代理地址 (如 socks5://127.0.0.1:10808 或 http://127.0.0.1:10809)。",
            )
        except Exception as exc:
            err_msg = str(exc)
            logger.warning("Telegram 消息推送异常: %s", err_msg)
            return False, f"网络请求失败: {err_msg}"

    def _call_telegram_api(
        self,
        endpoint: str,
        params: Optional[dict[str, Any]] = None,
        json_data: Optional[dict[str, Any]] = None,
        bot_token: Optional[str] = None,
        proxy_url: Optional[str] = None,
        timeout: int = 15,
    ) -> tuple[bool, Any, str]:
        """统一调用 Telegram Bot API (支持直连、HTTP/HTTPS 及 SOCKS5 代理)."""
        token = (bot_token or self.config.get_effective_token()).strip()
        if not token:
            return False, {}, "Bot Token 不能为空"
        proxy = (proxy_url if proxy_url is not None else self.config.proxy_url).strip()

        # 判断是否走 SOCKS5 协议
        proxy_lower = proxy.lower()
        is_socks = (
            proxy_lower.startswith("socks5://")
            or proxy_lower.startswith("socks5h://")
            or proxy_lower.startswith("socks://")
            or proxy_lower.startswith("socks4://")
            or (":10808" in proxy_lower and not proxy_lower.startswith("http"))
        )

        path = f"/bot{token}/{endpoint}"
        if params:
            query = urllib.parse.urlencode(params)
            path = f"{path}?{query}"

        if is_socks:
            method = "POST" if json_data is not None else "GET"
            return self._request_via_socks5(
                proxy_url=proxy,
                path=path,
                method=method,
                json_data=json_data,
                timeout=timeout,
            )

        url = f"https://api.telegram.org{path}"
        headers = {"User-Agent": "LaoguControlCenter/2026.1"}
        body = None
        if json_data is not None:
            body = json.dumps(json_data).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=body, headers=headers)
        handlers = []
        if proxy:
            norm_proxy = proxy if "://" in proxy else f"http://{proxy}"
            handlers.append(urllib.request.ProxyHandler({"http": norm_proxy, "https": norm_proxy}))
        opener = urllib.request.build_opener(*handlers)

        try:
            with opener.open(req, timeout=timeout) as resp:
                resp_text = resp.read().decode("utf-8")
                res = json.loads(resp_text)
                if res.get("ok"):
                    return True, res.get("result", {}), "请求成功"
                return False, res, self._format_telegram_api_error(resp_text)
        except urllib.error.HTTPError as exc:
            body_err = ""
            try:
                body_err = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            return False, {}, self._format_telegram_api_error(body_err or str(exc))
        except urllib.error.URLError as exc:
            err_reason = str(exc.reason)
            logger.warning("Telegram API 网络连接失败: %s", err_reason)
            if proxy:
                return (
                    False,
                    {},
                    f"代理服务器连接失败: {err_reason}。\n"
                    "💡 提示：如果您在 v2rayN 中开启了【TUN 虚拟网卡模式】，请尝试将网络代理输入框【留空】再试！",
                )
            return (
                False,
                {},
                f"无法连接 Telegram 服务器: {err_reason}。\n"
                "💡 提示：请确保科学上网软件已开启并连接节点，或填写本地代理地址 (如 http://127.0.0.1:10809)。",
            )
        except Exception as exc:
            err_msg = str(exc)
            logger.warning("Telegram API 请求异常: %s", err_msg)
            return False, {}, f"网络请求失败: {err_msg}"

    def get_chat_info(
        self,
        chat_id: str,
        bot_token: Optional[str] = None,
        proxy_url: Optional[str] = None,
        timeout: int = 10,
    ) -> tuple[bool, dict[str, Any], str]:
        """向 Telegram API 查询指定 Chat ID 的用户真实详情 (用于验证账号真实性并获取用户名和昵称)."""
        clean_id = str(chat_id or "").strip()
        if not clean_id:
            return False, {}, "Chat ID 不能为空"
        token = (bot_token or self.config.get_effective_token()).strip()
        proxy = proxy_url if proxy_url is not None else self.config.proxy_url
        if not proxy:
            proxy = get_local_active_proxy()

        ok, res, msg = self._call_telegram_api(
            endpoint="getChat",
            params={"chat_id": clean_id},
            bot_token=token,
            proxy_url=proxy,
            timeout=timeout,
        )
        if ok and isinstance(res, dict):
            return True, res, "验证成功"
        return False, res if isinstance(res, dict) else {}, msg

    def fetch_recent_bot_updates(
        self,
        bot_token: Optional[str] = None,
        proxy_url: Optional[str] = None,
        timeout: int = 10,
    ) -> list[dict[str, Any]]:
        """获取最近与通知机器人产生会话的用户列表."""
        token = (bot_token or self.config.get_effective_token()).strip()
        proxy = proxy_url if proxy_url is not None else self.config.proxy_url
        if not proxy:
            proxy = get_local_active_proxy()

        ok, res, _ = self._call_telegram_api(
            endpoint="getUpdates",
            params={"limit": 30},
            bot_token=token,
            proxy_url=proxy,
            timeout=timeout,
        )
        if not ok or not isinstance(res, list):
            return []

        users: list[dict[str, Any]] = []
        seen_ids = set()
        for item in reversed(res):
            if not isinstance(item, dict):
                continue
            msg = item.get("message") or item.get("edited_message") or item.get("channel_post") or item.get("callback_query", {}).get("message") or {}
            from_u = msg.get("from") or {}
            u_id = from_u.get("id")
            if is_valid_user_chat_id(u_id) and str(u_id) not in seen_ids:
                seen_ids.add(str(u_id))
                users.append({
                    "chat_id": str(u_id),
                    "first_name": str(from_u.get("first_name") or "").strip(),
                    "last_name": str(from_u.get("last_name") or "").strip(),
                    "username": str(from_u.get("username") or "").strip(),
                })
        return users

    def auto_detect_chat_id(
        self,
        bot_token: Optional[str] = None,
        proxy_url: Optional[str] = None,
    ) -> dict[str, Any]:
        """全流程一键智能探测并验证本机 Telegram 私人 ID."""
        token = (bot_token or self.config.get_effective_token()).strip()
        proxy = (proxy_url if proxy_url is not None else self.config.proxy_url).strip()
        if not proxy:
            proxy = get_local_active_proxy()

        candidates: list[tuple[str, str]] = []

        # 1. 检查当前实例已配置的 chat_id (若非占位符)
        if is_valid_user_chat_id(self.config.chat_id):
            candidates.append((self.config.chat_id.strip(), "当前配置"))

        # 2. 深度穿透扫描本机历史文件
        local_cached_id = find_local_cached_chat_id()
        if local_cached_id and local_cached_id not in [c[0] for c in candidates]:
            candidates.append((local_cached_id, "本地历史配置文件"))

        # 逐个对候选 ID 进行云端身份校验
        for cand_id, src_label in candidates:
            ok, user_info, _ = self.get_chat_info(cand_id, bot_token=token, proxy_url=proxy, timeout=8)
            if ok and user_info:
                fn = str(user_info.get("first_name") or "").strip()
                ln = str(user_info.get("last_name") or "").strip()
                un = str(user_info.get("username") or "").strip()
                full_name = f"{fn} {ln}".strip() or fn or "Telegram 用户"
                display_title = f"{full_name} (@{un})" if un else full_name
                return {
                    "success": True,
                    "chat_id": cand_id,
                    "name": full_name,
                    "username": un,
                    "display_title": display_title,
                    "source": src_label,
                    "proxy_used": proxy,
                    "message": f"成功识别并验证本机专属账号：{display_title} (ID: {cand_id})",
                }

        # 3. 若候选 ID 均未在云端成功（或无候选 ID），尝试拉取机器人最新互动会话
        recent_users = self.fetch_recent_bot_updates(bot_token=token, proxy_url=proxy, timeout=8)
        if recent_users:
            latest = recent_users[0]
            cid = latest["chat_id"]
            fn = latest.get("first_name") or ""
            ln = latest.get("last_name") or ""
            un = latest.get("username") or ""
            full_name = f"{fn} {ln}".strip() or fn or "Telegram 用户"
            display_title = f"{full_name} (@{un})" if un else full_name
            return {
                "success": True,
                "chat_id": cid,
                "name": full_name,
                "username": un,
                "display_title": display_title,
                "source": "机器人最新会话",
                "proxy_used": proxy,
                "message": f"成功从机器人最新会话识别到账号：{display_title} (ID: {cid})",
            }

        # 4. 离线兜底：若网络不通或未连上节点，但本地找到过有效真实 ID
        if candidates:
            best_id, src_label = candidates[0]
            return {
                "success": True,
                "chat_id": best_id,
                "name": "本地已保存账号",
                "username": "",
                "display_title": f"本地私人专属 ID ({best_id})",
                "source": f"{src_label} (离线读取)",
                "proxy_used": proxy,
                "message": f"已从{src_label}读取到您的真实私人 ID: {best_id}（当前未能连接到 TG 云端核验昵称，已自动填入）",
            }

        # 5. 未能识别到任何 ID，给出清晰指引
        return {
            "success": False,
            "chat_id": "",
            "proxy_used": proxy,
            "message": (
                "未能自动检测到您的 Telegram 专属 ID。\n\n"
                "💡 快速绑定步骤：\n"
                "1. 打开 Telegram，搜索官方通知机器人：@xchengxutz_bot\n"
                "2. 点击底部的【Start】(开始) 或向机器人发送任意消息\n"
                "3. 回到此处再次点击【⚡ 自动获取本机飞机ID】即可瞬间完成绑定！"
            ),
        }

    def send_risk_alert(
        self,
        window_name: str,
        handle: str,
        error_code: int | str,
        error_message: str,
        auto_paused: bool = True,
    ) -> tuple[bool, str]:
        """向 Telegram 即时推送推特账号风控拦截警报 (包含窗口名称或账号 ID)."""
        if not self.config.enabled or not self.config.notify_on_risk_alert:
            return False, "Telegram 风控告警未开启"
        if not self.config.chat_id:
            return False, "未配置 Telegram Chat ID"

        text = self.build_risk_alert_report(
            window_name=window_name,
            handle=handle,
            error_code=error_code,
            error_message=error_message,
            auto_paused=auto_paused,
        )
        return self.send_raw_message(chat_id=self.config.chat_id, text=text)

    def send_network_alert(
        self,
        window_name: str,
        handle: str,
        error_message: str,
    ) -> tuple[bool, str]:
        """向 Telegram 即时推送代理网络断开/离线保活待机提醒 (包含窗口名称或账号 ID)."""
        if not self.config.enabled:
            return False, "Telegram 未开启"
        if not self.config.chat_id:
            return False, "未配置 Telegram Chat ID"

        text = self.build_network_alert_report(
            window_name=window_name,
            handle=handle,
            error_message=error_message,
        )
        return self.send_raw_message(chat_id=self.config.chat_id, text=text)

    # ================= 战报格式化模板 =================

    @staticmethod
    def build_test_message(chat_id: str) -> str:
        """构建连通性测试消息."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return (
            f"🔔 *【老谷控制中心】Telegram 通知配置成功！*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 *绑定用户 ID*：`{chat_id}`\n"
            f"⏰ *测试时间*：{now_str}\n"
            f"🎉 您的控制中心已成功与此机器人建立连通！当账号完成当日任务或手动导出时，战报将第一时间私密推送至此处。"
        )

    @staticmethod
    def _format_duration(duration_min: float) -> str:
        """格式化运行耗时为更易读的文本 (例如: 1 小时 48 分钟 (108.5 分钟))."""
        if duration_min <= 0:
            return "< 1 分钟"
        if duration_min < 1:
            secs = max(1, int(round(duration_min * 60)))
            return f"{secs} 秒"
        total_mins = int(duration_min)
        if total_mins < 60:
            return f"{duration_min:.1f} 分钟"
        hours = total_mins // 60
        mins = total_mins % 60
        if mins == 0:
            return f"{hours} 小时 ({duration_min:.1f} 分钟)"
        return f"{hours} 小时 {mins} 分钟 ({duration_min:.1f} 分钟)"

    @staticmethod
    def _safe_handle_display(handle: str) -> str:
        """安全格式化推特 handle，防止下划线等特殊字符破坏 Telegram Markdown 解析."""
        if not handle:
            return ""
        clean = str(handle).strip().lstrip("@")
        return f" (`@{clean}`)" if clean else ""

    @staticmethod
    def build_account_completion_report(
        account_name: str,
        handle: str,
        duration_min: float,
        likes: int,
        follows: int,
        scanned: int,
        initial_followers: int = 0,
        final_followers: int = 0,
        initial_following: int = 0,
        final_following: int = 0,
        status: str = "COMPLETED",
    ) -> str:
        """构建单账号当日完工喜报 / 阶段成果通知."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        handle_display = TelegramNotifier._safe_handle_display(handle)
        dur_str = TelegramNotifier._format_duration(duration_min)

        norm_status = str(status or "").upper()
        if norm_status == "CANCELLED":
            title = "🎯 *【老谷控制中心】账号任务阶段成果通知*"
            status_line = "🏁 *任务状态*：⏸️ 已人工中止 · 阶段数据已入库保留"
            goal_tag = "（人工中止）"
        elif norm_status in {"SUCCESS", "COMPLETED"}:
            title = "🎯 *【老谷控制中心】账号任务达成通知*"
            status_line = "🏁 *任务状态*：✅ 当日目标达成 · 已安全切回首页消痕"
            goal_tag = "（达成配置目标）"
        else:
            title = "🎯 *【老谷控制中心】账号任务执行结算*"
            status_line = f"🏁 *任务状态*：{status}"
            goal_tag = ""

        # 仅当真正抓取到真实非零资产变动数据时才展示，彻底去除 0 ➔ 0 (持平) 等冗余展示
        net_followers = final_followers - initial_followers
        net_following = final_following - initial_following
        asset_block = ""
        if (initial_followers > 0 or final_followers > 0 or initial_following > 0 or final_following > 0) and (
            net_followers != 0 or net_following != 0
        ):
            f_sign = f"净增 +{net_followers} 🚀" if net_followers > 0 else (f"净减 {net_followers}" if net_followers < 0 else "持平")
            ing_sign = f"净增 +{net_following}" if net_following > 0 else (f"净减 {net_following}" if net_following < 0 else "持平")
            asset_block = (
                f"\n📈 *账号粉丝资产动态*：\n"
                f"• 👥 *粉丝量*：`{initial_followers}` ➔ `{final_followers}` (*{f_sign}*)\n"
                f"• ➕ *关注量*：`{initial_following}` ➔ `{final_following}` (*{ing_sign}*)\n"
            )

        return (
            f"{title}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 *执行账号*：`{account_name}`{handle_display}\n"
            f"{status_line}\n"
            f"⏱️ *运行耗时*：{dur_str}\n"
            f"⏰ *完成时间*：{now_str}\n\n"
            f"📊 *今日自动化执行成果*：\n"
            f"• ➕ 今日新增关注：*{follows}* 人{goal_tag}\n"
            f"• 👍 今日推文点赞：*{likes}* 次\n"
            f"• 🔍 扫描博主/推文：*{scanned}* 位\n"
            f"{asset_block}"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"ℹ️ _由老谷控制中心 2026 自动推送_"
        )

    @staticmethod
    def build_summary_report(accounts_data: list[dict[str, Any]]) -> str:
        """构建大盘全盘汇总战报."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        total_accounts = len(accounts_data)
        total_follows = sum(int(a.get("follows", 0)) for a in accounts_data)
        total_likes = sum(int(a.get("likes", 0)) for a in accounts_data)
        total_scanned = sum(int(a.get("scanned", 0)) for a in accounts_data)
        total_net_fans = sum(int(a.get("final_followers", 0)) - int(a.get("initial_followers", 0)) for a in accounts_data)

        lines = [
            f"📋 *【老谷控制中心】全盘运行汇总战报*",
            f"━━━━━━━━━━━━━━━━━━",
            f"📅 *统计时间*：{now_str}",
            f"💻 *活跃窗口*：共 {total_accounts} 个账号窗口",
            f"",
            f"📊 *全局今日累计产出*：",
            f"• 今日累计关注：*{total_follows}* 人",
            f"• 今日累计点赞：*{total_likes}* 次",
            f"• 今日扫描总量：*{total_scanned}* 位",
        ]

        if total_net_fans != 0:
            fans_sign = f"+{total_net_fans}" if total_net_fans > 0 else str(total_net_fans)
            lines.append(f"• 全局粉丝净增：*{fans_sign}* 粉丝")

        lines.append("")
        lines.append("📋 *各账号明细*：")

        if not accounts_data:
            lines.append("（当前暂无正在运行或完成的账号数据）")
        else:
            for idx, acc in enumerate(accounts_data, 1):
                name = acc.get("name", f"账号: {idx}")
                raw_h = str(acc.get("handle") or "").strip().lstrip("@")
                handle = f" (`@{raw_h}`)" if raw_h else ""
                f_cnt = int(acc.get("follows", 0))
                l_cnt = int(acc.get("likes", 0))
                s_cnt = int(acc.get("scanned", 0))
                init_f = int(acc.get("initial_followers", 0))
                fin_f = int(acc.get("final_followers", 0))
                diff = fin_f - init_f

                parts = [f"关注 +{f_cnt}", f"点赞 {l_cnt}"]
                if s_cnt > 0:
                    parts.append(f"扫描 {s_cnt}")
                if diff != 0:
                    diff_str = f"+{diff}" if diff > 0 else str(diff)
                    parts.append(f"粉丝 `{init_f}`➔`{fin_f}` ({diff_str})")

                lines.append(f"{idx}. *[{name}]*{handle} " + " | ".join(parts))

        lines.append("━━━━━━━━━━━━━━━━━━")
        lines.append("ℹ️ _由老谷控制中心 2026 自动生成并推送_")
        return "\n".join(lines)

    @staticmethod
    def build_risk_alert_report(
        window_name: str,
        handle: str,
        error_code: int | str,
        error_message: str,
        auto_paused: bool = True,
    ) -> str:
        """构建推特底层风控拦截报警通知 (支持窗口名称与账号ID)."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        handle_display = f"@{handle}" if handle else "未抓取或未登录"
        safe_msg = str(error_message or "").replace("`", "'")
        action_text = (
            "🔴 *已触发底层自动熔断*：当前账号任务已自动安全暂停，防止进一步被风控封号！"
            if auto_paused
            else "🟡 *未暂停（用户已关闭熔断）*：当前任务保持继续运行，请注意观察账号安全！"
        )
        return (
            f"🚨 *【老谷控制中心】推特风控拦截紧急告警*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💻 *窗口名称*：`{window_name}`\n"
            f"👤 *推特账号*：`{handle_display}`\n"
            f"⚠️ *拦截错误码*：`Code {error_code}`\n"
            f"💬 *官方提示信息*：`{safe_msg}`\n"
            f"⏰ *告警时间*：{now_str}\n\n"
            f"🛡️ *当前处置状态*：\n"
            f"{action_text}\n\n"
            f"💡 *排查建议*：\n"
            f"• Code 226/399：推特判定自动化或要求验证码挑战，建议静置数小时或在浏览器中手动完成人机验证\n"
            f"• Code 326/64：账号已被临时锁定或冻结，请在独立窗口中绑定手机/申诉\n"
            f"• Code 141/185：达到推特单日操作硬上限，建议次日再跑\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"ℹ️ _由老谷控制中心推特防御系统第一时间自动告警_"
        )

    @staticmethod
    def build_network_alert_report(
        window_name: str,
        handle: str,
        error_message: str,
    ) -> str:
        """构建代理网络断网保活待机通知."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        handle_display = f"@{handle}" if handle else "未抓取或未登录"
        safe_msg = str(error_message or "").replace("`", "'")
        return (
            f"🌐 *【老谷控制中心】代理网络断开提醒*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💻 *窗口名称*：`{window_name}`\n"
            f"👤 *推特账号*：`{handle_display}`\n"
            f"⚠️ *网络状态*：`{safe_msg}`\n"
            f"⏰ *发生时间*：{now_str}\n\n"
            f"💡 *运行状态*：*已自动进入保活待机（未退出浏览器）*\n"
            f"👉 *处理建议*：请检查代理软件（Clash / v2ray / 老谷代理池）节点连通性，网络恢复后引擎将自动继续推进！\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"ℹ️ _由老谷控制中心网络守护系统自动提醒_"
        )

