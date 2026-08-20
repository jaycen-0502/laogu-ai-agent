# Laogu Browser 捆绑构建说明

本目录保存 Laogu Browser 的可审查源码和 Wails 构建模板，不保存任何用户实例或生产凭据。

## Windows 环境

- Go：以 `go.mod` 中的版本为准
- Node.js 24 或兼容版本
- Wails CLI 2.13 或兼容版本
- Microsoft WebView2 Runtime

首次安装依赖：

```powershell
cd browser
npm --prefix frontend ci
go mod download
```

执行测试和构建：

```powershell
go test ./backend/...
npm --prefix frontend run build
wails build -clean
```

构建结果位于 `build\bin\Laogu-Browser.exe`。该目录受 Git 忽略规则保护，EXE 应作为
GitHub Release 附件发布，不应直接提交到仓库历史。

## 本地配置

发布时使用 `publish\config.init.yaml` 作为初始配置模板。真实 `config.yaml`、`data\`、
`logs\`、`bin\`、`chrome\` 和授权状态只保存在用户电脑，不会被 Git 跟踪。

服务器授权地址可通过发布配置或环境变量设置。不要把授权签发私钥、密码文件、数据库、
Cookie、账号登录资料或带有真实代理凭据的配置加入源码或 Release。
