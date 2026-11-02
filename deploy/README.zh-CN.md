# 部署方式与入口

以 `docs/PRD.md` 第 5、8–9、11 节及 `docs/decisions.md` 为依据。

## 新电脑：双击安装（推荐）

1. 复制**最新的完整 Windows 发布包** `docbase-版本-windows.zip`，在属性中解除锁定（若有），完整解压到短路径，例如 `C:\docbase-setup`。不要在压缩包里直接运行，也不要只复制一个脚本。
2. 双击解压目录的 **`install.cmd`**，接受 Windows 管理员权限提示。默认联网安装缺少的 Python 3.12、PostgreSQL 17，再安装应用服务并验证。需要 64 位 Windows、管理员权限、联网及可用的微软 App Installer / winget；不需要 Git、Node 或提前配置 Python 环境。
3. 等待成功提示后打开 `https://localhost`。账号 `admin`，随机密码以管理员身份读取 `C:\docbase\config\setup-credentials.json`。失败查看 `C:\docbase\logs\bootstrap.log`。

`install.ps1` 是同一个安装入口，但 Windows 双击 `.ps1` 通常会打开编辑器。请双击 `.cmd`，或在解压目录执行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1`。

默认程序、数据、备份目录分别为 `C:\docbase`、`C:\docbase-data`、`C:\docbase-backup`。正式使用建议将备份放到另一块盘。局域网访问需指定服务器固定 IP（`-Address`），并按运维手册在客户端信任根证书。

## 完整源码：在线一键部署

不需要预装数据库、Python 或 Git。从真正源码根目录运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\install.ps1
```

接受 Windows 管理员权限提示后，脚本检查并安装 Python 3.12、PostgreSQL 17；从源码构建前端（没有 Node 时也会安装），在 `C:\docbase\packages` 生成当前版本 Windows 安装包，再安装 API、worker、Caddy 服务和防火墙规则。短的发布目录可避免当前项目嵌套路径触发 Windows 长路径错误。通过微软 winget 下载，首次执行必须联网；需要可用的 App Installer / winget。

默认目录：程序 `C:\docbase`，文件 `C:\docbase-data`，备份 `C:\docbase-backup`，入口 `https://localhost`。其他内网电脑要访问时指定本机固定 IP，例如：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\bootstrap.ps1 -Address 192.168.1.50 -DataDir E:\docbase-data
```

首次创建的数据库超级用户密码、应用管理员随机密码保存在 `C:\docbase\config\setup-credentials.json`，仅管理员和 SYSTEM 可读。管理员用户名为 `admin`。安装日志在 `C:\docbase\logs\bootstrap.log`。本入口安装的数据库在失败后重试时沿用原凭据，已有应用用户不会被重置。如果原先已经安装了其他 PostgreSQL，脚本不会覆盖或重置其密码，需要在 PowerShell 中用 `-PostgresPassword (Read-Host -AsSecureString)` 传入已有密码，而不是期待自动生成的密码能登录旧数据库。

`-PrepareOnly` 只准备 Python 和数据库；`-UseExistingFrontend` 仅在明确需要复用已有 `frontend/dist` 时使用，默认重新构建。完整离线发布包仍按下面的传统流程安装；在线入口和离线安装的前提不同。

脚本最后验证证书、健康、前端、匿名访问拒绝、登录和项目读写，并备份恢复到新的 `docbase_drill_时间` 数据库。冒烟项目会被归档；恢复演练数据库及 `C:\docbase\restore-drills` 下的文件保留供核对。日志最后出现 `DEPLOYMENT VERIFIED` 才代表这组验证通过。已有账号密码改过时，自动验证的初始密码也需相应更新。

| 场景 | 方式 | 安装入口 |
|---|---|---|
| 当前 Surface Pro 7 试点（8/16 GB、Windows、外接 SSD） | Windows 原生服务：WinSW + FastAPI + worker + Caddy，本机 PostgreSQL | Windows 发布包根目录 `install.ps1` |
| 客户已有 Linux 服务器，需要容器化交付 | Docker Compose：PostgreSQL、API、worker、Caddy | Linux 发布包根目录 `docker-install.sh` |
| 客户已有 Ubuntu 24.04，禁止容器 | systemd + 本机或外部 PostgreSQL | Linux 发布包根目录 `install.sh` |

当前试点优先修复 Windows 安装和验收，不需要为部署而更换操作系统或在 Surface 上额外安装 Docker。第一版是内网单机单租户系统，不需要 Kubernetes、Redis、Celery、MinIO；AI 仍是第二版范围。

## 源码与安装包

- `deploy/windows`：打包时复制的脚本和模板，不是可独立运行的安装包。
- `deploy/linux`：Docker 可以在源码布局下构建；systemd 安装仍需发布包。发布包启动应使用预加载的镜像。
- `deploy/nginx`：可选反向代理示例，默认安装使用 Caddy。
- `release`：构建产物。现有旧包不自动包含当前修复，重新构建后才能使用新代码。
- 项目被放在同名父目录里时，先进入包含 `backend`、`frontend`、`deploy` 的内层项目根目录。

## Windows 发布流程

在线一键入口已包含构建。需要单独制作离线安装包时，构建机先安装 Python 3.12、Node.js 22；在真正源码根目录执行：

```powershell
py -3.12 -m venv backend\.venv-build
& .\backend\.venv-build\Scripts\python.exe -m pip install -e '.\backend[dev]'
Push-Location frontend
npm.cmd ci
Pop-Location
& .\backend\.venv-build\Scripts\python.exe backend\scripts\release.py --target windows --output-dir C:\docbase-packages
```

把新生成的 zip 复制到运行机，解除锁定、解压。在目标运行机安装 Python 3.12、PostgreSQL 16/17 后，以管理员 PowerShell 进入新包根目录：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1 -Offline -DataDir E:\docbase-data -BackupDir C:\docbase-backup
```

`E:` 按实际外接盘盘符调整。完整步骤见 `docs/install-windows.md`。开发机只做试装；正式注册服务在目标运行机验收，不在开发机上自动注册。

## 上线标准

1. API、worker、proxy 均运行，健康检查 `ok/db/storage` 均为 true。
2. 另一台内网电脑信任根证书后能登录、上传和下载。
3. 重启运行机且不登录 Windows，服务仍自动可用。
4. 外接盘断开时拒绝写入，接回后恢复。
5. 手工备份并恢复到新库、新目录，核对数量和抽查文件。

历史步骤文档中的测试通过记录只代表当时的环境；不能代替当前机器验收。Linux 验证按原决策使用 GitHub Actions，需实际查看运行结果。

## 发布验证

每次发布应检查安装、登录、文件上传与交付、备份恢复及重启后的服务状态，并记录对应版本。源码变化不会自动更新已有安装包或运行实例。真实容量与故障验收见架构说明。
