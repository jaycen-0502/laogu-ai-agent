# 离线授权使用说明

## 文件分发

发给客户的文件只有：

- `build/bin/Laogu-Browser-offline-license.exe`

以下管理员文件绝对不能发给客户：

- `build/license-admin/Laogu-License-Keygen.exe`
- `build/license-admin/Laogu-License-Issuer.key`
- 工具首次签发后生成的 `Laogu-License-Ledger.json`

`Laogu-License-Issuer.key` 是签发私钥，使用 Windows DPAPI 加密并绑定管理员电脑。请同时做离线备份；私钥丢失后，现有客户端将无法继续使用同一签名体系续期。

## 首次激活

1. 客户打开浏览器客户版。
2. 软件显示 `LGREQ1.` 开头的机器请求码。
3. 客户点击“复制请求码”并发送给管理员。
4. 管理员双击 `Laogu-License-Keygen.exe`。
5. 粘贴请求码，输入授权天数、客户备注和唯一许可证编号。
6. 工具生成并自动复制 `LGACT1.` 开头的激活码。
7. 客户粘贴激活码并点击“立即激活”。

## 续期

客户到期后发送软件显示的新请求码。签发时必须继续使用原来的许可证编号，可以更换授权天数。

授权天数支持任意 `1-3650` 天，常用值：

- 7 天：试用或短期授权
- 30 天：月卡
- 90 天：季卡
- 365 天：年卡

## 单设备绑定与管理员解绑

管理员工具会在同目录生成 `Laogu-License-Ledger.json`。同一个许可证编号默认只能绑定一台电脑；给另一台电脑签发时会拒绝。

换机前执行：

```powershell
.\Laogu-License-Keygen.exe unbind --license "许可证编号"
```

解绑后，使用新电脑请求码和相同许可证编号重新签发。

## 命令行签发

```powershell
.\Laogu-License-Keygen.exe issue `
  --request "LGREQ1..." `
  --days 30 `
  --customer "客户备注" `
  --license "ORDER-2026-001"
```

## 识别与防复制

客户端绑定以下信息：

- Windows MachineGuid
- 系统盘卷序列号
- 主板/系统 UUID
- 当前安装生成的 Ed25519 公钥
- DPAPI 加密的本机安装私钥

激活码由管理员 Ed25519 私钥签名。复制到其他电脑、修改到期时间、使用旧请求码对应的激活码，都会校验失败。软件还会检测明显的系统时间回退。

这是离线许可方案，不能做到远程立即封禁。管理员解绑只决定是否允许给新电脑签发，不会撤销旧电脑尚未到期的离线激活码；旧授权会在原到期时间失效。
