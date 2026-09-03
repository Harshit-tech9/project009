# Agentic AI Architect integration and Phase 1 handoff

This is the primary handoff document for continuing Project009 in a new session.
Read it together with [`README.md`](README.md), the approved
[`Phase 0 specification`](../../docs/superpowers/specs/2026-09-02-project009-phase0-prototype-design.md),
and the product
[`mockup`](../mockups/Agentic_AI_Architect_Product_Mockup.html).

## Current status

Phase 1 is implemented as a local FastAPI + React application around the validated
Python 3.12 core. The CLI remains supported. Both paths provision one pinned
Agent37/Hermes instance, reuse one Hermes session, persist local artifacts, and
delete the instance before the single terminal event.

The definitive Meridian live run passed on 2026-09-02:

- [`assessment.json`](runs/meridian-20260902T163016323807Z/assessment.json)
- [`live_review.json`](runs/meridian-20260902T163016323807Z/live_review.json)
- [`events.jsonl`](runs/meridian-20260902T163016323807Z/events.jsonl)
- [`interview_transcript.json`](runs/meridian-20260902T163016323807Z/interview_transcript.json)

That run confirmed:

- Three adaptive interview questions captured the workflow and economic baseline.
- Exactly three opportunities were generated and ranked.
- `vendor-quotation-comparison` was recommended, selected, and used by the blueprint.
- The blueprint contains business, agent, technical, economic, and pilot sections.
- Hermes processed PDF, XLSX, and PNG samples.
- All 27 comparison sources reused their verified extraction locators.
- The missing XLSX warranty and intentional PNG INR 3,000 discrepancy were flagged.
- The Agent37 deletion event preceded the single terminal completion event.
- A separate instance-list check found zero Project009 instances after the run.
- The current automated suite makes no paid calls.

Phase 1 now includes the local web server, replayable SSE, private validated uploads,
SQLite snapshots, durable human actions, customer-defined requirements, blueprint
confirmation, pre-proof chat, and explicit proof run/skip outcomes. It does **not**
include production authentication, report delivery, email, booking, live Outlook/SAP
integration, or autonomous business actions.

## Repository locations

Preserve the existing names, including the current `blaislogic` spelling:

```text
blaiselogic-website/
├── mockups/
│   └── Agentic_AI_Architect_Product_Mockup.html
├── project009/                 # completed Python agent core
└── blaislogic_frontend/        # existing React 19 + Vite website
```

Important Phase 0 files:

- `architect/pipeline.py` — owns the end-to-end lifecycle and cleanup.
- `architect/contracts.py` — strict Pydantic contracts and integration protocols.
- `architect/client.py` — Agent37 hosting and instance API client.
- `architect/stages/` — research, interview, opportunities, blueprint, and proof.
- `architect/persona.py` — deterministic Phase 0 interaction implementation.
- `architect/events.py` — redacted JSONL progress and artifact helpers.
- `config/companies.yaml` — Meridian configuration and synthetic persona.
- `run_assessment.py` — CLI composition root; it is not a reusable web endpoint.

## Phase 1 objective

Build an internal web application matching the seven-stage mockup while preserving
the validated agent core. A user should be able to start and finish one assessment
in the browser without running the CLI.

Stable product stages:

`start -> research -> interview -> opportunities -> blueprint -> proof -> results`

The current core `Assessment.status` supports `completed` and `failed`. The Phase 1
API view will also need lifecycle states such as `queued`, `running`,
`waiting_for_user`, `cancelling`, and `cancelled`. Those lifecycle states do not add
or rename product stages. Decide whether `cancelled` extends the core contract or is
an API-only state before implementing routes, and cover the decision with tests.

Phase 1 should support:

1. Starting an assessment from browser input.
2. Watching live progress and reconnecting after a refresh.
3. Confirming or correcting research.
4. Answering one interview question at a time, with a maximum of six.
5. Selecting one of three opportunities.
6. Uploading PDF, XLSX, and PNG files.
7. Viewing the five blueprint sections and proof comparison.
8. Seeing assumptions, sources, confidence, review flags, risks, economics, usage,
   failure status, and pilot roadmap.

PDF download, report email, strategy-call booking, DOCX/CSV, production auth, and
live Outlook/SAP integrations remain later work unless the Phase 1 scope is
explicitly changed.

