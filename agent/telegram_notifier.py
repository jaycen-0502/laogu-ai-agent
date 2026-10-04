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


import sys


class TelegramNotifier:
    """Telegram 战报推送引擎 (轻量级原生实现，支持代理与多租户分流)."""

    CONFIG_FILE_NAME = "telegram_config.json"

    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or Path.cwd()
        self.config_file = self.config_dir / self.CONFIG_FILE_NAME
        self.config = self.load_config()

    def load_config(self) -> TelegramConfig:
        """从本地加载 TG 配置 (支持多候选路径查找)."""
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
                    return TelegramConfig.from_mapping(data)
                except Exception as exc:
                    logger.warning("加载 Telegram 配置失败 (%s): %s", path, exc)

        return TelegramConfig()

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
    def _send_via_socks5(
        cls,
        proxy_url: str,
        token: str,
        target_chat: str,
        text: str,
        parse_mode: str = "Markdown",
        timeout: int = 15,
    ) -> tuple[bool, str]:
        """使用原生 Socket 实现标准 SOCKS5/SOCKS5h 传输 (无需第三方依赖，支持远程域名解析防污染)."""
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
                f"无法连接到本地代理服务器 ({proxy_host}:{proxy_port}): {exc}。\n"
                "💡 提示：如果您在 v2rayN 中开启了【TUN 虚拟网卡模式】，所有流量已自动代理，请直接将网络代理输入框【留空】再试！",
            )

        try:
            sock.settimeout(timeout)
            # 1. SOCKS5 握手认证协商 (RFC 1928 / RFC 1929)
            if username and password:
                sock.sendall(b"\x05\x02\x00\x02")
                auth_resp = sock.recv(2)
                if len(auth_resp) < 2 or auth_resp[0] != 5:
                    sock.close()
                    return False, f"SOCKS5 握手失败: 代理端响应异常 ({auth_resp!r})"
                if auth_resp[1] == 2:
                    u_bytes = username.encode("utf-8")
                    p_bytes = password.encode("utf-8")
                    sock.sendall(b"\x01" + bytes([len(u_bytes)]) + u_bytes + bytes([len(p_bytes)]) + p_bytes)
                    auth_res = sock.recv(2)
                    if len(auth_res) < 2 or auth_res[1] != 0:
                        sock.close()
                        return False, "SOCKS5 代理账号或密码认证失败"
                elif auth_resp[1] != 0:
                    sock.close()
                    return False, f"SOCKS5 认证协商失败: 代理要求不支持的认证方式 ({auth_resp[1]})"
            else:
                sock.sendall(b"\x05\x01\x00")
                auth_resp = sock.recv(2)
                if len(auth_resp) < 2 or auth_resp != b"\x05\x00":
                    sock.close()
                    return (
                        False,
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
                return False, f"SOCKS5 代理建立到 Telegram 链路失败 (错误码: {rep_code})，请检查代理节点是否通畅。"

            # 3. 升级 TLS 加密通道
            ssl_ctx = ssl.create_default_context()
            tls_sock = ssl_ctx.wrap_socket(sock, server_hostname="api.telegram.org")

            # 4. 发送 HTTP POST 请求
            payload = json.dumps({
                "chat_id": target_chat,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }).encode("utf-8")

            http_data = (
                f"POST /bot{token}/sendMessage HTTP/1.1\r\n"
                f"Host: api.telegram.org\r\n"
                f"User-Agent: LaoguControlCenter/2026.1\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(payload)}\r\n"
                f"Connection: close\r\n\r\n"
            ).encode("utf-8") + payload
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
                return True, "发送成功"
            return False, cls._format_telegram_api_error(body_part)

        except Exception as exc:
            try:
                sock.close()
            except Exception:
                pass
            return False, f"SOCKS5 通信异常: {exc}"

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

