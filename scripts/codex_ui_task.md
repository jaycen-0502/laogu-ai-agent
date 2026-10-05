# Codex 专属开发任务指令 (Model: sol-5.6-高 / gpt-5.6-sol)

## 一、任务目标与问题诊断
用户反馈 Web 管理后台在「客户端版本管理」页面点击“清理安装包”时，弹出的物理删除确认窗口存在视觉与色彩缺陷：
1. **背景冲突**：全局后台为现代明亮浅色风格（白灰配冷青绿与深石板灰），但清理弹窗错误地使用了暗黑沉闷底色（`var(--bg-surface, #1e1e2d)`），犹如突兀的“黑块”，破坏整体观感；
2. **文字对比度不足**：在暗黑底色上正文文字继承了全局深灰底色（`#17202a` / `#1e3a8a`），导致字迹发暗发黑、难以清晰阅读；
3. **按钮与层次缺乏质感**：原生按钮呈现纯白直角硬边，缺乏危险操作的视觉警示层级与悬浮反馈；
4. **信息条目呈现简陋**：硬盘空间释放量与日志保留规则直接用减号短横线罗列，缺乏结构化的卡片式重点标注。

目标：依据现代 Web 控件设计规范，使用模型 `sol-5.6-高`（`gpt-5.6-sol`）将该确认弹窗全面升级为高质感浅色卡片微质感界面。

---

## 二、色彩体系与视觉设计规范

1. **遮罩层 (Backdrop)**：
   - 采用深邃毛玻璃微透遮罩：`background: rgba(15, 23, 42, 0.55); backdrop-filter: blur(4px);`
   - 支持平滑淡入动效与点击外部空白处防误触关闭。
2. **卡片容器 (Card Container)**：
   - 纯净白底：`background: #ffffff; border: 1px solid #e2e8f0; border-radius: 14px;`
   - 深度柔和投影：`box-shadow: 0 25px 50px -12px rgba(15, 23, 42, 0.22), 0 0 0 1px rgba(15, 23, 42, 0.05);`
   - 入场动效：轻微缩放平滑向上浮现 (`releaseModalIn`，0.18s)。
3. **顶栏与警示徽章 (Header & Icon Badge)**：
   - 左侧柔和危险警示图标徽章：珊瑚红微浅底（`#fef2f2`）搭配精致垃圾桶/删除矢量图标（`#dc2626`）；
   - 主标题：深石板黑（`#0f172a`），字重 600，17px；副标题：中灰（`#64748b`），12px；
   - 右上角轻量关闭按钮（`✕`），悬浮变淡灰底色。
4. **正文与版本标记 (Body & Version Badge)**：
   - 核心询问文字：深灰（`#334155`），版本号用等宽代码胶囊徽章（`#f1f5f9` 底色 + 细边框）高亮呈现，附带渠道标签。
5. **重点提示卡片 (Structured Info Box)**：
   - 内嵌浅色卡片（`#f8fafc` 底色，`1px solid #e2e8f0` 细边）；
   - 绿色圆点清晰突出“释放空间（MB 数值）”；
   - 蓝色圆点清晰突出“历史日志永久留存”；
   - 玫瑰红圆点清晰提示“客户端下载限制影响”。
6. **操作按钮层级 (Actions Hierarchy)**：
   - 取消按钮：次级浅灰边框按钮（`#ffffff` 背景，`#cbd5e1` 边框，`#475569` 文字），悬浮平滑变深；
   - 危险删除按钮：正统警示绯红色（`#dc2626` 实心填充，`#b91c1c` 边框，白色字，轻微红色投影），处理中自动置灰。

---

## 三、修改的具体代码与落地

### 1. 样式表扩展 `web/src/styles.css`
- 新增 `.release-modal-overlay`
- 新增 `.release-modal-card` 及入场关键帧动画 `@keyframes releaseModalIn`
- 新增 `.release-modal-header`、`.release-modal-icon`、`.release-modal-title-box`、`.release-modal-close`
- 新增 `.release-modal-body`、`.release-modal-version-tag`、`.release-modal-channel-badge`
- 新增 `.release-modal-info-box`、`.release-modal-info-item`
- 新增 `.release-modal-actions`、`.release-btn-cancel`、`.release-btn-danger`

### 2. 页面重构 `web/src/pages/releases.tsx`
- 重写 `deleteTarget` 弹窗渲染结构，消除内联暗黑底色与行内黑框样式，完全接入全新样式类。

### 3. 版本对齐 `web/package.json`
- 保持前端包版本与服务端最新发布基准（`0.21.94`）严格一致，确保一键热更新流水线校验通过。

---

## 四、验证与自动化部署
1. 本地执行 `npm run build`，确保 TypeScript 类型检查及 Vite 打包 100% 通过；
2. 运行关联后端测试验证接口契约完整性；
3. 提交 Git 并在生产服务器无损热加载前端静态资源；
4. 确保运行端拓扑不受任何干扰。
