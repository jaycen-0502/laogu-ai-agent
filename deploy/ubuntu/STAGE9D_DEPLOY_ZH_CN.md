# Stage 9D：AI话术分析与回复生成部署

## 功能范围

- 话术分析：识别原文意图、情绪、语气、重点、风险和建议回复策略。
- 回复生成：按目标、语气、语言、候选数量、字符上限和品牌人设生成草稿。
- 服务端强制执行候选数量、字符上限、去重和敏感信息脱敏。
- 历史记录、详情、复制和删除。
- 工作区与用户隔离，Token、耗时、失败原因和审计记录。

回复仅为候选草稿，参数中固定保存：

```text
requires_human_review=true
auto_publish=false
```

Stage 9D不包含自动发布、自动调用脚本或自动创建任务；这些能力属于Stage 10A。

## 部署基线

- 部署前应用版本：`0.9.3`
- 部署前数据库版本：`0007_ai_analysis`
- 部署后应用版本：`0.9.4`
- 部署后数据库版本：`0008_ai_writing`

## 部署顺序

1. 使用Stage 9C最终备份作为部署前恢复点，或额外创建Stage 9D部署前备份。
2. 核对更新包SHA256、文件列表和危险路径。
3. 解压更新包并设置Python文件为`root:laogu 0644`、Web文件为`laogu:laogu 0644`。
4. 执行Python语法、服务导入和迁移头检查。
5. 以`laogu`用户执行`npm ci`和`npm run build`。
6. 停止服务、执行`.venv/bin/alembic upgrade head`并重新启动。
7. 验证版本、路由、数据库表、Web资源和健康状态。

## 预期API

- `GET /api/ai/writing`
- `POST /api/ai/writing/analyze`
- `POST /api/ai/writing/replies`
- `GET /api/ai/writing/{record_id}`
- `DELETE /api/ai/writing/{record_id}`

## 可选配置

```text
LAOGU_RATE_LIMIT_AI_WRITING=10
```

默认每个来源IP每分钟最多发起10次话术请求。

## 验收要求

- `alembic current`为`0008_ai_writing (head)`。
- OpenAPI版本为`0.9.4`并包含全部话术路由。
- Web存在并可读取`ai_writing-*.js`。
- 话术分析和回复生成各成功一次。
- 回复数量不超过请求数量，每条不超过设置字符数。
- 数据库参数显示`requires_human_review=true`、`auto_publish=false`。
- 审计包含`AI_WRITING_ANALYZED`和`AI_REPLIES_GENERATED`。
- 本机和公网健康检查正常。
