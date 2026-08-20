# Laogu Web管理后台

开发模式：

```bash
cd web
npm install
npm run dev
```

Vite会把`/api`代理到`http://127.0.0.1:8000`。

生产构建：

```bash
npm ci
npm run build
```

生产环境由Nginx提供`web/dist`静态文件，`/api/`反向代理到FastAPI。不要在生产环境运行Vite开发服务器。

JWT仅保存在浏览器`sessionStorage`，关闭浏览器会话后失效。最终权限始终由Server根据用户角色和`workspace_id`判断。
