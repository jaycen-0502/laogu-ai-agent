# 远程授权模式部署说明

本阶段保留现有 `LGREQ1` / `LGACT1` 离线激活。服务器只验签并保存激活码哈希、授权元数据、设备哈希和检查记录，不保存 Cookie、密码、Token 或浏览器用户数据。

## 1. 配置服务器公钥

把浏览器源码 `backend/license_public_key.go` 中的 `offlineLicenseIssuerPublicKey` 值复制到服务器环境文件；这是 Ed25519 公钥，可以公开，绝对不要复制 `Laogu-License-Issuer.key` 私钥。

```bash
sudoedit /etc/laogu/server.env
# 增加一行（填入实际公钥）
LAOGU_LICENSE_ISSUER_PUBLIC_KEY=...
```

## 2. 先备份，再迁移

```bash
sudo -u postgres pg_dump -Fc -d laogu > /var/backups/laogu/laogu-before-remote-license.dump
cd /opt/laogu-ai-agent
sudo -u laogu bash -c '
  set -a; . /etc/laogu/server.env; set +a
  .venv/bin/alembic current
  .venv/bin/alembic upgrade head
  .venv/bin/alembic current
'
```

预期迁移头为 `0013_remote_licenses`。如果当前代码版本仍是生产旧版本，不要运行迁移，先安装同一版本源码。

## 3. 重启和验证

```bash
sudo systemctl restart laogu-server
sudo systemctl is-active laogu-server
curl -fsS http://127.0.0.1:8000/api/health; echo
curl -fsS http://127.0.0.1:8000/api/health/ready; echo
```

## 4. 管理员登记激活码

管理员登录后，把授权终端生成的完整 `LGACT1....` 激活码提交到管理 API。服务器响应不会返回完整激活码：

```bash
curl -fsS -X POST https://api.jaycwl.org/api/license/register \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"activation_code":"LGACT1....","offline_grace_days":7}'
```

`offline_grace_days` 只能是 3–30。相同激活码重复登记是幂等操作；同一 `licenseId` 的新激活码使用 `/api/license/renew`。

## 5. 浏览器端配置

在浏览器 `config.yaml` 的 `license.server_url` 填入 HTTPS 服务地址，或设置环境变量 `LAOGU_LICENSE_SERVER_URL`。留空时浏览器保持纯离线模式。配置后启动会在线检查，服务器不可达时在最近一次成功检查后的宽限期内继续运行；撤销、设备不匹配或过期会立即拒绝。

不要把授权终端私钥、网站 Cookie、密码或完整激活码提交到源码仓库、备份包或服务器日志。
