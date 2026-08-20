Laogu Desktop 便携版
====================

启动：双击 Laogu-Desktop.exe。

使用前：
1. 启动 Laogu Browser，确认本机 127.0.0.1:19876 可访问。
2. 检查 config\laogu.env 中的 LAOGU_SERVER_URL。
3. 如果检测到 python -m agent.service_main，程序会询问是否关闭它；确认后只关闭独立 Agent，不会关闭 Laogu Browser。

已有 Agent 凭据：
首次运行会自动从桌面原项目导入 agent_data\credentials.json，无需手动复制。
程序只复制 DPAPI 加密凭据，不会解密或显示 Agent Token。
该文件只能在原 Windows 用户下使用，不要发送给别人。

首次注册：
便携包不会内置管理员密码、JWT 或 Agent Token。建议先用原项目完成 Agent 注册，
程序首次启动时会自动导入 DPAPI 保护后的 credentials.json。不要把注册 JWT 长期写入配置文件。

日志与状态：
- logs\
- agent_data\

程序仍依赖本机已经安装的 Laogu Browser、Node.js，以及 Laogu Browser 的 Automation Runtime。
