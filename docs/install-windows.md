# docbase 安装与运维手册（Windows）

面向：负责安装和维护 docbase 的 IT 人员。Linux / Docker 的安装另见 `install-linux.md`。

## 1. 组成

一台 Windows 电脑上运行三个 Windows 服务，加上 PostgreSQL 数据库：

| 服务 | 作用 | 端口 |
|---|---|---|
| `docbase-proxy` | HTTPS 入口（Caddy），把请求转给后端；HTTP 自动跳转到 HTTPS | 443、80（对局域网） |
| `docbase-api` | 后端接口和页面 | 8000（只在本机监听） |
| `docbase-worker` | 后台任务：存量导入、交付包、共享盘镜像、每天的备份和清理、存储检查 | 无 |
| `postgresql-x64-17` | 数据库（PostgreSQL 自己的服务） | 5432（只在本机） |

三个 docbase 服务都设成开机自动启动，崩溃后 10 秒自动重启，不需要有人登录 Windows。

## 2. 硬件和系统要求

| 项目 | 要求 |
|---|---|
| 系统 | Windows 10 / 11（专业版或以上），64 位 |
| 内存 | ≥ 8 GB |
| 文件存储 | 外接 ≥ 1 TB 的 USB 固态盘（或内置第二块盘 / NAS）；盘符固定（如 `E:`） |
| 备份 | 另一块盘或 NAS 更好；至少不要和文件存储在同一块盘上 |
| 网络 | 有线网（USB 网卡或扩展坞）、**固定 IP**；网络类型设成"专用网络" |
| 电源 | 接电源；关闭睡眠和休眠；关闭 USB 选择性暂停（否则外接盘会被断开） |

## 3. 安装前准备

1. **Python 3.12**（64 位）：从 python.org 下载 3.12 的 Windows 安装程序，安装时勾选 **Add python.exe to PATH**。要用 3.12，不能用 3.13 或更新（安装包里的依赖是为 3.12 准备的）。
2. **PostgreSQL 17**（或 16）：从 postgresql.org 下载 Windows 安装程序，按默认选项安装，**记住设置的 postgres 密码**（安装 docbase 时要输入一次，用来建库和账号）。
3. **外接盘**：接好并在"磁盘管理"里固定盘符；建一个文件夹，如 `E:\docbase-data`。
4. **电源**：设置 → 电源 → 睡眠设成"从不"；控制面板 → 电源选项 → 更改计划设置 → 更改高级电源设置 → USB 设置 → USB 选择性暂停设置 → 已禁用。
5. **固定 IP**：在路由器 / DHCP 上给这台电脑分配固定 IP，或在网卡设置里手动设置。
6. 把 docbase 安装包（`docbase-版本-windows.zip`）复制到这台电脑，**右键 → 属性 → 勾选"解除锁定"**，再解压（比如解压到桌面）。

安装过程不需要上网：Python 依赖、WinSW、Caddy 都在安装包里。

## 4. 安装

用**管理员身份**打开 PowerShell（开始菜单右键 Windows PowerShell → 以管理员身份运行），进入解压后的目录：

```powershell
cd $HOME\Desktop\docbase-0.1.0-xxxx-windows
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1 -DataDir E:\docbase-data
```

常用参数：

| 参数 | 说明 | 默认 |
|---|---|---|
| `-DataDir` | 文件存储目录（必填） | — |
| `-BackupDir` | 备份目录 | `C:\docbase-backup` |
| `-InstallDir` | 安装目录（程序、配置、日志） | `C:\docbase` |
| `-Address` | 访问地址，IP 或名字，多个用逗号分隔，如 `192.168.1.50,docbase.corp.local` | 本机局域网 IP |

脚本依次做：检查 Python 和 PostgreSQL → 复制程序 → 离线安装依赖 → 建数据库和账号（要输入 postgres 密码）→ 写配置文件 → 升级数据库结构 → **建第一个系统管理员**（输入用户名和两次密码）→ 生成 HTTPS 配置 → 注册三个服务 → 开防火墙 → 检查。

完成后屏幕上会显示访问地址。用浏览器打开 `https://地址`，用刚才建的管理员登录；其他用户在页面右上角"用户名 → 用户管理"里建。

安装后的目录：

