# Stage 9C：AI账号与关键词分析部署

## 功能范围

- 账号运行健康分析：登录、浏览器、账号状态和自动化活动统计。
- 关键词分析：用户粘贴文本，可选附加指定账号的活动数据。
- 服务端确定性关键词计数，AI只解释语境和给出建议。
- 分析历史、详情和删除。
- 工作区、用户权限隔离，敏感字段入库前脱敏。
- Token、耗时、错误和审计记录。

当前账号数据不包含X帖子、粉丝、曝光或互动指标。分析结果会明确标注数据限制，不得将运行健康分析解释为内容表现分析。

## 部署基线

- 应用版本：`0.9.2`
- 数据库版本：`0006_image_center`
- Stage 9C部署后应用版本：`0.9.3`
- Stage 9C部署后数据库版本：`0007_ai_analysis`

## 部署顺序

1. 完成PostgreSQL、源码、Web和配置备份。
2. 检查更新包SHA256和文件列表。
3. 解压更新包并设置Python文件为`root:laogu 0644`、Web文件为`laogu:laogu 0644`。
4. 以`laogu`用户运行Python语法和服务导入检查。
5. 以`laogu`用户执行`npm ci`和`npm run build`。
6. 停止`laogu-server`，执行`.venv/bin/alembic upgrade head`，然后启动服务。
7. 检查应用版本、路由、`ai_analyses`表、本机及公网健康状态。

## 预期API

- `GET /api/ai/analysis`
- `POST /api/ai/analysis/account`
- `POST /api/ai/analysis/keywords`
- `GET /api/ai/analysis/{analysis_id}`
- `DELETE /api/ai/analysis/{analysis_id}`

## 可选配置

```text
LAOGU_RATE_LIMIT_AI_ANALYSIS=10
```

默认每个来源IP每分钟最多发起10次分析。

## 验收要求

- `alembic current`输出`0007_ai_analysis (head)`。
- OpenAPI版本为`0.9.3`且包含全部分析路由。
- Web首页引用`ai_analysis-*.js`资源且资源返回HTTP 200。
- 账号分析成功，结果明确显示当前样本量和数据限制。
- 关键词计数与输入文本实际出现次数一致。
- `ai_analyses`保存SUCCESS/FAILED、Token和耗时。
- 审计包含`AI_ACCOUNT_ANALYZED`、`AI_KEYWORDS_ANALYZED`或失败记录。
- 本机与公网健康检查均返回`{"ok":true}`。
