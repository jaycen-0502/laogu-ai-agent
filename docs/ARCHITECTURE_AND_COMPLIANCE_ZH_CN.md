# v0.21.6 架构落地与安全合规基线

本文件是产品工程控制基线，不等同于 OpenAI、平台方或任何司法辖区的合规认证；上线前仍需结合实际业务、用户协议、隐私政策和适用法律进行人工审核。

## 运行边界

服务器只负责用户、工作区、脚本、任务批次、权限、审计和结果存储；Windows 桌面控制台负责启动和停止同一进程内的 Agent；Agent 通过 HTTPS/WebSocket 调用服务器并通过本机 Laogu Browser API 执行任务。桌面控制台关闭时，Agent 生命周期随窗口关闭而停止，不需要用户单独启动 PowerShell 或 `python -m agent.service_main`。

Agent 凭据只保存为 Windows DPAPI 保护文件，并绑定设备 ID。服务器端只保存哈希、状态和审计元数据；日志、结果和错误在回传前执行脱敏。

## 安全门禁

- 仅允许现有只读任务类型和经过静态校验的脚本。
- 拒绝 Cookie、密码、会话/访问 Token 导出，验证码或安全绕过，批量私信/垃圾信息、冒充和隐私侵入等高风险自动化。
- AI 任务必须经过功能权限、模型白名单、请求限流和审计；会产生外部副作用的任务必须由用户显式确认。
- 不在仓库、发布包、备份或健康接口中放置生产密钥、Token、Cookie、数据库连接串或 Telegram 凭据。

## 升级与回滚

发布前执行 `python scripts/verify_release.py deploy/updates/release-manifest-v0.21.6.json`。生产升级顺序固定为：备份数据库与配置 → 校验发布包 → 安装代码 → `alembic upgrade head` → 重启服务 → 检查 `/api/health` 与 `/api/health/ready` → 失败则回滚代码和数据库备份。Windows 包升级后重新启动桌面控制台，由内置 Agent 自动重新连接；不要并行运行独立 Agent 进程。
