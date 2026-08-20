# Stage 10B：统一控制中心第一版部署说明

## 目标

10B 增加只读的统一控制中心，用于在一个页面查看当前工作区的 Workspace、Agent、Profile、Account、Task、Activity、Script 和 AI 状态，并可打开单个 Profile 的关联任务与活动详情。

10B 是聚合展示层，不新增数据库表，不改变现有 Task Pull 或 Agent 执行协议，也不直接控制浏览器。

## 更新内容

- 应用版本：`0.11.0`
- 数据库迁移：无（无需执行 Alembic upgrade）
- 新增后端模块：`server/control_api.py`
- 新增接口：
  - `GET /api/control/overview?recent_limit=20`
  - `GET /api/control/profiles/{profile_record_id}`
  - `GET /api/control/timeline?limit=50`
- Web 入口：`/command-center`
- 前端资源：`control_center-*.js`

## 权限与隔离

- `ADMIN` 可查看所有 Workspace 的聚合数据。
- 其他登录用户只能查看自己所属 Workspace 的数据。
- Profile 详情会再次校验 Workspace 归属，不属于当前 Workspace 时返回 `404`。
- 接口仅查询已有数据，不创建 Task、不确认或拒绝 AI 提案、不执行脚本、不控制 Browser。
- 不展示 Cookie、Credential 或其他敏感密钥内容。

## 部署前检查

1. 先备份 PostgreSQL、源码、Web `dist`、配置、systemd 服务文件和已生成图片。
2. 校验 `/tmp/stage10b-update.tar.gz` 的 SHA256，并确认压缩包不包含绝对路径或 `..` 路径。
3. 在临时目录执行 Python 语法检查和测试。

## 部署步骤

1. 解压更新包到 `/opt/laogu-ai-agent`，按现有规范设置 `root:laogu` 后端权限及 `laogu:laogu` Web 权限。
2. 在 `/opt/laogu-ai-agent/web` 以 `laogu` 用户执行 `npm ci` 和 `npm run build`，发布新的 `web/dist`。
3. 无需执行数据库迁移。
4. 重启 `laogu-server`，然后检查健康状态。

## 验收

```bash
systemctl is-active laogu-server
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/api/health/ready
curl -fsS http://127.0.0.1:8000/openapi.json
curl -fsSI https://api.jaycwl.org/assets/control_center-<hash>.js
```

预期结果：

- 服务状态为 `active`。
- 两个健康接口均返回 `{"ok":true}`。
- OpenAPI 的版本为 `0.11.0`，并包含上述 3 个 `/api/control` 路由。
- Web 资源返回 `200 OK`，登录后台后可访问 `/command-center`。
- 控制中心能够显示当前 Workspace 范围内的统计、Agent、Profile/Account、任务、活动和审计信息；点击 Profile 可查看详情。
- 数据库版本保持 `0009_ai_task_proposals`，不应出现新的 10B 迁移。

## 回滚

停止服务后恢复部署前的源码、Web `dist`、配置和服务文件，再启动 `laogu-server`。由于 10B 无数据库迁移，不需要执行数据库 downgrade；如需恢复数据库，使用部署前的 PostgreSQL 备份。

## 明确不包含的能力

10B 不包含 WebSocket 实时推送、ProfileWorker、Command、Cookie/Credential 管理、浏览器控制、任意命令执行或新的 Agent 协议。这些能力应在后续阶段单独设计、评审和验收。
