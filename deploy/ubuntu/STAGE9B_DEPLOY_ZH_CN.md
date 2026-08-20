# 第9B阶段：AI聊天与独立生图入口生产部署

本阶段部署AI聊天中心及独立AI生图入口，不进入9C。执行环境：Ubuntu项目目录 `/opt/laogu-ai-agent`，服务域名 `https://api.jaycwl.org`。

## 1. 部署前备份

先备份PostgreSQL、源码、Web构建、Nginx、systemd和环境文件。环境文件备份必须保持 `600` 权限，禁止打印其中的密钥。

## 2. 安装更新包

上传 `stage9b-update.tar.gz` 到服务器 `/tmp/`，先检查SHA256和压缩包路径，再解压到 `/opt/laogu-ai-agent`。服务端Python文件建议使用 `root:laogu 0644`，Web目录使用 `laogu:laogu`。

## 3. 部署前检查

使用 `laogu` 身份加载 `/etc/laogu/server.env`，执行：

```bash
.venv/bin/python -c 'import server.main; print("SERVER_IMPORT_OK", server.main.app.version)'
.venv/bin/alembic heads
.venv/bin/alembic current -v
```

扩展包部署前，生产数据库应为 `0005_chat_center`；安装扩展包后，预期代码头版本为 `0006_image_center`。

## 4. 构建Web

```bash
sudo chown -R laogu:laogu /opt/laogu-ai-agent/web
sudo mkdir -p /opt/laogu-ai-agent/.npm
sudo chown -R laogu:laogu /opt/laogu-ai-agent/.npm
sudo -u laogu env npm_config_cache=/opt/laogu-ai-agent/.npm \
  bash -c 'cd /opt/laogu-ai-agent/web && npm ci --no-audit --no-fund && npm run build'
```

## 5. 数据库迁移与重启

先创建不公开的图片存储目录：

```bash
sudo mkdir -p /opt/laogu-ai-agent/data/ai-images
sudo chown -R laogu:laogu /opt/laogu-ai-agent/data
sudo chmod 750 /opt/laogu-ai-agent/data /opt/laogu-ai-agent/data/ai-images
```

由于systemd单元启用了 `ProtectSystem=strict`，还必须把图片目录加入服务的可写白名单。推荐创建drop-in配置：

```bash
sudo install -d -m 0755 /etc/systemd/system/laogu-server.service.d
printf '%s\n' '[Service]' 'ReadWritePaths=/opt/laogu-ai-agent/data/ai-images' \
  | sudo tee /etc/systemd/system/laogu-server.service.d/ai-images.conf >/dev/null
sudo systemctl daemon-reload
```

停止 `laogu-server`，以 `laogu` 身份加载环境变量后执行 `.venv/bin/alembic upgrade head`，再启动服务。迁移失败时立即重新启动旧服务并停止后续操作。

## 6. 基础验收

检查：

- `systemctl status laogu-server`
- `alembic current -v` 为 `0006_image_center (head)`
- `chat_sessions`、`chat_messages`、`ai_usage`、`ai_images` 四张表存在
- 本机和公网 `/api/health`、`/api/health/ready` 返回 `{"ok":true}`
- Web构建中存在 `ai_chat-*.js` 和 `ai_images-*.js`

## 7. Provider配置

在Web的“AI 服务商”中配置中转站：

- 类型：OpenAI兼容接口
- Base URL：中转站实际提供的HTTPS地址；当前xfastapi使用 `https://xfastapi.ai`，不要擅自添加 `/v1`
- 模型：`gpt-5.6-sol`
- 状态：启用
- 默认Provider：是

API Key只在Web页面输入，不要粘贴到聊天、终端输出或验收记录。

聊天调用优先使用 `/responses`；中转站明确返回 `404/405` 时，服务端自动回退 `/chat/completions`。

## 8. 真实端到端验收

1. 登录Web并打开 `/ai/chat`。
2. 新建会话。
3. 发送 `Reply with exactly: OK`，确认真实返回 `OK`。
4. 再发送 `What did I just ask you?`，确认多轮上下文正确。
5. 发送较长请求并点击“停止生成”，确认assistant状态为 `CANCELLED`。
6. 检查 `ai_usage` 中Provider、模型、Token、延迟和状态。
7. 检查审计日志中的会话创建、成功、停止/取消记录。

真实结果必须记录Provider名称、模型、HTTP结果、实际响应、Token Usage和Latency；Mock结果不能作为生产验收。

## 9. 独立AI生图验收

1. 登录Web并打开 `/ai/images`。
2. 确认Provider仍使用文字聊天的默认模型 `gpt-5.6-sol`，生图页面固定调用 `gpt-image-2`，两者互不覆盖。
3. 先生成一张1K、中等质量图片；请求路径为当前Provider Base URL直接追加 `/images/generations`。
4. 验证图片预览、下载、刷新后仍存在，并确认另一用户无法读取图片内容。
5. 2K会产生更高费用，只在明确需要时真实验收。
6. 检查 `ai_images` 中状态、尺寸、质量、Token、耗时以及审计记录 `AI_IMAGE_GENERATED`。

图片保存在 `/opt/laogu-ai-agent/data/ai-images`，该目录不得通过Nginx直接公开，只能通过登录鉴权API读取。可用环境变量 `LAOGU_AI_IMAGE_STORAGE_PATH` 修改路径；默认无需配置。

## 10. 部署后备份

验收完成后再次备份数据库、最终源码、Web构建和图片存储目录。到此停止，不进入9C。
