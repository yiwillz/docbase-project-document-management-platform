# 开发环境准备（Windows）

本手册面向修改源码和运行测试的开发者。只安装使用平台，请按 [Windows 安装手册](install-windows.md) 双击完整包中的 `install.cmd`；运行机无需 Git、Node、VS Code 或开发虚拟环境。项目定位与架构见 [项目说明](../README.zh-CN.md)。

| 软件 | 版本 | 说明 |
|---|---|---|
| Git | 最新版 | 安装时一路默认即可 |
| Python | 3.12.x | 从 python.org 下载；**安装时勾选 "Add python.exe to PATH"** |
| PostgreSQL | 16.x 或 17.x | 用官方 Windows 安装包（含 pgAdmin）；记住设置的 postgres 密码；端口保持 5432 |
| Node.js | 22 LTS | 前端开发和打包用（第 9 步开始需要） |
| VS Code | 最新版 | 建议装 Python、Vue - Official 两个扩展 |

## 第一次打开项目

1. 把仓库克隆到一个短路径下（例如 `D:\work\docbase`，不要放在其他项目或 `.venv` 里面）：
   ```powershell
   git clone https://github.com/yiwillz/docbase-project-document-management-platform.git D:\work\docbase
   ```
   根目录应能看到 `README.md`、`docs\`、`backend\`、`frontend\`、`deploy\`。
2. 建独立测试数据库（打开 pgAdmin，或在 PowerShell 里运行；`docbase` 已存在时不要重复创建）：
   ```powershell
   psql -U postgres -c "create database docbase;"
   psql -U postgres -c "create database docbase_test;"
   ```
3. 建虚拟环境并安装后端依赖（在 backend 目录）：
   ```powershell
   python --version                 # 确认解释器为 Python 3.12.x
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -e ".[dev]"
   ```
4. 按下一节配置 `backend/.env`，然后启动连测试库的后端：`python scripts/test_server.py`（启动时自动执行数据库迁移）。

不要复制其他电脑的 `.venv`。若提示找不到原电脑的 Python，请先将旧 `.venv` 重命名留存，再用本机 Python 3.12 重建并安装依赖。安装实例 `C:\docbase\venv` 与源码 `backend/.venv` 互不替代。

## 数据库连接配置

把 `backend/.env.example` 复制一份改名为 `backend/.env`，把密码改成你的：

```
DOCBASE_DATABASE_URL=postgresql+psycopg://postgres:你的密码@localhost:5432/docbase
DOCBASE_TEST_DATABASE_URL=postgresql+psycopg://postgres:你的密码@localhost:5432/docbase_test
DOCBASE_DATA_DIR=D:/docbase-data
```

正式运行在 Surface Pro 7 上时，`DOCBASE_DATA_DIR` 指向外接盘，例如 `E:/docbase-data`。

要用存量导入时，再加一行允许导入的目录（多个用 `;` 分隔），只有这些目录下的文件能导入：

```
DOCBASE_IMPORT_ROOTS=D:/old-projects
```

数据库 URL 密码中的 `@`、`:`、`/` 等保留字符需要先进行 URL 编码。开发配置只用于本机测试，不复制运行实例的凭据文件。

后台 worker（存量导入、每天的例行清理）是单独的进程，和后端一起开着（在 backend 目录、激活虚拟环境后）：

```powershell
python scripts/worker.py --test   # 开发时只连测试库，文件存 backend/data_test
```

正式安装时，安装脚本会把后端和 worker 都注册成 Windows 服务，开机自启，见 [install-windows.md](install-windows.md)；Linux 见 [install-linux.md](install-linux.md)。`.env` 里其他配置项（日志、备份等）的说明在 `backend/.env.example` 里。

要用共享盘只读镜像时，在 `.env` 里加：

```
DOCBASE_MIRROR_DIR=//fileserver/docbase-mirror   # 共享盘上的一个文件夹
DOCBASE_MIRROR_MODE=link                         # link 硬链接（默认）/ copy 复制
```

**用硬链接时，这个共享必须由 IT 设成只读**（共享权限里只给读）。硬链接和平台存储是同一份数据：有人在共享盘上直接改了镜像里的文件，平台存的那一版也跟着变。平台会把镜像里的文件设成只读，但这个属性别人可以去掉，挡不住。IT 做不到只读共享时改用 `DOCBASE_MIRROR_MODE=copy`（多占一倍空间）。镜像目录和数据目录不在同一块盘、或者盘不支持硬链接（如 exFAT 的 U 盘）时，平台会自动改成复制。

## 前端

第一次（在 frontend 目录）：
```powershell
npm ci
```

**开发时**（改前端代码，页面会自动刷新）：
1. 另开一个终端，在 backend 目录启动连测试库的后端：`python scripts/test_server.py`（8001 端口）
2. 在 frontend 目录：`npm run dev`，浏览器打开 http://localhost:5173
3. 页面里的接口请求会转到 8001。保留开发与正式实例的隔离；不要为页面试验将代理指向运行中的正式库。

**发布运行**：`npm run build` 生成 `frontend/dist`，发布包由 FastAPI 提供页面，Caddy 提供 HTTPS；安装后的入口是 `https://localhost` 或配置的服务器地址，不需要另外运行 Vite。

