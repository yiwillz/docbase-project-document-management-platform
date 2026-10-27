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
- **Deliverables generated automatically.** One per linked equipment item, per module, or per milestone, depending on the document type.
- **Upload without forms.** Drop a batch of files: the platform recognises the deliverable from the file and folder names and the equipment list, and you confirm.
- **Automatic naming and versioning.**
  - Naming follows a per-project template, e.g. `3.5.72_Cut sheet-Mixer A-20260226-V1.pdf`.
  - Re-uploading a file with the same title and format creates the next version.
  - Older versions go to a 90-day recycle area.
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
  - Daily backups, with restore verification.
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
| CI | GitHub Actions: tests, then clean installs on Linux via Docker and systemd |

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

- **No Redis or Celery.** Background jobs use a database table with leases, so the worker also runs on Windows and resumes after a crash.
- **Content-addressed storage.** Each file is stored once. A file and its database record are written together or not at all.
- **Configuration over code.** Naming templates, review codes, status wording and roles are stored per project.
- **Route-level permissions.** A single rule table covers every API route, and a test fails if a new route has no rule.

## Getting Started

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

# frontend (in another terminal)
cd frontend
npm install
npm run dev                        # http://localhost:5173
```

### Demo accounts

The test server creates these accounts on start:

| Username | Password | Role |
|---|---|---|
| `tester` | `tester123` | System admin |
| `test_admin` | `test12345` | Project admin |
| `test_member` | `test12345` | Member |
| `test_viewer` | `test12345` | Read-only |

## Deployment

- **Windows 10/11:** an offline installer registers the backend, the worker and the reverse proxy as Windows services. See [docs/install-windows.md](docs/install-windows.md).
- **Linux:** Docker Compose, or systemd with an offline package. See [docs/install-linux.md](docs/install-linux.md).

Database migrations run automatically on upgrade. Backup and restore are built in.

## Testing

```bash
cd backend
python scripts/check.py            # lint, name scan, backend tests, frontend checks, end-to-end tests
python scripts/check.py --quick    # lint and scan only
```

The suite has about 400 backend tests, frontend unit tests and Playwright end-to-end tests. All of them run against a separate test database.

## Roadmap

- [x] **v1: Document base.** Structure, deliverables, naming, versions, MDL, transmittals, delivery, legacy import, deployment.
- [ ] **v2: AI.** Knowledge-base Q&A over project documents, AI pre-review of submittals.
- [ ] **v3: Workflow.** Internal approval flows, push to Procore / Jira.

## License

[Apache License 2.0](LICENSE)
