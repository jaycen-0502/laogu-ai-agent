# Stage 10D 部署说明

版本：Server `0.14.0`，Windows Agent `0.9.0`，数据库迁移 `0011_credential_capabilities`。

本阶段只实现 Credential/Cookie Capability Probe，不实现 Credential Snapshot。Probe 仅调用已有 Browser 健康和 Profile 状态接口，并只识别明确公开的能力元数据字段；不请求、不读取、不保存、不返回 Cookie Value、Token 或会话值。

新增 Command：`PROBE_CREDENTIAL_CAPABILITY`。新增只限 ADMIN/OWNER 的 `GET /api/credential-capabilities`。数据库只保存 browser reachable、cookie read/write supported、snapshot allowed、evidence 和检查时间。

若 Browser 未明确公开支持能力，结果必须为 `NOT_ADVERTISED`，所有 Cookie 能力为 false，Snapshot 保持禁用。只有明确能力元数据同时证明读取和 Snapshot 支持时，`credential_snapshot_allowed` 才可能为 true；本阶段仍无 Snapshot 执行接口。

部署顺序：备份数据库与源码，校验更新包，安装代码，执行 `alembic upgrade head`，重启 Server；在 Windows 备份并更新 Agent 后重新运行。验收时只执行 Probe，不得尝试导出 Cookie。
