# Web 工作区选择修复更新包

`stage15-user-workspace-fix.tar.gz` 修复“用户与邀请 → 管理员直接创建用户”提交时工作区为空的问题。

根因是后端工作区列表接口返回 `id`，旧前端只读取 `workspace_id`。更新后的前端同时兼容两种字段，并默认使用当前管理员工作区。

## 服务器部署

先将压缩包上传到服务器 `/tmp/`，然后执行：

```bash
cd /opt/laogu-ai-agent
sha256sum /tmp/stage15-user-workspace-fix.tar.gz
tar -tzf /tmp/stage15-user-workspace-fix.tar.gz
sudo tar -xzf /tmp/stage15-user-workspace-fix.tar.gz -C /opt/laogu-ai-agent
sudo chown laogu:laogu web/src/pages/index.tsx
sudo -u laogu bash -c '
cd /opt/laogu-ai-agent/web
npm ci --no-audit --no-fund
npm run build
'
sudo systemctl reload nginx
```

构建成功后，在浏览器按 `Ctrl+F5` 刷新。更新包只包含公开前端源码，不包含数据库、生产配置、Token、私钥或用户数据。