## Target architecture

```text
React/Vite browser
      │ HTTPS + SSE
      ▼
Python server-side adapter
      ├── AssessmentCoordinator
      ├── FrontendAssessmentInteraction
      ├── SSEProgressSink
      └── RunStore / upload storage
                │
                ▼
       existing run_assessment(...)
                │
                ▼
          Agent37 / Hermes
```

The frontend must never call Agent37 directly. It must never receive the Agent37
key, hosting authorization header, instance key header, raw instance URL, or remote
workspace paths.

FastAPI is the recommended Phase 1 server because the core is already asynchronous
Python with Pydantic contracts. Suggested additions are `fastapi`, `uvicorn`,
`python-multipart`, and an async SQLite library. This is a Phase 1 recommendation,
not functionality that currently exists.

## Implemented integration constraints

- One coordinator worker runs one assessment at a time; additional assessments queue.
- Human actions and browser-safe SSE events are durable in SQLite and use independent
  monotonic stream sequences.
- Every human wait has a 15-minute timeout. Refresh/reconnect is supported, but an
  interrupted Hermes turn cannot resume after a hard server restart.
- Uploaded files remain outside the public frontend, are assessment-owned, and are
  locked after proof consumption.
- The browser receives safe projections only, never instance/session identifiers,
  remote paths, credentials, raw turns, or cleanup metadata.
- Proof analysis shows all six steps active together and completes them together;
  the UI does not fabricate per-step timing.

## Existing boundaries to preserve

### `AssessmentInteraction`

The core calls this interface whenever it needs a human decision:

- `confirm_research(research)`
- `answer_interview(question, progress)`
- `select_opportunity(opportunities, recommended_id)`
- `confirm_blueprint(blueprint)`
- `next_proof_action(blueprint)`
- `record_proof_reply(content)`

Phase 0 uses `ScriptedPhase0Interaction`. Phase 1 uses
`FrontendAssessmentInteraction`, which publishes a pending action and asynchronously
waits for the matching browser response.

Every pending action needs:

```json
{
  "action_id": "stable-random-id",
  "assessment_id": "assessment-id",
  "type": "confirm_research | answer_interview | select_opportunity | confirm_blueprint | proof_workspace",
  "stage": "research | interview | opportunities | blueprint | proof",
  "payload": {},
  "created_at": "UTC timestamp"
}
```

The server must accept only the current pending `action_id`. Duplicate submissions
should be idempotent; stale or mismatched actions should return HTTP 409.

### `ProgressSink`

The core emits ordered `AssessmentEvent` objects. Phase 0 appends them to JSONL.
Phase 1 uses a composite sink that preserves JSONL artifacts, persists browser-safe
events, and publishes them to connected SSE clients.

Existing core events, in normal order:

```text
workspace_starting
assessment_started
research_started
company_research.completed
interview_started
workflow_interview.completed
opportunities_started
opportunities.generated
opportunity.selected
blueprint_started
blueprint.generated
blueprint.confirmed
proof_started
proof_chat.started | proof_chat.completed (zero to three exchanges)
document_processed (once per document)
proof_completed | proof_skipped
results_preparing
agent_deleted
assessment_completed | assessment_failed | assessment_cancelled
```

Pending human actions are not core `AssessmentEvent` objects. The server adapter
exposes them as separate SSE event types such as
`interaction_required` and `interaction_accepted`, without changing the stable core
stage names.

### `run_assessment(...)`

This function remains the orchestration entry point and returns a serializable
`Assessment`. Stage modules must remain independent of FastAPI, React, SQLite, and
HTTP concerns.

The coordinator supplies the API-generated `assessment_id`, and an injected
`RunStore` preserves filesystem artifacts while projecting safe completed-stage data
into SQLite.

Keep the existing cleanup invariant: an Agent37 instance must be deleted on success,
failure, cancellation, or server shutdown. Cleanup failure makes the assessment
unsuccessful.

## Recommended API contract

All routes are server-side routes under `/api`. Exact names may change once, before
frontend implementation; after that, version the contract instead of silently
changing it.

### Create and read an assessment

```http
POST /api/assessments
GET  /api/assessments/{assessment_id}
GET  /api/assessments/{assessment_id}/events
POST /api/assessments/{assessment_id}/cancel
```

