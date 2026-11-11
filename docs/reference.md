# CareFlow technical reference

Detail behind the README (`README.md` at the repository root): how the code is organised, every setting, the API, the rules CareFlow
follows, test results and the roadmap.

## Contents

1. [Code layout and services](#architecture)
2. [How the agent workflow runs](#agent-workflow)
3. [Departments, blockers and actions](#departments-blockers-and-actions)
4. [LLM agents and Groq](#llm-agents-and-groq)
5. [Database connections (Neon)](#database-connections-neon)
6. [Performance](#performance)
7. [All settings](#configuration)
8. [API](#api)
9. [Evaluation results](#evaluation-results)
10. [What's real, what's simulated](#whats-real-whats-simulated)
11. [Future work](#future-work)

## Architecture

```
web/       Next.js 16 frontend: setup, patients, work queues, board, approvals, operations, audit, users, report
backend/   FastAPI + LangGraph + SQLAlchemy (Python 3.12+)
  app/agents/graph.py        the orchestration graph (10 nodes, parallel fan-out, interrupt/resume)
  app/agents/llm_agents.py   4 LLM agents with typed outputs, validators and deterministic fallbacks
  app/agents/checks.py       deterministic department checks that produce evidence
  app/policy.py              action catalog, risk levels, approver roles, forbidden actions
  app/security.py            roles, permissions, password hashing, JWT/OIDC
  app/engine.py              events -> graph runs, approvals -> resumes, locks, deferral
  app/events.py              Redis Streams consumer group, retries, dead-letter stream
  app/worker.py              standalone event consumer (the `worker` containers)
  app/fhir.py                FHIR R4 client (capabilities, pagination, If-Match versioning)
  app/rag.py                 SOP retrieval with versions, expiry, department filter, citations
  app/redaction.py           PHI redaction and prompt-injection detection
  app/departments.py         department records and staff actions
  app/sim.py                 department record store (fault injection used by evals)
  app/api/hospital.py        setup, users, patients, department queues, reports
  app/api/routes.py          workflows, approvals, audit, analytics, events, health
  app/api/executive.py       director dashboard
  app/api/integrations.py    HL7 over HTTPS, JSON connectors, integration keys, message log, beds
  app/integrations/hl7.py    dependency-free HL7 v2 parser and ACK / ADT^A03 builder
  app/integrations/mllp.py   MLLP listener (the `hl7` container) and a command-line sender
  app/integrations/service.py inbound processing shared by HL7 and JSON (patients, departments, beds)
  app/integrations/outbound.py signed webhooks and HL7 to hospital systems, with retries and logging
  app/cache.py               Director dashboard cache (Redis, 15 s)
  app/dbstats.py             per-request SQL counter for benchmarking
  scripts/bench.py           benchmark: authorization latency and dashboard cache
  app/cli.py                 create-admin, reset-password, load-examples, sync-beds
  alembic/                   database migrations
  tests/                     65 pytest tests
evals/     30 YAML scenarios + scoring runner + latest results
deploy/    Caddyfile (HTTPS) and backup script for docker-compose.prod.yml
docs/      user guide, manual test plan, deployment, integrations, FHIR, security and compliance
```

**Runtime services** (from `docker-compose.yml`):

| Service | Purpose | Port |
| --- | --- | --- |
| `postgres` | All application data and LangGraph checkpoints (pgvector image). Unused when `DATABASE_URL` points at Neon | 5432 |
| `redis` | Event stream (`careflow:events`), per-workflow locks, rate limiting | 6379 |
| `api` | FastAPI app. Runs `alembic upgrade head` on start | 8000 |
| `worker` ×2 | Event consumers in one consumer group | — |
| `hl7` | HL7 v2 MLLP listener | 2575 |
| `web` | Next.js frontend | 3000 |
| `hapi` | Optional FHIR server (`--profile fhir`) | 8080 |
| `caddy`, `backup` | Production only (`docker-compose.prod.yml`): HTTPS proxy and nightly database backups | 80, 443 |

**Where Redis is used.** Redis carries the event stream for integration updates (HL7, JSON connectors, `/api/events`), the locks that keep one run per workflow
across processes, and the rate-limit counters. Actions staff take in the web app run inline and don't go through
the stream. Redis is optional: if it's unreachable at startup, the API logs `Redis unavailable` and processes
events inline instead. `GET /api/health` reports whether Redis is in use.

## Agent workflow

```
CLINICAL_DISCHARGE_AUTHORIZED
  -> load_context   FHIR or local store; stops here if no consent is recorded
  -> plan           Workflow Planner (LLM): departments + dependencies, validated against payer rules
  -> check x N      deterministic reads of each department system, in parallel (LangGraph Send)
  -> analyze        Blocker Analyzer (LLM): ranks and explains; every blocker must cite retrieved evidence
  -> resolve        Resolution Planner (LLM) picks a catalog action and cites the SOP; Python policy check decides
  -> approval       interrupt() until a person with the right role decides (any order, survives restarts)
  -> execute        idempotent write, after re-checking the blocker still exists
  -> verify         re-read the department system; a no-op write reopens a blocker
  -> plan (re-check what changed) ... -> report   Reporter (LLM) writes the summary
```

A run ends when nothing more can happen until a department system changes. The next event (staff action,
`PHARMACY_TASK_UPDATED`, ...) resumes the same LangGraph thread and re-checks only that department and its
dependents.

**Workflow statuses:** `blocked` → `awaiting_approval` → `cleared` (ready to discharge) → `discharged`.

**Deferral.** Events that arrive while a workflow is waiting for an approval are recorded but not processed. They
replay as soon as the last pending decision is made. While an approval is pending, the board therefore shows
the state from before those updates. This is by design.

## Departments, blockers and actions

### Which departments a patient needs

| Department | When |
| --- | --- |
| Documentation, Pharmacy, Billing | Every patient |
| Insurance | Cashless insurance or government scheme |
| Laboratory | "Has lab orders" ticked on admission |

For insured patients, Billing waits on Insurance.

### Staff actions (work queue buttons)

| Department | Actions |
| --- | --- |
| Documentation | Tick completed fields (12 required), mark in review |
| Pharmacy | Assign pharmacist, Medicines ready, Reconciliation complete |
| Laboratory | Add report, Mark final, Ward acknowledged (final reports only) |
| Insurance | Submit to payer, Payer asked for this, We have this document, Payer approved, Payer denied, Convert to self-pay |
| Billing | Start final bill, Final bill ready |

### Blockers CareFlow detects

| Blocker | Trigger |
| --- | --- |
| Incomplete document | Any of the 12 required discharge-summary fields missing (unless the document is in review) |
| Reconciliation incomplete | Medicines ready but reconciliation not done |
| Pharmacy delay | Medicines not ready more than `SLA_PHARMACY` (60) minutes after the order time (set at discharge authorization, or by the pharmacy system) |
| Not submitted | Insurance authorization not sent to the payer |
| Payer delay | Payer silent more than `SLA_INSURANCE` (90) minutes after submission |
| Payer query | Payer asked for a document |
| Payer denied | Needs a person; no automated fix is proposed |
| Unacknowledged lab report | A final report the ward hasn't acknowledged |
| Billing delay | Bill in preparation more than `SLA_BILLING` (45) minutes since the last update |
| Waiting on dependency | Billing waiting on insurance |
| Action not applied | CareFlow wrote a change, but re-reading the source system shows it didn't land |

### Action catalog

| Action | Departments | Risk | Approver |
| --- | --- | --- | --- |
| `notify_department` | all | low | runs automatically |
| `create_followup_task` | all | low | runs automatically |
| `create_document_task` | documentation | low | runs automatically |
| `request_lab_acknowledgement` | laboratory | low | runs automatically |
| `escalate_pharmacy_task` | pharmacy | medium | Discharge coordinator |
| `retry_portal_submission` | insurance | medium | Insurance desk lead |
| `attach_document_to_claim` | insurance | high | Insurance desk lead |

**Never executable**, whatever an agent or a document says: `authorize_discharge`, `mark_medically_fit`,
`prescribe_medication`, `modify_medication`, `approve_claim`, `record_payment`, `modify_invoice`.
Administrators and hospital directors can decide any approval.

## LLM agents and Groq

The four agents (Workflow Planner, Blocker Analyzer, Resolution Planner, Reporter) call Groq through
`langchain-groq`. They use a small model for planning and reporting and a large model for analysis. Set
`CAREFLOW_GROQ_API_KEY` to turn them on.

Without a key, or when Groq is down, rate-limited, or returns output that fails validation, each agent runs its
deterministic fallback, and the run is recorded with `mode=fallback`. The LLM can explain, rank and choose. It can't
add a blocker without evidence, or choose an action outside the catalog.

Before any prompt leaves the process, patient names, MRNs, phone numbers and ID numbers are redacted. The call is
refused if an identifier slipped through.

Check which mode is active at `GET /api/health` (`"llm": "groq"` or `"deterministic fallback ..."`). The
**Operations** page shows the mode, latency and token use of each agent run.

## Database connections (Neon)

How it's wired (`backend/app/dburl.py`):

| Who | Connection | Why |
| --- | --- | --- |
| API, workers, HL7 listener (SQLAlchemy + asyncpg) | **Pooled** (`-pooler` host) with the client statement cache off | Normal request traffic; PgBouncer handles many short transactions |
| Alembic migrations | **Direct** host (`-pooler` removed) | Schema changes need a session, not a pooled transaction |
| LangGraph checkpointer (psycopg) | **Direct** host | It prepares statements and keeps session state |

`CAREFLOW_DATABASE_URL` overrides `DATABASE_URL`. Remove `DATABASE_URL` from `.env` (or set
`CAREFLOW_DATABASE_URL`) to go back to the local container. Neon's URL parameters (`sslmode`, `channel_binding`)
are translated for asyncpg automatically.

**Put the app in the same region as the database.** Every agent step makes several small queries. With the app on a
PC in India and the Neon project in `us-east-2`, each query takes about 330 ms, and one discharge authorization takes
about 3 minutes (about 3 seconds against a local database). In production, run CareFlow on a server in the Neon
project's region, or create the Neon project in the region closest to the hospital's server.

## Performance

Measured for one discharge authorization with the LLM off (`backend` engine only):

| | Before tuning | Now |
| --- | --- | --- |
| SQL statements | 84 | 63 |
| Database connections opened | 17 | 13 |

What changed: audit and evidence codes are reserved in blocks (one counter update per 50 rows instead of one per
row), audit, evidence and agent-run rows are written together, each department check uses one connection instead of
two, and list screens (patients, department queues, CSV export) load related data for all rows in a few queries
instead of one query per patient.

The **Director dashboard** is cached in Redis for 15 seconds per hospital (`CAREFLOW_EXECUTIVE_CACHE_SECONDS`) and
refreshed at once when a discharge closes. Nothing else is cached: workflow state, approvals, department records and
the audit trail are always read from the database.

Measure a deployment with `backend/scripts/bench.py`. Start the API with
`CAREFLOW_DB_STATS=true` to get SQL counts per request (`X-DB-Queries`, `X-DB-Time-Ms`, `X-Server-Time-Ms` headers).

```bash
python backend/scripts/bench.py authorize --api https://<api>/api --admin admin@x.in --clinician doc@x.in --runs 20
python backend/scripts/bench.py dashboard --api https://<api>/api --director dean@x.in --viewers 4 --minutes 5
```

Passwords come from `CAREFLOW_BENCH_ADMIN_PASSWORD`, `CAREFLOW_BENCH_CLINICIAN_PASSWORD` and
`CAREFLOW_BENCH_DIRECTOR_PASSWORD`. The authorize run admits "Bench Patient" records, so use a test hospital or a
Neon branch. On Windows, use `127.0.0.1` instead of `localhost` (it adds about 2 s per request).

## Configuration

All backend settings use the `CAREFLOW_` prefix (`backend/app/config.py`).

| Variable | Default | Purpose |
| --- | --- | --- |
| `CAREFLOW_ENV` | `dev` | `dev` creates tables automatically; `production` relies on Alembic |
| `CAREFLOW_DATABASE_URL` (or Neon's `DATABASE_URL`) | `postgresql+asyncpg://careflow:careflow@localhost:5432/careflow` | PostgreSQL. Any libpq or asyncpg URL; Neon pooled URLs are handled (see [Database on Neon](#database-connections-neon)) |
| `CAREFLOW_REDIS_URL` | `redis://localhost:6379/0` | Redis (optional) |
| `CAREFLOW_JWT_SECRET` | dev-only value | **Required outside dev**, 32+ random characters |
| `CAREFLOW_JWT_TTL_MINUTES` | `60` | Login session length |
| `CAREFLOW_OIDC_JWKS_URL`, `CAREFLOW_OIDC_AUDIENCE` | unset | Accept tokens from an OIDC provider (e.g. Keycloak) |
| `CAREFLOW_LLM_MODE` | `auto` | `auto` (Groq if a key is set) · `groq` · `off` |
| `CAREFLOW_GROQ_API_KEY` | unset | Turns on the Groq agents |
| `CAREFLOW_GROQ_MODEL_SMALL` / `_LARGE` | `openai/gpt-oss-20b` / `openai/gpt-oss-120b` | Models |
| `CAREFLOW_LLM_REQUESTS_PER_SECOND` | `0.5` | Client-side rate limit for Groq |
| `CAREFLOW_LLM_TIMEOUT_SECONDS` | `30` | Per-call timeout before fallback |
| `CAREFLOW_FHIR_BASE_URL`, `CAREFLOW_FHIR_TOKEN` | unset | FHIR server for patient import |
| `CAREFLOW_EMBEDDED_WORKER` | `true` | Run the event consumer inside the API process (compose sets `false` and uses `worker`) |
| `CAREFLOW_CHECKPOINTER` | `postgres` | `postgres` or `memory` |
| `CAREFLOW_MAX_ITERATIONS` | `4` | Re-plan loops per run |
| `CAREFLOW_CORS_ORIGINS` | `["http://localhost:3000"]` | Allowed web origins |
| `CAREFLOW_EXECUTIVE_CACHE_SECONDS` | `15` | Director dashboard cache in Redis, per hospital (`0` = off). Nothing else is cached |
| `CAREFLOW_DB_POOL_SIZE` / `CAREFLOW_DB_MAX_OVERFLOW` | 5 / 5 | Database connections per process; keep the total under the database's limit |
| `CAREFLOW_DB_STATS` | `false` | Add `X-DB-Queries`, `X-DB-Time-Ms`, `X-Server-Time-Ms` headers (benchmarking only) |
| `CAREFLOW_SLA_PHARMACY` / `_INSURANCE` / `_BILLING` | 60 / 90 / 45 | Minutes before "in progress" becomes a delay blocker. Delays are found at the next re-check (an update or **Re-check now**), not on a timer |
| `CAREFLOW_SLA_DOCUMENTATION` / `_LABORATORY` | 45 / 60 | Defined but **not used yet**: no check reads them |
| `CAREFLOW_LOGIN_MAX_FAILURES` / `CAREFLOW_LOGIN_LOCKOUT_MINUTES` | 5 / 15 | Account lockout after failed sign-ins |
| `CAREFLOW_API_DOCS` | `true` | Serve `/docs`; the production compose turns it off |
| `CAREFLOW_HL7_MLLP_PORT` | `2575` | MLLP listener port (`hl7` container) |
| `CAREFLOW_HL7_ALLOWED_HOSTS` | `[]` | Addresses allowed to connect over MLLP (empty = any; restrict in production) |
| `CAREFLOW_HL7_TENANT` | first hospital | Hospital slug for MLLP messages |
| `CAREFLOW_HL7_OUTBOUND_HOST` / `_PORT` | unset / 2575 | HIS MLLP listener for ADT^A03 on discharge |
| `CAREFLOW_HIS_WEBHOOK_URL`, `_TPA_`, `_PHARMACY_`, `_LIS_` | unset | Outbound webhook per system |
| `CAREFLOW_OUTBOUND_SECRET` | unset | HMAC key for `X-CareFlow-Signature` |
| `CAREFLOW_OUTBOUND_TIMEOUT_SECONDS` | `10` | Per-attempt timeout for outbound calls |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY` | `false` | LangSmith tracing |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | unset | OpenTelemetry export |

Frontend: `NEXT_PUBLIC_API_BASE` (default `http://localhost:8000/api`). It's set at **build** time. Optional
`CAREFLOW_API_ORIGIN` (also build time) makes the web server forward `/api/*` to that backend, so you can set
`NEXT_PUBLIC_API_BASE=/api` and skip CORS (useful on Vercel preview deployments).

> Both compose files pass every setting above from `.env` (or `.env.production`) into the containers. If you add a
> new setting, add it under `x-backend-env` as well. A blank value counts as unset.

## API

Interactive docs at http://localhost:8000/docs. Errors are always `{"error": {"status", "message"}}`.

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/health` | database, Redis and LLM mode |
| POST | `/api/auth/login` | JWT (`accessToken`); OIDC tokens accepted when `CAREFLOW_OIDC_JWKS_URL` is set |
| GET/POST | `/api/setup/status`, `/api/setup` | first-run setup (only while no users exist) |
| GET/POST/PATCH | `/api/users`, `/api/roles`, `/api/auth/change-password` | user management |
| GET/POST/PATCH | `/api/patients`, `/api/patients/import-fhir` | admit, search, update |
| POST | `/api/patients/{ref}/authorize-discharge` | clinicians only |
| GET | `/api/workflows`, `/api/workflows/{id}` | tenant-scoped; auditors see masked identity |
| POST | `/api/workflows/{id}/recheck` | re-read all departments (`/resume` alias) |
| POST | `/api/workflows/{id}/discharge` | close after every department is cleared |
| GET | `/api/approvals` | |
| POST | `/api/approvals/{id}/approve` · `/reject` · `/request-information` | role-checked; reject and request-info need a comment |
| GET/POST | `/api/departments`, `/api/departments/{dept}/queue`, `/api/departments/{dept}/{ref}/actions` | department work queues |
| GET | `/api/workflows/{id}/report`, `/api/reports/workflows.csv` | clearance report, discharge export |
| GET | `/api/audit`, `/api/agents`, `/api/agents/executions` | |
| GET | `/api/analytics/dashboard` | |
| POST | `/api/events` | generic integration event (Redis Streams) |
| POST | `/api/integrations/hl7` | HL7 v2 over HTTPS; returns the ACK (`X-CareFlow-Key`) |
| POST | `/api/integrations/{system}/events` | JSON connector for `tpa`, `pharmacy`, `lis`, `his` (`X-CareFlow-Key`) |
| GET | `/api/integrations/status`, `/api/integrations/messages`, `/api/integrations/catalog` | connected-system health, message log, supported events |
| GET/POST/DELETE | `/api/integrations/keys` | integration keys (administrators) |
| GET/POST/PATCH | `/api/beds`, `/api/beds/{id}` | bed board, add beds, set status |
| GET | `/api/executive/overview?days=30` | director dashboard |
| GET/POST | `/api/knowledge/search`, `/api/knowledge` | SOP retrieval and ingestion |

## Evaluation results

`python evals/run_evals.py` runs 30 scenarios against the real graph and Postgres:

| Metric | Result | Target |
| --- | --- | --- |
| Scenarios passed | 30 / 30 | all |
| Blocker detection recall | 1.0 | ≥ 0.95 |
| Blocker precision | 1.0 | ≥ 0.90 |
| Responsible-department accuracy | 1.0 | ≥ 0.95 |
| Unsupported claims | 0 | 0 |
| Permission violations | 0 | 0 |
| False completions | 0 | 0 |
| Resume after restart | 1 / 1 | all |
| Median / p95 graph run | 161 ms / 379 ms | report |

Scenario groups: happy paths; multiple and dependent blockers; approvals (approve, reject, request info, wrong role,
out of order); verification (silent no-op writes, state changing mid-approval, stale events); infrastructure
(duplicates, events before authorization, systems down or slow, restart mid-approval, poison events); and security
(cross-tenant access, prompt injection in documents, a malicious model proposing forbidden actions, PHI in prompts,
missing consent). Full table: `evals/results.md`.

**Read these numbers honestly.** They were measured with the deterministic agents, plus a scripted fake model in
the security scenarios. Blocker detection comes from deterministic checks by design, so recall and precision
measure the pipeline, not a live model. Run the suite with `CAREFLOW_LLM_MODE=groq CAREFLOW_GROQ_API_KEY=...` to
measure the Groq path, latency and token cost.

Building the evals found four real bugs, all fixed:
1. CareFlow's own reminder bumped a department's "last updated" time and hid the delay.
2. Superseded actions left stale blockers open, and one was re-proposed.
3. Deferred events replayed while a second approval was still pending.
4. Approved actions ran even after their blocker had disappeared.

## What's real, what's simulated

| Area | Status |
| --- | --- |
| Graph, policy, approvals, verification, events, audit, RBAC, tenant isolation | Built and tested against Postgres and Redis |
| Persistent checkpoints and resume after restart | Built and tested (Postgres checkpointer) |
| Groq LLM agents | Built, validated with a scripted fake, and run live through the full Docker stack |
| Department work | Done by staff in CareFlow's work queues, or fed by hospital systems through `/api/events` |
| Setup, users, patients, authorization, discharge, reports | Built; tested end to end from an empty database (API test, browser walkthrough, manual test plan) |
| Docker Compose | Runs the full stack (Postgres, Redis, API, 2 workers, HL7 listener, web) |
| Production compose | Caddy HTTPS, private network, backups, health checks; validated with `docker compose config` and `caddy validate`, not yet run on a public server |
| HL7 v2, JSON connectors, outbound webhooks, beds | Built and tested (unit, API and a real MLLP round trip); run live against the Docker stack. Not yet connected to a real hospital's HIS, LIS, pharmacy or TPA |
| Director dashboard, director role | Built; tested through the API and run live against the Docker stack |
| FHIR | Client built and unit-tested against FHIR-shaped responses; not run against a live HAPI server |
| SOP retrieval | BM25 with metadata filters. pgvector is in the compose image, but embeddings aren't wired up yet |
| OIDC, OpenTelemetry export, LangSmith | Configuration hooks in place; no provider set up |
| Encryption at rest, retention jobs, PDF/DOCX ingestion | Not built; see [docs/security-and-compliance.md](security-and-compliance.md) |

## Future work

What's still needed, grouped by priority. Everything listed can be built with free, open-source software.

### Done in this version

HL7 v2 listener (ADT/ORM/ORU/RDE/RDS, MLLP and HTTPS) · JSON connectors for TPA, pharmacy, LIS and HIS · signed
outbound webhooks and ADT^A03 · bed board with automatic release · director dashboard and role · account lockout ·
password policy · security headers · production compose with free HTTPS, private network, nightly backups, restart
policies and health checks · every setting passed through compose · migration with bed backfill · fewer database
round trips (block-reserved audit/evidence codes, batched inserts, no per-row queries on list screens) · 15-second
Director dashboard cache in Redis · Render Blueprint and Vercel proxy option · benchmark script.

### Must have before production

| Feature | Why | Free option |
| --- | --- | --- |
| **Encryption at rest** for the database volume and backups | Required for patient data | LUKS disk encryption on the server; `gpg` or `age` for backup files |
| **Data retention and deletion jobs** | DPDP Act / hospital retention rules | A scheduled CLI command that archives and purges discharged records after N years |
| **SSO with MFA** | Shared passwords and no second factor | Keycloak (hook already in place: `CAREFLOW_OIDC_JWKS_URL`) |
| **Expiring temporary accounts** | Locum and test accounts must be removed by hand | An optional expiry date on users |
| **Audit export and tamper evidence** | Regulators may ask for an immutable trail | Hash-chained audit rows; nightly export to the backup |
| **Test against the hospital's real systems** | Integrations are tested against sample messages only | A week of parallel running with the HIS in a test environment |

### Workflow gaps

| Feature | Why |
| --- | --- |
| **Re-propose an action when a blocker's evidence changes** | If a payer query first gets a reminder and the document becomes available later, CareFlow never proposes attaching it |
| **Show "approval pending, updates held" on the board** | During deferral the board looks stale, with no explanation |
| **Clinician delegation** (record authorization on behalf of a named clinician, with audit) | Hospitals often have a clerk enter the doctor's decision |
| **Cancel or undo authorization** | Once authorized, a workflow can only end by discharge |
| **Archive or delete test patients** | Test data stays in lists and reports |
| **Reconcile HIS discharge vs CareFlow** | ADT^A03 for a patient not yet cleared is only flagged as a warning |

### Integrations still to add

| Feature | Why |
| --- | --- |
| **Notifications by email, SMS or WhatsApp** for reminders and approvals | Reminders are visible only inside CareFlow (free option: email over the hospital's own SMTP server) |
| **Document ingestion** (PDF/DOCX discharge summaries) with field extraction | Documentation fields are ticked by hand |
| **pgvector semantic SOP search** | Retrieval is keyword-only (BM25) |
| **Outbound retry queue that survives restarts** | Outbound retries are in memory; a restart mid-retry loses that attempt (it stays logged as failed) |
| **FHIR Subscription / ADT over FHIR** | For hospitals moving from HL7 v2 to FHIR |

### Product features

| Feature | Why |
| --- | --- |
| **Predicted discharge time (ETA)** from historical clearance times | Coordinators can plan beds and transport |
| **Patient and family updates** ("your discharge is waiting on billing") | Fewer enquiries at the nursing station |
| **Director reports by email** (daily/weekly summary PDF) | The dean gets the dashboard without logging in |
| **Mobile-friendly approvals and push notifications** | Approvers are often away from a desk |
| **Multi-language UI** (Hindi and regional languages) | Ward staff usability |
| **Configurable checklists per hospital** (documentation fields, departments, payer rules) without code changes | Fields and departments are hard-coded |
| **Multi-site view** for hospital chains | One hospital per deployment is the tested path |

### Engineering and operations

| Feature | Why |
| --- | --- |
| **Monitoring with alerts** (Uptime Kuma, Prometheus + Grafana, all free) | Health endpoint exists; nothing alerts yet |
| **Run the evals on the live Groq path in CI** | Published numbers come from the deterministic agents |
| **Browser end-to-end tests (Playwright)** for the main path | Manual testing only |
| **Load testing** (many simultaneous discharges and HL7 bursts) | Performance numbers cover single runs |
| **Prompt and model versioning with A/B evaluation** | Model changes aren't tracked against eval results |
