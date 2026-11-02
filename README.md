<div align="center">

# docbase

**Self-hosted document management for equipment and production-line projects**

[![checks](https://github.com/yiwillz/docbase-project-document-management-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/yiwillz/docbase-project-document-management-platform/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![Vue](https://img.shields.io/badge/vue-3-4FC08D?logo=vuedotjs&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/postgresql-16%20%7C%2017-4169E1?logo=postgresql&logoColor=white)

English | [简体中文](README.zh-CN.md)

</div>

---

## About

**Status: v1 pilot acceptance in progress.** Core workflows are implemented and basic Windows deployment has been verified locally. AI is not implemented; capacity, performance and failure-recovery targets are not fully accepted. Documentation (Chinese): [architecture and acceptance](docs/architecture.md) · [user guide](docs/user-guide.md).

When a company delivers machines or a complete production line, every piece of equipment comes with a stack of documents the client expects:

- datasheets and drawings;
- manuals and wiring diagrams;
- certificates and test reports.

On a project with hundreds of machines, this means thousands of files from the company itself and from its suppliers, each going through several revisions. In practice the list of what is owed lives in an Excel sheet, the files sit in shared folders with inconsistent names, and finding what is still missing is manual work before every client meeting.

docbase tracks this per project and per machine:

- **What is owed.** The project structure (milestones, document types, modules) and the equipment list define every expected document.
- **What has arrived.** Uploaded files are recognised, named, versioned and filed automatically.
- **What the client has approved.** Transmittals and review codes record each submission and reply.
- **What is still missing.** Missing and overdue documents are visible for each machine and each supplier.

The document register the client asks for is generated from the same data, whatever it is called on the project (MDL, VDR, submittal list). So are delivery packages and handover folders.

It suits equipment manufacturers, automation and production-line integrators, and turnkey / EPC contractors. Each company runs its own single-tenant installation on its intranet, on Windows or Linux.

## Features

- **Project structure.** Milestones, document types and modules, set up in the platform. Use standard templates or copy from another project.
- **Equipment register.** Import from Excel with a preview. Equipment can be counted by quantity and tags are optional. Full history with restore.
- **Deliverables generated automatically.** One per linked equipment entry, per module, or per document type, depending on its configuration.
- **Upload without forms.** Drop a batch of files: the platform recognises the deliverable from the file and folder names and the equipment list, and you confirm.
- **Automatic naming and versioning.**
  - Naming follows a per-project template.
  - Re-uploading within the same deliverable with the same title and format creates the next version.
  - Vendor and internal files keep the latest three versions by default; older versions are recycled for 90 days. Client files and versions referenced by transmittals or baselines are protected.
- **Deliverable matrix.** Completion, received, outstanding and overdue at a glance. Filters can be shared as a link.
- **Client document register (MDL / VDR).** Exported to Excel or PDF in a uniform format, with recorded revisions, a diff against the previous issue, and import of the client's reply.
- **Transmittals and review codes.**
  - Configurable review codes.
  - Reply sheets: export, the client fills them in, import them back.
  - Close-out checks per milestone.
- **Delivery.**
  - Zip packages with a file index.
  - A read-only mirror to a shared drive.
  - Stage baselines that can be compared and re-exported.
- **Legacy import.** Scan existing folders: de-duplicate, merge versions, review a dry-run report, then import in the background. A whole batch can be undone.
- **Accounts and audit.** Roles and project membership. Every change is logged field by field (before → after, who, why) and can be exported.
- **Operations.**
  - Scheduled backups when configured and the worker is running; completeness checks and restore drills are required.
  - Daily log files.
  - Storage-offline alarm.
- **Bilingual UI.** Chinese and English.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, openpyxl |
| Database | PostgreSQL 16 / 17 |
| Frontend | Vue 3, TypeScript, Vite, Element Plus, vue-i18n |
| Testing | pytest, Vitest, Playwright |
| Deployment | Windows services (WinSW + Caddy), Docker Compose, systemd |
| CI | GitHub Actions defines backend/frontend checks and Linux install jobs; consult the run for the relevant commit |

## Architecture

```
 Browser ── Vue 3 SPA
    │  HTTPS (Caddy / Nginx)
    ▼
 FastAPI ── services (business logic)
    │          ├── storage: local disk, content-addressed (SHA-256)
    │          └── outbox events, written in the same transaction
    ▼
 PostgreSQL ◄── worker: job table + leases
                (import, packages, mirror sync, cleanup, backup)
```

- **Background jobs.** A database table and leases coordinate work. Imports save per-item progress; interrupted packages are rebuilt. Long-running lease renewal needs validation.
- **Content-addressed storage.** Identical content shares a blob. Files are persisted before database commit; failed transactions can leave orphan blobs for cleanup. This is not an atomic disk/database transaction.
- **Configuration.** Naming templates, review codes and status wording are configured per project. Roles are stored in the database and assigned through project membership.
- **Route-level permissions.** A single rule table covers every API route, and a test fails if a new route has no rule.

## Install and Use

On a new Windows x64 computer, extract the complete current release package to a short path and double-click **`install.cmd`**. Accept the administrator prompt. Initial online installation requires internet access and App Installer / winget; it prepares Python 3.12, PostgreSQL 17 and the services. Git and Node are not needed for a release package.

Open `https://localhost` after installation. The initial account is `admin`; read its generated password as an administrator from `C:\docbase\config\setup-credentials.json`, then change it. Services start automatically. LAN access requires a configured server address and client certificate trust.

With the complete source tree, run from the repository root:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\windows\install.ps1
```

Source installation also prepares the frontend build environment. Offline installation requires preinstalled Python/PostgreSQL and `install.ps1 -Offline` from a complete release package. See the [Windows](docs/install-windows.md) or [Linux](docs/install-linux.md) operations guide.

## Developer Setup

### Prerequisites

- Python 3.12
- PostgreSQL 16 or 17
- Node.js 22

### Setup

```bash
git clone https://github.com/yiwillz/docbase-project-document-management-platform.git
cd docbase-project-document-management-platform

# databases
psql -U postgres -c "create database docbase;"
psql -U postgres -c "create database docbase_test;"

# backend
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cp .env.example .env               # set the database URLs and DOCBASE_DATA_DIR
python scripts/test_server.py      # http://localhost:8001, migrations run automatically

# frontend (another terminal, starting at the repository root)
cd frontend
npm ci
npm run dev                        # http://localhost:5173
```

Run `python scripts/worker.py --test` in a third terminal with the backend environment activated for imports and delivery jobs. Full Windows development instructions are in [setup.md](docs/setup.md).

### Test-database Accounts Only

The test server creates these accounts on start:

| Username | Password | Role |
|---|---|---|
| `tester` | `tester123` | System admin |
| `test_admin` | `test12345` | Project admin |
| `test_member` | `test12345` | Member |
| `test_viewer` | `test12345` | Read-only |

## Deployment

- **Windows 10/11 x64:** online environment preparation or offline installation with prerequisites; the backend, worker and reverse proxy run as Windows services. See [docs/install-windows.md](docs/install-windows.md).
- **Linux:** Docker Compose, or systemd with an offline package. See [docs/install-linux.md](docs/install-linux.md).

Database migrations run automatically on upgrade. Backup and restore are built in.

## Testing

```bash
cd backend
python scripts/check.py            # lint, backend tests, frontend checks, end-to-end tests
python scripts/check.py --quick    # backend lint and frontend lint; no tests
```

The repository includes pytest, Vitest and Playwright tests. Database tests use a separate test database; pure unit tests do not require one. Backend tests rebuild the test schema, so do not share it with an active manual test session. Use actual run output for counts and results.

## Roadmap

- **v1: Document base — pilot acceptance in progress.** Core workflows are implemented. Evidence is still needed for 80 GB imports, 90% classification accuracy, 20 concurrent users and fault recovery.
- [ ] **v2: AI.** Knowledge-base Q&A over project documents, AI pre-review of submittals.
- [ ] **v3: Workflow.** Internal approval flows, push to other project management platform like jira.

## Documentation and Repository Scope

| Task | Documentation (Chinese) |
|---|---|
| Architecture and acceptance gaps | [Architecture](docs/architecture.md) |
| Daily use | [User guide](docs/user-guide.md) |
| Requirements | [PRD](docs/PRD.md) |
| Installation and operations | [Windows](docs/install-windows.md) / [Linux](docs/install-linux.md) |
| Development | [Setup](docs/setup.md) |
| Data model and migration | [Database](docs/database.md) / [Machine migration](docs/migrate-machine.md) |

The repository includes source, tests, migrations, CI, release/deployment tools and product documentation. Private samples, personal keyword lists, machine-specific audit records, AI work notes, credentials, environments, logs and databases stay local.

Git commit and push do not require Python. There is no mandatory commit hook; developers run `check.py` explicitly. CI prepares its own runner environment and does not depend on a local virtual environment or private keyword list.

## License

[Apache License 2.0](LICENSE)