Initial creation accepts customer-supplied company and workflow requirements:

```json
{
  "company_name": "Northstar Health",
  "website": "https://northstar.example/",
  "participant_role": "Operations Director",
  "industry": "Healthcare operations",
  "workflow_challenge": "Triage incoming cases without repeated manual review.",
  "desired_outcome": "Reduce triage time while preserving human review.",
  "proof_goal": "Verify case priority and SLA assignment.",
  "proof_fields": ["case ID", "priority", "SLA"]
}
```

Return HTTP 202 with an API-generated ID, initial status, stage, and event URL.
Run the assessment as a background task controlled by an
`AssessmentCoordinator`; do not hold the create request open for the full run.

The GET response should use a browser-safe `AssessmentView`, not serialize the
internal `Assessment` blindly. Exclude `instance_id`, `instance_url`, image digest,
remote file paths, raw Hermes turns, request headers, and secrets.

### Submit human actions

```http
POST /api/assessments/{assessment_id}/actions/{action_id}
```

Use a discriminated payload based on the pending action type:

- Research: `{ "accepted": true }` or a validated corrected research object.
- Interview: `{ "answer": "..." }`.
- Opportunity: `{ "selected_id": "..." }`.
- Documents: multipart upload followed by an action referencing stored document IDs.

Reject an answer that does not match the assessment's current pending action.

### Upload documents

```http
POST /api/assessments/{assessment_id}/documents
DELETE /api/assessments/{assessment_id}/documents/{document_id}
```

Phase 1 accepts only PDF, XLSX, and PNG. Validate extension, MIME type, actual file
signature, size, document count, and sanitized generated filenames. Store files
outside the Vite public directory and never accept a browser-supplied filesystem or
Agent37 remote path.

### SSE behavior

`GET /events` should:

- Send persisted events first, followed by live events.
- Use a monotonically increasing stream sequence and SSE `id`.
- Honor `Last-Event-ID` so refresh/reconnect does not lose progress.
- Send periodic heartbeat comments.
- Finish only after `assessment_completed`, `assessment_failed`, or `cancelled`.
- Never expose secrets or internal Agent37 connection details.

Recommended SSE names are `snapshot`, `assessment_event`,
`interaction_required`, `interaction_accepted`, `heartbeat`, and `terminal`.

## Persistence and execution model

For the internal Phase 1 application, SQLite plus local filesystem/object storage is
acceptable:

```text
assessments       status, stage, timestamps, company input, run directory
pending_actions   action ID, type, payload, status, response, timestamps
events            stream sequence, event type, redacted JSON payload
documents         document ID, safe path, format, size, checksum
```

The first implementation may use one API process and in-memory `asyncio` tasks, but
assessment state, actions, and events must be persisted. Document clearly that an
in-flight Agent37 call cannot be resumed after a hard process crash. Production
workers, distributed queues, and cross-process coordination belong to Phase 3.

Start with a concurrency limit of one live Agent37 assessment. Queue additional
assessments and show `queued` in API status while keeping the seven UI stage names
unchanged.

## Frontend mapping

Add a dedicated route such as `/agentic-ai-architect` to
`blaislogic_frontend`. Do not replace the existing home and insight routes.

Suggested frontend structure:

```text
src/
├── pages/AgenticArchitectPage.jsx
├── components/architect/
│   ├── AssessmentShell.jsx
│   ├── StartStage.jsx
│   ├── ResearchStage.jsx
│   ├── InterviewStage.jsx
│   ├── OpportunitiesStage.jsx
│   ├── BlueprintStage.jsx
│   ├── ProofStage.jsx
│   └── ResultsStage.jsx
└── lib/architectApi.js
```

Render directly from the existing contracts:

- Research: summary, fact cards, source links, and assumptions.
- Interview: one current question, progress up to six, transcript, and summary.
- Opportunities: exactly three cards, six scores, rank, recommendation, selection.
- Blueprint: business, agent, technical, economic, and pilot tabs.
- Proof: six progress steps, documents, customer-defined comparison rows, locators, confidence,
  review flags, discrepancies, and conditional recommendation.
- Results: combined assessment, economics, risks, assumptions, usage, and pilot plan.

