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
- **自动生成应交项**：按文件种类的类型，每个关联设备、每个模块或每个里程碑各生成一个。
- **上传不用填表**：拖入一批文件，平台根据文件名、文件夹名和设备清单识别对应的应交项，人只需确认。
- **自动命名和升版**：
  - 按项目配置的命名模板生成文件名；
  - 同标题、同格式的文件再上传就自动升版；
  - 旧版本进回收区，保留 90 天。
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
  - 每天自动备份，可做恢复核对；
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
| CI | GitHub Actions：跑测试，再在干净的 Linux 机器上用 Docker 和 systemd 各装一遍 |

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

- **不用 Redis / Celery**：后台任务放在数据库任务表里，用租约领取。worker 在 Windows 上也能跑，崩溃后能接着做。
- **内容寻址存储**：相同内容只存一份；文件和数据库记录要么都写入，要么都不生效。
- **配置而不是写死**：命名模板、审核代码、状态文字、角色都按项目配置。
- **按路由的权限规则**：所有接口都由一张规则表管，新加的接口没有规则时测试会失败。

## 快速开始

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

# 前端（另开一个终端）
cd frontend
npm install
npm run dev                        # http://localhost:5173
```

### 演示账号

测试后端启动时会自动建好这些账号：

| 用户名 | 密码 | 角色 |
|---|---|---|
| `tester` | `tester123` | 系统管理员 |
| `test_admin` | `test12345` | 项目管理员 |
| `test_member` | `test12345` | 成员 |
| `test_viewer` | `test12345` | 只读 |

## 部署

- **Windows 10/11**：离线安装包，把后端、worker 和反向代理注册成 Windows 服务，见 [docs/install-windows.md](docs/install-windows.md)。
- **Linux**：Docker Compose，或 systemd 加离线安装包，见 [docs/install-linux.md](docs/install-linux.md)。

升级时数据库迁移自动执行；自带备份和恢复。

## 测试

```bash
cd backend
python scripts/check.py            # 代码检查、名称扫描、后端测试、前端检查、页面测试
python scripts/check.py --quick    # 只做代码检查和名称扫描
```

后端约 400 个测试，另有前端单元测试和 Playwright 页面测试，全部连单独的测试库运行。

## 路线图

- [x] **第一版：文件基座**。项目结构、应交项、命名、版本、MDL、传送单、交付、存量导入、部署。
- [ ] **第二版：AI**。基于项目文件的知识库问答、提交前 AI 预审。
- [ ] **第三版：流程**。内部审批流，推送到其他的项目平台。

## 许可证

[Apache License 2.0](LICENSE)
