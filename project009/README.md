# Project009 — Agentic AI Architect

BlaiseLogic's **Agentic AI Architect**: an assessment product that runs one Agent37/Hermes session through company research, a guided workflow interview, opportunity ranking, blueprint generation, and a vendor-quotation proof of work.

Phase 0 is the validated CLI core. Phase 1 adds a local FastAPI API and React SPA around that same core. See [`integration.md`](integration.md) for API contracts and Phase 1 handoff detail.

## What it does

A single assessment walks through seven product stages:

```text
start -> research -> interview -> opportunities -> blueprint -> proof -> results
```

| Stage | Outcome |
|--------|---------|
| **Research** | Cited public company facts, assumptions, and human confirmation |
| **Interview** | Up to six adaptive workflow questions with an economic baseline |
| **Opportunities** | Exactly three ranked opportunities; one is selected |
| **Blueprint** | Business, agent, technical, economic, and pilot sections |
| **Proof** | PDF / XLSX / PNG quotation comparison with locators and review flags |
| **Results** | Combined assessment, risks, usage, and pilot roadmap |

Each run provisions one pinned Hermes instance, reuses one session, persists local artifacts, and deletes the instance before the terminal event on success, failure, or cancellation.

## Stack

| Layer | Technology |
|--------|------------|
| Agent core | Python 3.12, Pydantic, httpx, Agent37/Hermes |
| API | FastAPI, uvicorn, aiosqlite, SSE |
| UI | React 19, Vite 8, React Router |
| Tests | pytest (API/core), Vitest + Testing Library (UI) |

## Layout

```text
.
├── architect/            # assessment pipeline, stages, Agent37 client
│   └── stages/           # research, interview, opportunities, blueprint, proof
├── webapp/               # FastAPI adapter, coordinator, SQLite, uploads
├── frontend/             # Architect React SPA
├── config/companies.yaml # Phase 0 company + synthetic persona (e.g. Meridian)
├── sample_docs/          # generators for PDF / XLSX / PNG proof samples
├── tests/                # mocked Agent37 suite (no paid calls)
├── run_assessment.py     # CLI entry point
└── integration.md        # Phase 1 handoff and API contract detail
```

| Path | Role |
|------|------|
| `architect/` | Pipeline, contracts, Agent37 client, stage modules |
| `webapp/` | FastAPI routes, coordinator, SQLite, uploads |
| `frontend/` | React SPA at `/agentic-ai-architect` |
| `config/companies.yaml` | Company configs and synthetic interview persona |
| `run_assessment.py` | CLI composition root |

A separate marketing site (`blaislogic_frontend`) may deep-link here over HTTP only. This app never embeds marketing source, and the browser never talks to Agent37 directly.

## Prerequisites

- Python **3.12** (see `.python-version`)
- Node.js 20+ (for the SPA)
- An **Agent37 API key** for live runs (not required for the automated test suite)

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` and set `AGENT37_API_KEY`. Do not pass the key as a CLI argument or commit `.env`. A process-level environment variable overrides the file.

Optional knobs in `.env`:

| Variable | Purpose |
|----------|---------|
| `AGENT37_API_KEY` | Backend-only Agent37 credential |
| `AGENT37_TEMPLATE` | Pinned template (default `agent37-hermes@2026.07.02b`) |
| `AGENT37_BUDGET_CREDIT_MICROS` | Per-instance spend headroom in USD micros |

## Generate sample quotations

```powershell
python sample_docs/generate_samples.py
```

This creates a text PDF, a structured XLSX, and a scan-style PNG used by the CLI persona.

## CLI (Phase 0)

```powershell
python run_assessment.py --company meridian
python run_assessment.py --all
python run_assessment.py --company meridian --budget-credit-micros 1000000
python run_assessment.py --company meridian --template agent37-hermes@2026.07.02b
```

Artifacts land in `runs/<company>-<UTC timestamp>/` (raw turns, parsed stages, redacted manifest, assessment JSON, MetricAI-shaped events). The Agent37 instance is deleted at the end of successful and failed runs.

Optional smoke against an already-running Hermes instance (does not create or delete it):

```powershell
python agent37_hermes_smoke.py https://<your-instance>.agent37.app
```

## Web app (Phase 1)

### Dev with Vite HMR

```powershell
# terminal 1
uvicorn webapp.app:app --host 127.0.0.1 --port 8000

# terminal 2
cd frontend
npm ci
npm run dev
```

Open http://127.0.0.1:5174/agentic-ai-architect (Vite proxies `/api` to port 8000).

### Production-style local serve

```powershell
cd frontend
npm ci
npm run build
cd ..
uvicorn webapp.app:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/agentic-ai-architect.

Optional: set `FRONTEND_DIST` to override the SPA path FastAPI serves. Set `VITE_MARKETING_SITE_URL` in `frontend/.env` for the brand home link (see `frontend/.env.example`).

### Browser API surface

| Method | Path | Role |
|--------|------|------|
| `POST` | `/api/assessments` | Start assessment (202) |
| `GET` | `/api/assessments/{id}` | Browser-safe snapshot |
| `GET` | `/api/assessments/{id}/events` | SSE progress + interactions |
| `POST` | `/api/assessments/{id}/actions/{action_id}` | Human replies |
| `POST` | `/api/assessments/{id}/documents` | PDF / XLSX / PNG upload |
| `DELETE` | `/api/assessments/{id}/documents/{document_id}` | Remove upload |
| `POST` | `/api/assessments/{id}/cancel` | Cancel + cleanup |

Full lifecycle, SSE, and security rules are documented in [`integration.md`](integration.md).

## Tests

The suite mocks Agent37 completely and must never provision an instance or spend credits.

```powershell
pytest -q

cd frontend
npm test
```

## Security and product boundaries

- `AGENT37_API_KEY` stays on the backend only; the SPA never receives instance URLs, session IDs, or credentials.
- Uploads are assessment-scoped, signature-checked (PDF / XLSX / PNG), size-capped (10 MB), and stored under `runtime/` — not in the Vite public tree.
- Customer website URLs are validated against SSRF targets before use.
- Phase 1 is **unauthenticated and local/internal**. Do not publicly deploy without auth and tenant isolation.
- The product is advisory and read-only: no live Outlook/SAP integration, vendor contact, order creation, or autonomous approvals.

## Current status

- **Phase 0** — CLI core validated (including Meridian live run evidence described in `integration.md`).
- **Phase 1** — Local web app with SSE, durable actions, uploads, blueprint confirmation, and proof run/skip.
- **Later** — Auth, multi-tenant production hardening, report delivery, booking, and scoped business-system integrations (see `integration.md`).

## License

This project is licensed under the [MIT License](../LICENSE).