On refresh, load the GET snapshot first and then connect to SSE. The browser should
derive its screen from server state rather than assuming that its last local stage is
still current.

## Security and product-truth rules

- `AGENT37_API_KEY` is backend-only and loaded from the process environment or
  backend `.env`; process environment takes precedence.
- Never accept the key in a browser payload, query parameter, or CLI argument.
- Never log or persist request headers, environment variables, keys, or raw secrets.
- Keep all external actions read-only or explicitly behind human approval.
- Never claim Outlook, SAP Business One, or another integration exists until it has
  actually been implemented and tested.
- Keep sourced public research separate from user-provided workflow facts and
  assumptions.
- Treat uploaded documents as untrusted input and prevent path traversal, executable
  uploads, public serving, and cross-assessment access.
- Validate website URLs and block localhost, link-local, private-network, and other
  SSRF targets before using customer-supplied URLs.
- Preserve the 0.80 confidence boundary and source-locator requirements.
- Do not describe an agent recommendation as an approval or autonomous decision.

Authentication and multi-tenant authorization are required before any external
customer beta. An unauthenticated Phase 1 build must remain local/internal and must
not be publicly deployed.

## Phase 1 implementation sequence

1. Add optional caller-supplied assessment IDs and a `RunStore` boundary without
   changing stage behavior.
2. Implement and test `AssessmentCoordinator`, durable pending actions, cancellation,
   and shutdown cleanup using a mocked Agent37 client.
3. Add the FastAPI create/read/action/upload/SSE routes and browser-safe DTOs.
4. Add API contract, reconnect, upload-security, failure, and cleanup tests.
5. Build the React route and seven stage components against a mocked API.
6. Connect the real API, verify refresh/reconnect, and test responsive behavior.
7. Run the complete automated suite without live Agent37 provisioning.
8. After explicit approval, perform one capped non-Meridian browser assessment and record a Phase 1 verdict.

Do not begin with visual polish or report-email buttons. Establish the lifecycle,
pending-action, persistence, SSE, cancellation, and cleanup contracts first.

## Phase 1 acceptance criteria

Phase 1 is complete only when:

- A user completes all seven stages from the browser without the CLI.
- Research confirmation, interview answers, opportunity selection, and document
  uploads pause and resume the same assessment correctly.
- Refreshing or reconnecting reconstructs the current state without duplicate events.
- Exactly one Hermes session is reused for the assessment.
- The browser never receives Agent37 credentials or internal instance details.
- Invalid or stale action submissions cannot advance the workflow.
- PDF, XLSX, and PNG uploads are validated and isolated by assessment.
- Failure, cancellation, and server shutdown all attempt confirmed Agent37 cleanup.
- `agent_deleted` precedes exactly one terminal assessment event after an instance
  has been created.
- Automated API/frontend tests use a mocked Agent37 client and incur no cost.
- One capped customer-defined browser run retains the evidence, confidence, and cleanup
  quality bar established by the Phase 0 Meridian assessment.

## Deferred phases

### Phase 2 — design-partner pilot

Add saved company profiles, reusable workflow templates, correction evaluation
datasets, DOCX/CSV support, downloadable reports, and pilot metrics. Keep the system
advisory and read-only.

### Phase 3 — private beta and production hardening

Add authentication, organizations and tenant isolation, production database/object
storage, durable workers, secret management, quotas, monitoring, retention/deletion,
versioned evaluations, and carefully scoped read-only business-system integrations.

### Phase 4 — controlled integrations and scale

Add reusable workflow templates, billing/admin capabilities, report delivery, and
approved integrations. Any vendor communication, ERP write, order creation, or other
business action requires explicit authorization, human approval, auditability, and a
separate safety review.

## New-session starting instruction

Use this prompt when beginning Phase 1 in a new coding session:

> Read `project009/integration.md`, the linked Phase 0 specification, and the current
> code before editing. Phase 0 has passed and its agent stages must be preserved.
> Implement Phase 1 in the documented sequence, starting with the caller-supplied
> assessment ID, RunStore boundary, coordinator, and frontend interaction adapter.
> Keep Agent37 credentials backend-only, mock all paid API calls in automated tests,
> preserve cleanup guarantees, and do not add autonomous business actions.
