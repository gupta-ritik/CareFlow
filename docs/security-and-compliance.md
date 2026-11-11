# Security and compliance notes

CareFlow is a portfolio project running on synthetic data. Nothing here is a certification or a legal opinion. A real deployment needs a formal privacy and security assessment with the hospital's DPO and legal counsel.

## What the code enforces

| Control | Where | How it's tested |
| --- | --- | --- |
| No clinical decisions | `policy.FORBIDDEN`, action catalog | Clinical authorization is only an input event; forbidden actions are absent from the catalog and refused by `policy.check` (`test_units.py`, eval 28) |
| LLMs propose, Python decides | `policy.check`, agent validators in `agents/llm_agents.py` | Hallucinated blockers and off-catalog actions fall back to deterministic output (eval 28, `test_llm_hallucinations...`) |
| Evidence for every claim | Blocker and action schemas carry evidence IDs; validators reject IDs that weren't retrieved | `unsupported_claims` metric across all 30 evals |
| Verification before completion | `verify` node re-reads the department system | Eval 15 (write succeeds, nothing changes) |
| Minimum necessary data to LLMs | `redaction.py`; `llm.structured` refuses prompts containing identifiers | Eval 29 scans every prompt for name, MRN, phone |
| Prompt-injection handling | Document and SOP text wrapped as data; injection patterns flagged; SOP chunks with instructions dropped | Eval 27, `test_injected_sop_is_dropped` |
| RBAC | `security.ROLE_PERMISSIONS`; approver role per catalog action | Evals 13, 26; API tests |
| Tenant isolation | Every query filters on `tenant_id`; other tenants get 404, not 403 | Eval 25, `test_tenant_isolation` |
| Consent check | `load_context` stops before any department read | Eval 30 |
| Idempotency | Unique `(tenant_id, event_id)`; execution idempotency keys | Eval 19, `test_stream_processing_idempotent_and_dlq` |
| Audit trail | `audit_logs` with actor, event, evidence; free text redacted | Audit page; every eval checks audit contents |
| Rate limiting | Redis fixed window per user or IP | `test_login_rate_limited` |
| Auditor view masks identity | `_mask` in `api/routes.py` | `test_auditor_sees_masked_identity` |
| No identity in checkpoints | Graph state holds codes and statuses only; DB handles travel in runtime context | Code review: `CareFlowState` fields |

## Added for production use

- **Login hardening:** passwords need 12+ characters mixing letter case, digits or symbols, and can't contain the
  email name; 5 failed sign-ins lock the account for 15 minutes (configurable); administrators can unlock.
- **Security headers** on every API response (nosniff, frame denial, no referrer, no caching of API data; HSTS
  outside dev). The API refuses to start outside dev without a 32+ character JWT secret or with `*` CORS.
- **Production compose:** HTTPS via Caddy and Let's Encrypt, Postgres and Redis on a private network (Redis with a
  password), `/docs` off, log rotation, nightly backups. See [deployment.md](deployment.md).
- **Integrations:** per-system API keys stored as SHA-256 hashes and revocable; HL7 MLLP can be restricted to known
  hosts; outbound webhooks are HMAC-signed; every inbound and outbound message is logged with its raw content
  (this log contains patient identifiers and is visible to administrators and the director only).
- **Hospital director role:** sees real patient identity across departments, so give it only to the dean or
  equivalent.

## What's left for a real deployment

- **Encryption:** TLS is handled by Caddy in `docker-compose.prod.yml`. Encryption at rest is not: use disk-level encryption (LUKS) on the server and encrypt backup files before copying them off the server.
- **Secrets:** `CAREFLOW_JWT_SECRET`, database and Groq keys from a secret manager, not `.env`.
- **OIDC:** set `CAREFLOW_OIDC_JWKS_URL` and `CAREFLOW_OIDC_AUDIENCE` to verify Keycloak (or another provider's) tokens. Role mapping from the provider's claims still needs writing.
- **Retention:** define how long audit logs, evidence and checkpoints are kept, and add a purge job.
- **LLM provider review:** even redacted, administrative text goes to Groq. A hospital would need a data processing agreement, or a self-hosted model.
- **File uploads:** SOP ingestion accepts markdown only, size-capped; PDF and DOCX extraction and malware scanning are not built.

## India: DPDP Act 2023 and ABDM

Points a formal assessment would need to cover, with how the design relates:

- **Purpose limitation and minimum data (DPDP):** CareFlow reads administrative status only, not clinical content. The consent flag gates all reads. Whether a hospital's existing treatment consent covers this coordination purpose is a legal question.
- **Data principal rights (DPDP):** access, correction and erasure requests would need endpoints and a process; erasure must be balanced against medical-record retention rules.
- **Processor obligations (DPDP):** an external LLM provider is a processor; contracts and cross-border transfer rules apply.
- **Breach notification (DPDP):** needs an incident process; the audit trail helps reconstruct events.
- **ABDM:** CareFlow does not create or link ABHA numbers or exchange records through ABDM gateways. If it consumed ABDM health records, it would need ABDM's consent manager flow (HIE-CM) and certification for that integration. ABHA-format numbers are redacted from LLM prompts.
