# Documentation audit

What exists in the CareFlow code on 2026-11-10, so the documentation describes only what is there.
This file isn't in the site navigation; it's a working list for the documentation steps.

**Status labels**

- **Built and tested**: in the code and exercised by automated tests (`backend/tests/`, 65 tests) or the eval suite
  (`evals/`, 30 scenarios).
- **Built, not tested**: in the code, but no automated test exercises it. *Checked by hand* means it was run manually
  against the local stack, but there's still no automated test.
- **Not built yet**: asked for in the documentation plan, but not in the code. Documentation must label it that way.

How this was checked: by reading `backend/app`, `web/src`, `evals`, the compose files, `render.yaml`, `scripts`,
`backend/scripts` and `backend/alembic`, and searching the tests for each feature. The generated reference pages
(`scripts/gen_docs.py`) list the exact settings, roles, actions, blocker kinds, events, endpoints, schema and CLI.

## Features

| Feature | Where | Status |
| --- | --- | --- |
| First-run setup (hospital + administrator) | `api/hospital.py: setup()` | Built and tested (`test_real_use`) |
| User management, roles, password change | `api/hospital.py` | Built and tested |
| Password policy (12+ chars, 2 classes, no email name) | `security.py: password_problem()` | Built and tested |
| Account lockout after failed sign-ins, admin unlock | `api/routes.py: login()`, `api/hospital.py` | Built and tested |
| Login rate limit (per IP, Redis) | `api/routes.py: rate_limit()` | Built and tested |
| Local JWT sign-in, token lifetime | `security.py` | Built and tested |
| OIDC token verification (e.g. Keycloak) | `security.py` (`CAREFLOW_OIDC_JWKS_URL`) | Built, not tested |
| Patient admission by hand, search, edit | `api/hospital.py` | Built and tested |
| Patient import from FHIR | `fhir.py`, `api/hospital.py: import_fhir()` | Built and tested against FHIR-shaped responses; not run against a live FHIR server |
| Clinician-only discharge authorization | `api/hospital.py: authorize_discharge()` | Built and tested |
| Orchestration graph (plan, parallel checks, analyze, resolve, approval, execute, verify, report) | `agents/graph.py` | Built and tested (evals) |
| Four LLM agents with deterministic fallbacks (Groq) | `agents/llm_agents.py`, `llm.py` | Built and tested with a scripted fake model; run live on Groq by hand |
| Department checks with evidence | `agents/checks.py` | Built and tested |
| Policy-checked action catalog, forbidden actions | `policy.py` | Built and tested |
| Approvals: approve, reject, request information, any order, survive restarts | `engine.py` | Built and tested (evals) |
| Deferral of events while an approval is pending | `engine.py` | Built and tested (evals) |
| Verification after every write ("action not applied") | `agents/graph.py: verify()` | Built and tested (evals) |
| Department work queues and staff actions | `api/hospital.py`, `departments.py` | Built and tested |
| Read-only queues for the director | `api/hospital.py: _dept_guard()` | Built and tested |
| Discharge board, workflow detail | `api/routes.py` | Built and tested |
| Manual re-check of a workflow (`/recheck`, `/resume`) | `api/routes.py` | Built, not tested (checked by hand) |
| Close discharge (releases bed, outbound notifications) | `api/hospital.py: close_discharge()` | Built and tested |
| Clearance report, CSV export | `api/hospital.py` | Built and tested |
| Operations analytics dashboard | `api/routes.py: dashboard()` | Built and tested |
| Audit trail API | `api/routes.py: audit()` | Built, not tested (audit rows are asserted through other tests; the endpoint itself isn't called) |
| Agent run log (`/api/agents`, `/api/agents/executions`) | `api/routes.py` | Built and tested (`/api/agents`); `/executions` not tested |
| Director dashboard and its 15-second Redis cache | `api/executive.py`, `cache.py` | Built and tested |
| Bed board (occupy on admit, cleaning on discharge/transfer, mark clean, add beds) | `integrations/service.py`, `api/integrations.py` | Built and tested |
| SOP knowledge retrieval (BM25, versions, expiry, citations) | `rag.py`, `knowledge/` | Built and tested |
| PHI redaction before LLM calls | `redaction.py` | Built and tested |
| Prompt-injection detection in documents | `redaction.py`, `agents/graph.py` | Built and tested (evals) |
| Tenant isolation | every query filters by tenant | Built and tested (evals) |
| Auditor masking of names and MRNs | `api/routes.py: _mask()`, `api/hospital.py: _masked()` | Built and tested |
| Security headers, production start-up checks, generic 500 handler (with CORS headers) | `main.py` | Built, partly tested |
| Database round-trip reductions (block-reserved codes, batched list queries) | `repo.py`, `api/*.py` | Built and tested (`test_performance`) |
| Self-healing connection pools (idle ping, checkpointer pool) | `db.py`, `checkpoint.py` | Built and tested (checkpointer test runs on Linux only) |
| Per-request SQL counter (`CAREFLOW_DB_STATS`) | `dbstats.py` | Built and tested |
| Benchmark script | `backend/scripts/bench.py` | Built, not tested (run by hand) |
| OpenTelemetry export, LangSmith tracing | `telemetry.py` | Built, not tested (configuration hooks only) |

## Roles

From `security.py: ROLE_PERMISSIONS` (10 roles). All built and tested through the role tests and evals.

Administrator · Hospital director · Discharge coordinator · Clinician · Ward clerk · Pharmacy · Laboratory ·
Insurance desk lead · Billing · Auditor.

**Not built yet: a Housekeeping role.** Beds are marked clean by any role with `bed:manage` (Administrator,
Discharge coordinator, Ward clerk).

## Endpoint groups

51 endpoints; the full list with permissions is generated in `reference/generated/api-endpoints.md`.

| Group | Endpoints | Status |
| --- | --- | --- |
| Auth (`/api/auth/login`, `/me`, `/logout`, `/change-password`) | 4 | login and change-password tested; `/me` and `/logout` not tested |
| Setup (`/api/setup`, `/status`) | 2 | Built and tested |
| Users and roles (`/api/users`, `/api/roles`) | 4 | Built and tested |
| Patients (`/api/patients`, import, update, authorize) | 5 | Built and tested |
| Workflows (list, detail, recheck, resume, discharge, report) | 6 | list, detail, discharge and report tested; recheck and resume not tested |
| Approvals (list, approve, reject, request information) | 4 | Built and tested (evals) |
| Departments (list, queue, actions) | 3 | Built and tested |
| Beds (list, add, set status) | 3 | Built and tested |
| Reports (`/api/reports/workflows.csv`) | 1 | Built and tested |
| Analytics, audit, agents | 4 | dashboard and agents tested; audit and executions not tested |
| Director dashboard (`/api/executive/overview`, `/cache-stats`) | 2 | Built and tested |
| Events (`/api/events`) | 1 | Built and tested at the engine level; the HTTP endpoint isn't called by a test |
| Integrations (HL7, connectors, catalog, status, messages, keys) | 8 | Built and tested |
| Knowledge (`/api/knowledge/search`, `/api/knowledge`) | 2 | Built and tested |
| Health (`/api/health`) | 1 | Built and tested |

## Integrations

| Integration | Status |
| --- | --- |
| HL7 v2 inbound over MLLP (port 2575) and HTTPS: ADT A01/A02/A03/A04/A08, ORM^O01, OML^O21, ORU^R01, RDE^O11, RDS^O13; ACK AA/AE/AR; duplicate detection by control ID | Built and tested (including a real MLLP round trip) |
| JSON connectors: TPA, pharmacy, LIS, HIS (`/api/integrations/{system}/events`), per-system integration keys | Built and tested |
| Outbound: HMAC-SHA256 signed webhooks per system, HL7 ADT^A03 to the HIS on discharge, 3 in-memory retries, message log | Built and tested |
| FHIR R4 patient import | Built and tested against FHIR-shaped responses only |
| **HL7 profiles in YAML, ADT^A16** | **Not built yet** |
| **TLS for MLLP (e.g. stunnel)** | **Not built yet** (no TLS on port 2575; use HTTPS or a separate tunnel) |
| **Email inbox connector for TPAs, TPA templates, extraction/matching, review list, document pack** | **Not built yet** |
| **NHCX (National Health Claims Exchange)** | **Not built yet** |
| **CSV drop folders and vendor mapping YAML for pharmacy/LIS; "needs matching" list** | **Not built yet** |
| **HMAC signing of inbound connector calls** | **Not built yet** (inbound uses integration keys; HMAC is outbound only) |
| **Persistent outbound queue / outbound dead-letter queue** | **Not built yet** (retries are in memory) |
| **ABDM / ABHA integration** | **Not built yet** (only ABHA-number redaction exists) |
| **Synthea test-data setup for FHIR** | **Not built yet** |

## CLI commands

From `app/cli.py: build_parser()`: `create-admin`, `reset-password`, `load-examples`, `sync-beds`.
All four are **built, not tested** by automated tests; `reset-password` was used by hand during setup.

**Not built yet: `remove-temporary-access`** (with `--dry-run`). Temporary access is removed by hand on the Users
and Integrations pages.

## Environment variables

44 settings in `app/config.py: Settings`; all documented in `reference/generated/environment-variables.md`.

**Found while auditing:** `CAREFLOW_SLA_DOCUMENTATION` and `CAREFLOW_SLA_LABORATORY` are defined but **no code
reads them**, so changing them has no effect. Earlier documentation (`docs/reference.md`, the README configuration
table) described them as working time limits. Their descriptions in `config.py` now say "not used yet".

## Background jobs and processes

| Job | Where | Status |
| --- | --- | --- |
| Event consumer (Redis Streams consumer group `careflow-workers`, retries, dead-letter stream `careflow:events:dlq`) | `events.py`, `worker.py` (2 `worker` containers) or embedded in the API | Built and tested |
| HL7 MLLP listener | `integrations/mllp.py` (`hl7` container) | Built and tested |
| Outbound webhook / HL7 delivery (fire-and-forget tasks) | `integrations/outbound.py` | Built and tested |
| Nightly database backup | `deploy/backup.sh` (`backup` container in `docker-compose.prod.yml`) | Built, not tested (validated with `docker compose config` only) |
| **Crash recovery of claimed events (XAUTOCLAIM / pending reclaim)** | — | **Not built yet** |
| **Data retention / purge job** | — | **Not built yet** |
| **Scheduled SLA re-checks** | — | **Not built yet**: time-based delays are found when a workflow is next checked (an event or a manual re-check), not on a timer |

## Security and operations features asked for in the plan

| Item | Status |
| --- | --- |
| MFA / two-factor sign-in | **Not built yet** in CareFlow; possible only through an OIDC provider that enforces it |
| Refresh tokens | **Not built yet** |
| Separate security log (sign-ins, lockouts, key changes) | **Not built yet** |
| Encryption of stored raw messages / encryption at rest | **Not built yet** |
| Metrics endpoint for queue length, lag, dead letters | **Not built yet** |
| Ollama (local LLM) | **Not built yet**; Groq is the only provider |
| Playwright screenshots | **Not built yet** (to be added in documentation step 4) |
| CLAUDE.md / contributing guide | **Not present** in the repository |

## Problems found and fixed during this audit

1. The blocker kind `payer_denied` had no label in the web app, so staff saw the raw text "payer_denied". Added
   "Payer denied" to `web/src/lib/format.ts: KIND_LABEL`.
2. 29 settings in `config.py` had no description. Each now has a one-line comment, which the generated
   environment-variable page uses.
3. The CLI parser was built inside `main()`. It moved to `build_parser()` (same behaviour, now with help text) so
   the CLI reference can be generated.
