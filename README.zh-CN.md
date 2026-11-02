<div align="center">

# docbase

**面向设备和产线项目的私有化文件管理平台**

[![checks](https://github.com/yiwillz/docbase-project-document-management-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/yiwillz/docbase-project-document-management-platform/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![Vue](https://img.shields.io/badge/vue-3-4FC08D?logo=vuedotjs&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/postgresql-16%20%7C%2017-4169E1?logo=postgresql&logoColor=white)

[English](README.md) | 简体中文

</div>

---

## 简介

当前处于**第一版试点与验收阶段**：主要功能已有实现，Windows 本机部署通过基础验证；AI 功能尚未实现，PRD 的容量、性能和故障恢复指标尚未全面验收。阅读：[架构与验收状态](docs/architecture.md) · [用户指南](docs/user-guide.md)。

交付一批设备或一整条产线时，客户会要求每台设备都附带一整套文件：

- 规格书、图纸；
- 说明书、电气图；
- 合格证、测试报告。

一个几百台设备的项目，加上供应商的资料，文件动辄几千份，每份还要改好几版。现实中，"应该交什么"记在 Excel 里，文件散落在共享盘、命名五花八门，"还缺什么"要在每次开会前靠人工核对。

docbase 按项目、按设备把这件事管起来：

- **应该交什么**：项目结构（里程碑、文件种类、模块）加上设备清单，定出每一份应交文件；
- **已经收到什么**：上传的文件自动识别、命名、升版、归档；
- **客户批了什么**：用传送单和审核代码记录每次提交和客户回复；
- **还缺什么**：按设备、按供应商列出缺件和逾期。

客户要的文件清单从同一份数据生成，不管项目上叫 MDL、VDR 还是资料清单；交付包和移交文件夹也一样。

适用于设备制造商、自动化和产线集成商，以及交钥匙 / EPC 总包。每家企业在自己的内网单独部署一套（单租户），支持 Windows 和 Linux。

## 功能

- **项目结构**：里程碑、文件种类、模块在平台内设置；可用标准模板，或从其他项目复制。
- **设备清单**：从 Excel 导入，先预览再确认；按台数记录，设备编号选填；有完整历史，可恢复。
- **自动生成应交项**：按文件种类的类型，每个关联设备条目、每个模块或整个文件种类生成应交项。
- **上传不用填表**：拖入一批文件，平台根据文件名、文件夹名和设备清单识别对应的应交项，人只需确认。
- **自动命名和升版**：
  - 按项目配置的命名模板生成文件名；
  - 同一应交项下同标题、同格式的文件再上传就自动升版；
  - 供应商和我方默认保留最新 3 版，更早版本进回收区 90 天；客户文件及被传送单/基线引用的版本受保护。
- **应交矩阵**：完成率、已收到、缺件、逾期一目了然；筛选条件可以用链接分享。
- **客户文件清单（MDL / VDR）**：按统一格式导出 Excel / PDF，自动记录版本，和上一版对比，可导回客户回复。
- **传送单和审核代码**：
  - 审核代码可配置；
  - 回复单导出给客户填写，填好后导回；
  - 每个里程碑有闭环检查。
- **交付**：
  - 一键生成 zip 交付包，附文件清单；
  - 共享盘只读镜像；
  - 阶段基线，可对比、可重新导出。
- **存量导入**：扫描已有文件夹，去重、合并版本，先看预演报告，再在后台导入；整批可撤销。
- **账号与审计**：角色和项目成员；每个改动按字段记录（改前 → 改后、操作人、原因），可导出。
- **运维**：
  - 配置备份目录并运行 worker 后按计划备份，需检查完整性并做恢复演练；
  - 日志按天保存；
  - 存储盘断开时报警。
- **界面中英文**。

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.12、FastAPI、SQLAlchemy 2.0、Alembic、openpyxl |
| 数据库 | PostgreSQL 16 / 17 |
| 前端 | Vue 3、TypeScript、Vite、Element Plus、vue-i18n |
| 测试 | pytest、Vitest、Playwright |
| 部署 | Windows 服务（WinSW + Caddy）、Docker Compose、systemd |
| CI | GitHub Actions 定义了后端、前端及 Linux 安装检查；是否通过须查看对应提交的运行结果 |

## 架构

```
 浏览器 ── Vue 3 单页应用
    │  HTTPS（Caddy / Nginx）
    ▼
 FastAPI ── services（业务逻辑）
    │          ├── 存储：本地磁盘，按 SHA-256 内容寻址
    │          └── Outbox 事件，和数据改动在同一事务里写入
    ▼
 PostgreSQL ◄── worker：任务表 + 租约
                （存量导入、交付包、镜像同步、清理、备份）
```

- **后台任务**：存入数据库并用租约领取。导入保存逐项进度，交付包中断后重新生成；长任务续租仍需验证。
- **内容寻址存储**：相同内容复用实体；先落盘后提交数据库，失败可能留下未引用实体，由 worker 清理。不是跨文件系统和数据库的原子事务。
- **配置**：命名模板、审核代码、状态文字按项目配置；角色保存在数据库中，按项目给成员分配。
- **按路由的权限规则**：所有接口都由一张规则表管，新加的接口没有规则时测试会失败。

## 安装并使用（普通用户 / IT）

Windows 新电脑优先使用最新完整发布包：解除锁定并完整解压到短路径，双击 **`install.cmd`**，接受管理员权限提示。首次需联网及可用的 App Installer / winget；自动准备 Python 3.12、PostgreSQL 17 和应用服务，不需要 Git、Node 或手工配置虚拟环境。不要只复制一个 `.ps1`。

完成后本机打开 `https://localhost`，初始账号 `admin`，随机密码以管理员身份读取 `C:\docbase\config\setup-credentials.json`。首次登录后修改密码。服务自动启动，日常不用启动开发服务器；其他内网电脑访问需配置固定 IP 并信任根证书。

完整源码也可双击 `deploy/windows/install.cmd`，或在源码根目录运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\install.ps1
```

源码模式还会准备前端构建环境。离线安装需预装 Python/PostgreSQL，使用完整发布包和 `install.ps1 -Offline`。详情见 [Windows 手册](docs/install-windows.md)；Linux 服务器见 [Linux 手册](docs/install-linux.md)。

## 开发与测试（开发者）

### 环境要求

- Python 3.12
- PostgreSQL 16 或 17
- Node.js 22

### 安装运行

```bash
git clone https://github.com/yiwillz/docbase-project-document-management-platform.git
cd docbase-project-document-management-platform

# 建数据库
psql -U postgres -c "create database docbase;"
psql -U postgres -c "create database docbase_test;"

# 后端
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows：.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cp .env.example .env               # 填数据库连接和 DOCBASE_DATA_DIR
python scripts/test_server.py      # http://localhost:8001，启动时自动迁移数据库

# 前端（另开终端，从仓库根目录执行）
cd frontend
npm ci
npm run dev                        # http://localhost:5173
```

另外在 `backend` 目录激活开发虚拟环境，运行 `python scripts/worker.py --test`，否则导入与交付任务会排队。Windows 完整开发步骤见 [setup.md](docs/setup.md)。

### 仅测试库使用的演示账号

测试后端启动时会自动建好这些账号：

| 用户名 | 密码 | 角色 |
|---|---|---|
| `tester` | `tester123` | 系统管理员 |
| `test_admin` | `test12345` | 项目管理员 |
| `test_member` | `test12345` | 成员 |
| `test_viewer` | `test12345` | 只读 |

## 部署

- **Windows 10/11 x64**：在线自动准备环境或预装环境后的离线安装，把后端、worker 和反向代理注册成 Windows 服务，见 [docs/install-windows.md](docs/install-windows.md)。
- **Linux**：Docker Compose，或 systemd 加离线安装包，见 [docs/install-linux.md](docs/install-linux.md)。

升级时数据库迁移自动执行；自带备份和恢复。

## 测试

```bash
cd backend
python scripts/check.py            # 代码检查、后端测试、前端检查、页面测试
python scripts/check.py --quick    # 后端代码检查 + 前端 lint，不跑测试
```

测试包括后端 pytest、前端 Vitest 和浏览器 Playwright。数据库测试只连接独立测试库，纯单元测试不使用数据库。后端测试会重建测试库结构，不要与正在使用的人工测试库共用。测试数量和结果以实际运行输出为准。

## 路线图

- **第一版：文件基座，试点与验收中**。主要功能已有实现；80 GB 导入、90% 归档正确率、20 人并发和故障恢复仍需取得验收证据。
- [ ] **第二版：AI**。基于项目文件的知识库问答、提交前 AI 预审。
- [ ] **第三版：流程**。内部审批流，推送到其他的项目平台。

## 文档与仓库范围

| 任务 | 文档 |
|---|---|
| 理解架构、实现与验收差距 | [架构与验收状态](docs/architecture.md) |
| 日常使用 | [操作指南](docs/user-guide.md) |
| 需求与验收指标 | [PRD](docs/PRD.md) |
| 安装、升级和维护 | [Windows](docs/install-windows.md) / [Linux](docs/install-linux.md) |
| 修改代码、运行测试 | [开发环境](docs/setup.md) |
| 数据结构、换机器 | [数据库](docs/database.md) / [迁移](docs/migrate-machine.md) |

公开仓库保留源码、测试、迁移、CI、发布/部署脚本和产品文档。测试与构建工具不是用户功能，但用于维护和交付软件，应保留。真实项目文件、私人名称清单、本机审计与安装过程记录、AI 工作笔记、虚拟环境、凭据、日志和数据库不提交。

Git 提交与推送不要求 Python。本项目不再提供强制提交钩子；`check.py` 是开发者主动运行的检查，CI 使用 GitHub runner 自己的环境，不依赖本机虚拟环境或私人名单。

## 许可证

[Apache License 2.0](LICENSE)
