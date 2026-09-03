# Project009 - Agentic AI Architect Phase 0

An internal, CLI-only prototype of the BlaiseLogic Agentic AI Architect. It runs one
Agent37/Hermes session through company research, a six-question workflow interview,
opportunity ranking, blueprint generation, and a vendor-quotation proof of work.

The approved design is at
[`../../docs/superpowers/specs/2026-09-02-project009-phase0-prototype-design.md`](../../docs/superpowers/specs/2026-09-02-project009-phase0-prototype-design.md).
The future frontend boundary is documented in [`integration.md`](integration.md).

## Setup

Python 3.12 is the target runtime (also declared in `.python-version`).

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Put the real Agent37 key in `.env` as `AGENT37_API_KEY`. Do not pass it as a CLI
argument and do not commit `.env`. A process-level environment variable takes
precedence over the file.

The checked-in template pin is the immutable example published in Agent37's official
template documentation. Each assessment preflights that exact pin with
`GET /v1/templates/{pin}` before provisioning. Replace `AGENT37_TEMPLATE` if your
workspace should use a newer published pin.

## Generate the sample quotations

```powershell
python sample_docs/generate_samples.py
```

This creates a text PDF, a structured XLSX, and a scan-style PNG. The binaries and
the generated ground-truth manifest are intentionally ignored by git. Project009
uploads their exact bytes; it does not parse them.

## Run

```powershell
python run_assessment.py --company meridian
python run_assessment.py --all
python run_assessment.py --company meridian --budget-credit-micros 1000000
python run_assessment.py --company meridian --template agent37-hermes@2026.07.02b
```

For one small, visible streaming call to an already-running Agent37 Hermes instance,
run:

```powershell
python agent37_hermes_smoke.py https://y7084lkgt3.agent37.app
```

The script sends the same payload as the Playground (`Hi Hermes`, streaming enabled,
low reasoning effort) and prints the raw SSE events. It never prints request headers
or the API key. The supplied instance must already be running; this script does not
create or delete it.

Every run writes raw turns, parsed stages, a redacted manifest, the final assessment,
and MetricAI-shaped events to `runs/<company>-<UTC timestamp>/`. The Agent37 instance
is deleted at the end of successful and failed runs.

## Validate without spending money

```powershell
pytest -q
```

## Run the Phase 1 web application locally

The architect UI lives in this repository under `frontend/` (not in
`blaislogic_frontend`). The marketing site links out to this app over HTTP only.

```powershell
cd frontend
npm ci
npm run build
cd ..
uvicorn webapp.app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/agentic-ai-architect`. The unauthenticated Phase 1
application is intentionally local-only. Assessment state and uploads remain under
`project009/runtime/` until manually removed.

For Vite HMR during UI work, run the API and frontend separately:

```powershell
# terminal 1
uvicorn webapp.app:app --host 127.0.0.1 --port 8000

# terminal 2
cd frontend
npm run dev
```

Then open `http://127.0.0.1:5174/agentic-ai-architect` (Vite proxies `/api` to port 8000).

Optional: set `FRONTEND_DIST` to override the built SPA path served by FastAPI.
Optional: set `VITE_MARKETING_SITE_URL` in `frontend/.env` for the brand home link.

The test suite mocks Agent37 completely. It must never provision an instance.

## Phase 0 / Phase 1 boundary

Phase 0 remains the CLI-only core under `architect/`. Phase 1 adds `webapp/` and
`frontend/` in this same product repository. There is still no production auth,
report delivery, email, booking, or live Outlook/SAP integration.