```
C:\docbase\
  app\            程序（升级时整体替换）
  venv\           Python 虚拟环境
  config\docbase.env   配置（数据库密码在这里，只有管理员能读）
  services\       三个服务的 WinSW 程序和配置
  proxy\          Caddy、Caddyfile、证书数据（proxy\data）
  certs\docbase-root.crt   内网根证书（发给每台访问的电脑）
  logs\           日志
E:\docbase-data\  文件存储（blobs 下按内容存放，不要手动改动）
C:\docbase-backup\  备份
```

重复运行 `install.ps1` 是安全的：已有的配置文件、数据库、管理员都会沿用（可以用来修复）。

## 5. 证书（HTTPS）

### 5.1 默认：内网自签证书

Caddy 第一次启动时自己生成一个根证书，用它给访问地址签发证书（每 12 小时左右自动续期，不用管）。浏览器要信任这个根证书，否则会显示"不安全"：

- **服务器本机**：安装脚本已经装好。
- **每台访问的 Windows 电脑**：把 `C:\docbase\certs\docbase-root.crt` 拷过去，双击 → 安装证书 → **本地计算机** → 将所有的证书都放入下列存储 → 浏览 → **受信任的根证书颁发机构** → 完成。重开浏览器。
- **电脑多时**：用域的组策略统一下发（计算机配置 → Windows 设置 → 安全设置 → 公钥策略 → 受信任的根证书颁发机构 → 导入）。
- **Mac**：双击证书加入"钥匙串访问"的"系统"钥匙串，设为"始终信任"。
- **iPhone / Android**：通过邮件或网页下载证书安装，并在设置里打开对它的完全信任。

根证书只用于这台服务器，有效期 10 年。**迁移到新机器时复制 `proxy\data` 文件夹可以沿用同一个根证书**，各电脑不用重装（见 `migrate-machine.md`）。

### 5.2 用公司自己的证书

公司有自己的证书颁发机构（如 AD 证书服务）时，可以用它签发的证书，各电脑天然信任：

1. 申请证书时把访问地址（IP 或名字）写进"使用者可选名称（SAN）"；导出成 PEM 格式的证书（含中间证书）`docbase.crt` 和私钥 `docbase.key`，放到 `C:\docbase\certs\`。
2. 编辑 `C:\docbase\proxy\Caddyfile`，把 `tls internal` 改成：
   ```
   tls C:\docbase\certs\docbase.crt C:\docbase\certs\docbase.key
   ```
3. 重启服务：`Restart-Service docbase-proxy`。

### 5.3 用 Nginx 代替 Caddy

客户 IT 习惯 Nginx 时，可以停用 `docbase-proxy` 服务，用 `deploy\nginx\docbase.conf` 示例配置（上传上限、超时、转发头都已写好）。

## 6. 日常运维

### 6.1 服务

```powershell
Get-Service docbase-*                                   # 看状态
Restart-Service docbase-api, docbase-worker             # 改了配置后重启
Stop-Service docbase-proxy, docbase-worker, docbase-api # 全部停止
```

也可以在"服务"（`services.msc`）里操作。

### 6.2 配置

配置在 `C:\docbase\config\docbase.env`（用记事本以管理员身份打开），每项都有说明。改完重启 `docbase-api` 和 `docbase-worker`。常改的：

| 配置 | 说明 |
|---|---|
| `DOCBASE_BACKUP_DIR` | 备份目录（改到 NAS：`\\nas\backup\docbase`，服务账号要能写） |
| `DOCBASE_BACKUP_HOUR` | 每天几点备份（默认 1 点） |
| `DOCBASE_IMPORT_ROOTS` | 存量导入允许读的目录 |
| `DOCBASE_MIRROR_DIR` | 共享盘只读镜像目录 |

### 6.3 日志

- `C:\docbase\logs\api-日期.log`、`worker-日期.log`：后端和后台任务，按天一个文件，保留 30 天、总共不超过 500 MB。
- `C:\docbase\logs\proxy-access.log`：HTTPS 访问记录。
- `C:\docbase\logs\service\`：服务启动、崩溃时的输出。

出问题时把日志打包发给维护方（不含任何项目文件，密码已隐去）：

```powershell
cd C:\docbase\app\backend
$env:DOCBASE_ENV_FILE = 'C:\docbase\config\docbase.env'
..\..\venv\Scripts\python.exe scripts\collect_logs.py --out $HOME\Desktop
```

### 6.4 外接盘断开

外接盘断开（或 NAS 断开）时：

- 页面顶部出现红条"文件存储不可用"，上传和导入被拒绝；浏览、下载已有文件也会失败；
- `worker-日期.log` 里记一条 ERROR；`https://地址/api/health` 返回 `"storage": false`；
- 接回去后在一分钟内自动恢复，不需要重启服务。