**登录**（第 16 步起）：
- 连测试库的后端（`python scripts/test_server.py`）启动时会自动建测试账号，并在终端里显示：
  - `tester` / `tester123`：系统管理员；
  - `test_admin`（项目管理员）、`test_member`（成员）、`test_viewer`（只读），密码都是 `test12345`。启动时会把它们加进测试库里每个没归档的项目；之后新建的项目，重启测试后端时再加。
  - 想看不同角色看到的界面，就退出登录换一个账号。
- 测试库里积累了太多自动测试建的项目时：先停掉连测试库的后端和 worker，再运行 `python scripts/reset_test_db.py`，清空测试库和测试文件、重建上面的测试账号。只对测试库有效。
- 自动安装脚本会创建初始管理员。只有手动初始化的空数据库才需要用下面的运维命令创建管理员（必须确认连接的是预期数据库；开发中用 `--test`）：
  ```powershell
  python scripts/create_user.py --admin
  ```
  以后的用户在页面右上角"用户名 → 用户管理"里建；项目成员和角色在项目的"设置 → 成员"里加。忘记密码时由系统管理员在"用户管理"里重设，或者再运行一次上面的命令（已有的用户名会改成新密码并解除锁定）。

**页面自动测试**（第 10 步起）：在 frontend 目录 `npm run e2e`。它会自己启动连测试库的后端（8021 端口）和打包好的前端（5193 端口），用电脑上装的 Chrome 按主流程点一遍，跑完自动关掉，不需要你开着前后端。`check.py` 每步结束也会跑它。

## 开发检查与 Git 提交

Git 提交和推送不需要 Python。本项目不再提供强制提交钩子；代码检查由开发者主动运行，GitHub Actions 在自己的 runner 中安装依赖并运行，不使用你电脑上的虚拟环境。

```powershell
# 在 backend 目录，使用本机开发虚拟环境
.\.venv\Scripts\python.exe scripts/check.py --quick  # 后端和前端 lint
.\.venv\Scripts\python.exe scripts/check.py          # 完整测试与前端检查
```

完整后端测试会重建测试库结构，运行前停止使用同库的人工测试后端和 worker。旧电脑复制来的 `.venv` 不可用时重建开发环境；这不应阻止 Git 提交。

公开仓库保留源码、测试、迁移、构建和部署工具、用户与运维文档。真实项目样本、私人名称清单、本机审计报告、AI 工作记录、数据库和凭据不上传。私人关键词扫描是本地可选检查，不属于产品功能，也不作为 CI 的必需项。

## 常见问题

- **PowerShell 不让运行 Activate.ps1**：当前终端运行 `Set-ExecutionPolicy -Scope Process Bypass`，或直接使用 `.venv\Scripts\python.exe`，无需修改整台电脑的策略。
- **psql 找不到**：把 `C:\Program Files\PostgreSQL\<版本号>\bin` 加到系统 PATH，或者直接用 pgAdmin 建库。
- **测试样本**：`backend/tests/fixtures/private/sample_mdl.xlsx` 是真实项目的 MDL，这个文件夹已被 git 忽略，不会提交到仓库；换电脑时要手动复制过去。仓库里只放虚构样本。
