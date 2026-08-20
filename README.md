# Laogu AI Agent

老谷系统由 Ubuntu 协调服务器、React 管理后台和 Windows Agent 组成。

当前正式版本：`0.18.0`

## 生产结构

```text
Internet
  -> Nginx :443
      -> React 管理后台
      -> FastAPI 127.0.0.1:8000
          -> PostgreSQL

Windows Agent
  -> HTTPS / WebSocket
      -> Ubuntu 协调服务器
```

Laogu Browser 及 Windows Agent 只在 Windows 运行，不安装到 Ubuntu 服务器。

## 全新服务器部署

完整教程见：

- [Ubuntu 24.04 一键部署与灾备恢复](deploy/ubuntu/ONE_CLICK_DEPLOY_ZH_CN.md)

全新安装的入口命令：

```bash
sudo bash deploy/ubuntu/install.sh --domain api.example.com
```

该脚本只允许在全新 Ubuntu 24.04 服务器执行，并拒绝覆盖已有生产配置或数据库。

故障恢复入口命令：

```bash
sudo bash deploy/ubuntu/restore.sh
```

私有仓库需要先给备用服务器配置 GitHub 只读 Deploy Key；恢复向导会自动寻找
`/root/restore` 下的加密备份，逐步检查并恢复现有生产数据。

## 灾备

- 每日使用 age 公钥加密 PostgreSQL、精简源码、应用数据和生产配置；
- 加密恢复包上传 Telegram；
- 本机自动备份保留 7 天；
- 每周检查磁盘、服务、数据库和备份状态；
- 私钥不提交 GitHub，也不包含在恢复包中。

## 安全

本仓库不保存 `.env`、生产配置、数据库、日志、Agent 凭证、Telegram Token、
age 私钥或 TLS 私钥。生产仓库应保持 Private，并使用只读 Deploy Key 部署。
