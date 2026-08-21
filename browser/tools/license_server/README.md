# Python 离线授权工具

该工具可在 Windows、Linux 和常规云服务器运行，不需要图形界面。

## 安装

Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## 直接使用现有签发密钥

发布目录已经包含与客户浏览器匹配的签发私钥和密码文件，不要再次执行 `init`，否则新公钥与客户浏览器不匹配。

交互式签发：

```bash
python license_admin.py interactive
```

命令行签发：

```bash
python license_admin.py issue \
  --request 'LGREQ1...' \
  --days 30 \
  --customer '客户备注'
```

Windows PowerShell 可以写成一行：

```powershell
python .\license_admin.py issue --request 'LGREQ1...' --days 30 --customer '客户备注'
```

许可证编号为可选项。留空时自动生成；需要按固定编号续期或以后执行管理员解绑时，可以增加 `--license 'ORDER-2026-001'`。

## 其他命令

查看请求码：

```bash
python license_admin.py inspect --request 'LGREQ1...'
```

管理员解绑：

```bash
python license_admin.py unbind --license 'ORDER-2026-001'
```

查看签发台账：

```bash
python license_admin.py list
```

## 首次生成新密钥（仅用于新的一套客户端）

```bash
python license_admin.py init
```

会生成：

- `Laogu-License-Issuer.pem`：密码加密的 Ed25519 私钥
- `Laogu-License-Password.txt`：随机高强度私钥密码

生成的新公钥必须重新编译进客户端。已有正式客户版请勿执行此操作。

服务器自动运行时，也可以使用环境变量代替密码文件：

```bash
export LAOGU_LICENSE_KEY_PASSWORD='强密码'
```

## 安全要求

以下文件只由管理员保存，绝不能发给客户：

- `Laogu-License-Issuer.pem`
- `Laogu-License-Password.txt`
- `Laogu-License-Ledger.json`

请分别做离线备份，并限制为仅管理员可读。生产服务器上最好不要把密码文件和私钥放在同一个备份位置。

客户只需要获取编译后的浏览器 EXE。
