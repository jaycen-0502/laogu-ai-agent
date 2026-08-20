# Stage 10A：AI任务提案与确认部署说明

## 目标

10A 增加独立的“AI任务”入口。用户输入自然语言要求后，AI 只从当前工作区已启用脚本和 Profile 中生成提案；服务端重新校验脚本、不可变版本、Profile 路由和参数。用户明确确认后，才创建 `script.execute` Task，Windows Agent 才能拉取执行。

AI 不会接收脚本源码，也不能生成或直接执行任意脚本。拒绝、确认、失败和提案均写入审计记录。

## 更新内容

- 应用版本：`0.10.0`
- 数据库迁移：`0008_ai_writing -> 0009_ai_task_proposals`
- 新表：`ai_task_proposals`
- 新路由：
  - `GET /api/ai/task-proposals`
  - `POST /api/ai/task-proposals`
  - `GET /api/ai/task-proposals/{proposal_id}`
  - `POST /api/ai/task-proposals/{proposal_id}/confirm`
  - `POST /api/ai/task-proposals/{proposal_id}/reject`
- Web入口：`/ai/tasks`

## 部署前

1. 先停止服务并备份 PostgreSQL、源码、Web、配置和图片目录。
2. 校验更新包 SHA256，并检查 tar 路径不得包含绝对路径或 `..`。
3. 在临时目录运行 Python 语法检查。

## 部署顺序

1. 解压更新包并按现有部署规范设置 `root:laogu` 与 Web 权限。
2. 执行 `.venv/bin/alembic upgrade head`。
3. 以 `laogu` 用户安装 Web 依赖并执行 `npm run build`，发布 `web/dist`。
4. 启动 `laogu-server`。

## 验收

- `systemctl is-active laogu-server` 返回 `active`。
- `/api/health` 与 `/api/health/ready` 返回 `{"ok":true}`。
- `alembic current` 为 `0009_ai_task_proposals (head)`。
- OpenAPI 包含上述 5 个 AI任务路由，版本为 `0.10.0`。
- Web 可读取 `ai_tasks-*.js`。
- 新建提案后数据库中没有新增 Task；只有点击确认后才出现 `script.execute` Task。
- 拒绝提案后不能再次确认；脚本被禁用、版本不匹配、Profile 不属于工作区或参数不符合 `params_schema` 时确认失败且不创建 Task。

## 回滚

停止服务后使用部署前源码和数据库备份恢复；如需回滚迁移，执行 `.venv/bin/alembic downgrade 0008_ai_writing`，确认后再启动服务。Web 静态文件同步恢复对应备份目录。