### 6.5 备份

- `docbase-worker` 每天 1 点自动备份到 `DOCBASE_BACKUP_DIR`：数据库一份，文件只复制新增的；保留 30 天。
- 超过 2 天没有成功备份、或最近一次失败时，**系统管理员**登录后页面顶部会出现黄条。
- 手动备份 / 看有哪些备份：

```powershell
cd C:\docbase\app\backend
$env:DOCBASE_ENV_FILE = 'C:\docbase\config\docbase.env'
..\..\venv\Scripts\python.exe scripts\backup.py           # 马上备份一次
..\..\venv\Scripts\python.exe scripts\backup.py --list    # 列出备份点
```

备份目录的结构：`db\`（每次的数据库）、`files\`（文件，按内容命名）、`manifests\`（每次备份的清单）。**整个备份目录要一起复制**，单独拷某个子目录无法恢复。

### 6.6 恢复

**演练（建议每季度一次）**：恢复到一个新库和新目录，不影响正在用的系统。

```powershell
cd C:\docbase\app\backend
$env:DOCBASE_ENV_FILE = 'C:\docbase\config\docbase.env'
..\..\venv\Scripts\python.exe scripts\restore.py --to docbase_drill --data-dir D:\restore-drill
```

最后会列出项目、应交项、文件、版本数量，和备份时对比，全部一致才算通过。演练完删除：在 pgAdmin 里删掉 `docbase_drill` 库，删掉 `D:\restore-drill` 文件夹。

**真正出事时**（数据库损坏、误操作要回到某天）：

1. 停服务：`Stop-Service docbase-proxy, docbase-worker, docbase-api`
2. 找备份点：`restore.py --list`
3. 覆盖恢复（会要求输入库名确认）：
   ```powershell
   ..\..\venv\Scripts\python.exe scripts\restore.py --stamp 20261026 --to docbase --data-dir E:\docbase-data --force
   ```
4. 启动服务：`Start-Service docbase-api, docbase-worker, docbase-proxy`

文件存储目录只会补上缺的文件，不会删除任何文件。

## 7. 升级

1. 把新版本安装包复制到这台电脑，解除锁定、解压。
2. 管理员 PowerShell 进入新版本目录：
   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\upgrade.ps1
   ```
3. 脚本会：先备份 → 停服务 → 换程序（旧程序留在 `C:\docbase\app.previous`）→ 装依赖 → 升级数据库结构 → 更新服务配置并启动 → 检查。
4. 失败时屏幕上会显示回退步骤。

配置文件、文件存储、备份、证书都不会被改动。

## 8. 卸载

```powershell
.\uninstall.ps1                  # 删除三个服务和防火墙规则，其余都保留
.\uninstall.ps1 -RemoveProgram   # 另外删除程序和虚拟环境
```

数据库、文件存储、备份不会被删除。

## 9. 常见问题

| 现象 | 处理 |
|---|---|
| 浏览器显示"连接不是私密连接" | 这台电脑还没装根证书（见第 5 节） |
| 别的电脑打不开，本机能打开 | 网络类型是"公用网络"（防火墙只对专用 / 域网络放行）；或 IP 变了 |
| 页面顶部红条"文件存储不可用" | 外接盘没接好、盘符变了，或 USB 选择性暂停没关 |
| 页面顶部黄条"超过 2 天没有成功备份" | 看 `docbase-worker` 是否在运行、备份目录是否可写；看 `worker-日期.log` |
| 服务启动后马上停止 | 看 `logs\service\` 里对应服务的日志；常见原因：配置文件里数据库密码不对、PostgreSQL 没启动 |
| 运行脚本提示"禁止运行脚本" | 先执行 `Set-ExecutionPolicy -Scope Process Bypass`；安装包解压前要"解除锁定" |
| 忘记管理员密码 | `cd C:\docbase\app\backend`，设好 `DOCBASE_ENV_FILE` 后运行 `..\..\venv\Scripts\python.exe scripts\create_user.py --username 用户名 --admin`，输入新密码 |
