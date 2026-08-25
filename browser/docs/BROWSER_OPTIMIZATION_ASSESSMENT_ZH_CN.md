# Laogu Browser 优化评估与安全边界

## 结论

当前不建议直接修改 Chromium 内核或增加“隐藏 CDP / Playwright、规避 X 风控、消除 Shadowban”功能。

这些目标会要求伪造或隐藏自动化事实，可能违反第三方平台规则，也会破坏当前系统依赖的 CDP、Launch API、指纹自测和 Agent 调度链路。后续优化应限定为：隐私保护、配置一致性检查、连接可靠性、可观测性和兼容性回归。

## 已完成的基线

- 源码：当前工作树 `browser/`
- 前端构建：`npm run ensure:native`、`npm run build:clean` 通过
- Wails 构建：`wails build -clean` 通过
- 产物：`build/bin/Laogu-Browser.exe`
- 分类基线包：`release/2026-08-23-baseline/`
- 基线包不包含：`data/`、个人 `config.yaml`、Cookie、LocalStorage、登录状态、代理凭据、Token、授权私钥和数据库

## 必须保持不变的对接契约

以下内容在任何优化中都不能改名、改端口、改协议或改生命周期：

1. Launch API 只监听 `127.0.0.1`，默认端口 `19876`。
2. 统一 CDP 入口仍提供 `/json/version`、`/json/list` 和 WebSocket 调试目标。
3. Agent、桌面控制台和外部 Playwright 继续使用现有 Launch API / CDP 连接方式。
4. Profile 的 `user_data_dir`、`profile_id`、启动码、调试端口状态和 SQLite 数据结构保持兼容。
5. Xray、sing-box、Mihomo 三套代理连接栈不能自动混用；连接栈选择必须继续由现有配置决定。
6. 关闭桌面控制台时，内置 Agent 的停止行为保持不变；不能改成后台常驻或静默拉起额外进程。
7. DPAPI 凭据保护、设备绑定、API Key、审计日志和敏感字段脱敏保持启用。

## 对原始四项目标的处理

### 1. 自动化与 CDP

不实现“彻底屏蔽 CDP、Runtime.enable 或 Playwright 痕迹”。当前系统明确依赖这些接口完成调试就绪检测、统一 CDP 接管、浏览器状态同步和只读任务执行。

可做的合规改进：

- 继续只绑定 `127.0.0.1`，不监听公网地址。
- 为 Launch API 增加可选 API Key，并对失败认证、连接来源和调用方法记录审计日志。
- 在 UI 中显示当前实例是否处于 CDP 接管状态，便于排障而不是隐藏事实。
- 对 CDP 方法做最小权限分级：只读检查、导航、Profile 控制和 Cookie 管理分别授权；默认不开放敏感导出。
- 保留 `debugReady`、活动实例和连接超时检查，避免 Agent 误连其他实例。

### 2. 网络、WebRTC、时区和语言

不提供“静态住宅 IP + 绕过平台检测”的黄金配置。可以提供环境一致性校验：

- 代理出口国家/地区、Profile 时区、主语言和 Accept-Language 不一致时给出阻断或明确警告。
- Geo-Location 仅在用户明确配置并获得网页权限时生效；不把 IP 地理位置伪装成任意城市。
- 继续使用当前代理连接栈和 IP 健康检查，不改变 Xray/sing-box/Mihomo 的解析规则。
- `--disable-non-proxied-udp` 可作为“阻止非代理 UDP 泄漏”的隐私选项，但必须标注其可能影响 WebRTC 音视频；不要悄悄强制覆盖用户选择。
- WebRTC 采用明确的策略枚举和启动前提示，禁止在运行中随机切换。

### 3. 硬件、Canvas、WebGL 和 AudioContext

不建议使用每次启动或每次页面访问都变化的随机噪声，也不建议伪造与真实主机明显矛盾的 GPU、CPU、RAM、屏幕尺寸组合。

可做的合规改进：

- Profile 指纹种子在同一 Profile 生命周期内稳定，复制 Profile 时显式生成新种子并记录审计事件。
- `hardwareConcurrency`、`deviceMemory`、窗口尺寸、DPR、触摸点数和平台版本做范围与组合校验，超出合理范围时阻止保存。
- Canvas、ClientRects、AudioContext、WebGL 的默认策略保持现有实现；将噪声开关作为可见的 Profile 配置，并在保存时提示兼容性影响。
- 使用现有“指纹自测”页面展示 runtime 与 expected 的差异，不以“通过风控”为验收标准。
- GPU / WebGL 只允许受支持的真实或明确的兼容性档案，不注入任意厂商和渲染器字符串。

### 4. Profile 预热与会话留存

不自动执行点赞、关注、发帖、搜索轰炸或其他平台行为来“养号”。

可做的合规改进：

- 首次启动只执行用户明确选择的站点导航和静态资源缓存预热。
- 预热任务显示目标 URL、权限和持续时间，支持取消，并记录本地审计日志。
- Cookie、LocalStorage、Cache 和 IndexedDB 只保存在对应 Profile 的本地目录，不导出到服务器、不跨 Profile 复制。
- 提供“清理站点数据”“导出非敏感诊断报告”“备份前确认敏感字段”的明确操作。

## 推荐的分阶段优化

### 阶段 A：只读一致性检查

增加 Profile 保存前校验和启动前诊断，不改变启动参数：

- 代理地区 vs 时区、语言、Geo-Location
- UA / UA-CH vs 平台和品牌版本
- CPU、内存、窗口、DPR、触摸点数的合理范围
- WebRTC 策略与代理连接栈
- CDP 端口是否仅监听本机、是否被错误进程占用

实施状态（2026-08-23）：已在 Profile 编辑页加入“一致性诊断”。本地配置提醒实时显示；代理出口定位和 Launch API 状态只在用户点击“运行诊断”后只读获取。诊断不调用保存、不调用启动、不回写 Profile 参数，也不阻止用户继续操作。现有格式错误、无效代理和缺少运行依赖等功能性校验保持原逻辑。

### 阶段 B：可见的隐私开关

将 WebRTC 非代理 UDP、Canvas 噪声、ClientRects 噪声等做成可见的 Profile 选项，并在 UI 标注兼容性影响。默认值维持当前行为，避免影响既有 Profile。

### 阶段 C：兼容性回归与发布

每次修改必须同时通过：

- `go test ./backend/...`
- `npm --prefix frontend run build:clean`
- `wails build -clean`
- Launch API 本机连通性测试
- CDP `/json/version`、`/json/list` 和 WebSocket 接管测试
- Agent 启动、停止、Profile 启动、Profile 停止测试
- 代理栈独立测试：Xray 组合栈、Mihomo 栈、sing-box 协议
- 旧 Profile 数据目录只读启动回归

## 暂不执行的改动

- 删除或隐藏 CDP、`Runtime.enable`、Playwright 连接信息
- 伪造 `navigator.webdriver` 或其他自动化检测结果
- 通过 Chromium Flag、扩展或协议改写规避第三方平台风控
- 随机化 Canvas/WebGL/AudioContext 以规避识别
- 自动生成或批量预热社交平台行为
- 修改 19876 端口、Launch API 路径、CDP WebSocket 协议和 Agent 调度接口

只有在完成阶段 A 的只读诊断、兼容性测试和用户确认后，才考虑阶段 B 的可见开关；不以“绕过 X 风控”或“消除 Shadowban”作为验收条件。
