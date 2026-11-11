# Security design

This page lists the threats CareFlow is designed against, how each one is handled, and where in the code. It is
for a security reviewer or hospital IT lead. Gaps are listed honestly at the end. For roles, sign-in and patient-data
handling in practice, see the [Security](../security/roles-and-permissions.md) section.

## Principles

- **Least privilege.** Ten roles, each with only the permissions its job needs. Every endpoint checks a permission
  on the server ([permission matrix](../reference/generated/roles-and-permissions.md)).
- **No clinical or financial automation.** Discharge authorization is clinician-only. Clinical and financial actions
  are on a hard-coded forbidden list that no agent, document or approver can unlock.
- **Proposals, not actions.** LLM agents only propose. Python validators and a policy check decide.
- **Everything is evidenced and audited.** Each blocker and action cites what was observed. Every change is written
  to the audit trail.
- **One hospital never sees another.** Every query filters by the signed-in user's hospital.

## Threats and mitigations

| Threat | Mitigation | Code |
| --- | --- | --- |
| **Password guessing** | Password policy (12+ characters, two character types, not containing the email name); bcrypt hashes; 5 wrong attempts lock the account for 15 minutes; sign-ins limited to 10 per minute per address | `security.py: password_problem()`, `api/routes.py: login()`, `rate_limit()` |
| **Stolen session token** | Token kept in sessionStorage (ends with the tab); limited lifetime (`CAREFLOW_JWT_TTL_MINUTES`); HTTPS with HSTS in production; every request reloads the user, so **deactivating an account or changing a role takes effect at once** | `security.py: current_principal()`, `main.py`, `web/src/lib/session.ts` |
| **Insider misuse** | Role permissions; risky actions need an approver with the right role; every decision is audited with the person's name; the Auditor role reviews without seeing identities | `security.py`, `policy.py`, `engine.py: decide()`, `repo.py: audit()` |
| **Cross-hospital access** | Every query filters by tenant; a workflow from another hospital returns the same 404 as one that doesn't exist; integration keys belong to one hospital | `api/*.py` (`tenant_id ==` filters), `api/routes.py: _wf()` |
| **Prompt injection** (instructions hidden in a document) | Document text is scanned; if it contains instructions it is treated as data, marked untrusted, audited by the *Injection guard*, and can't trigger any action; prompts wrap system data in `<data>` tags the model is told not to obey | `redaction.py: looks_like_injection()`, `agents/graph.py: check_department()`, `llm_agents.py: SYSTEM_GUARD` |
| **A misbehaving or manipulated model** | Output must match a schema and pass validators; actions must be in the catalog, target the right department, carry parameters and cite retrieved evidence; forbidden actions are refused | `agents/llm_agents.py`, `policy.py: check()` |
| **Patient data sent to the LLM** | Names, MRNs, phone and ID numbers (including ABHA-style numbers) are removed before any prompt leaves the server; the call is refused if a known identifier remains; the LLM is optional | `redaction.py`, `llm.py: structured()` |
| **Replayed or duplicated events** | Each event is stored once per hospital and event ID; HL7 messages are de-duplicated by control ID (MSH-10) | `engine.py: handle_event()`, `integrations/service.py: is_duplicate()` |
| **A forged hospital-system message** | Inbound HL7-over-HTTPS and JSON connectors need a per-system integration key (`X-CareFlow-Key`), stored only as a SHA-256 hash, scoped to one system, revocable at once; administrators may also send with their session | `api/integrations.py: _caller()` |
| **Forged CareFlow notifications** (someone impersonating CareFlow to a hospital system) | Outbound webhooks carry `X-CareFlow-Signature: sha256=<HMAC of the body>` when `CAREFLOW_OUTBOUND_SECRET` is set | `integrations/outbound.py: sign()` |
| **HL7 on an open network** | The production compose binds port 2575 to `127.0.0.1` unless `CAREFLOW_HL7_BIND` is changed; `CAREFLOW_HL7_ALLOWED_HOSTS` limits which addresses may connect; firewall guidance in the deployment guide | `docker-compose.prod.yml`, `integrations/mllp.py` |
| **Flooding** | Messages over 1 MB are refused; HL7 and connectors limited to 600 per minute per hospital; approval decisions and re-checks to 30 per minute per user | `integrations/mllp.py`, `api/integrations.py`, `api/routes.py: rate_limit()` |
| **Misconfiguration** | Outside `dev`, the API refuses to start with a weak JWT secret or `*` as a CORS origin; API docs (`/docs`) are off in production; database and Redis ports aren't published; Redis needs a password | `main.py: lifespan()`, `docker-compose.prod.yml` |
| **Browser-side attacks** | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, a restrictive `Permissions-Policy`, `Cache-Control: no-store` on the API | `main.py: security_headers()` |
| **Leaking internals in errors** | Unexpected errors return a generic message; details go only to the server log | `main.py: unhandled()` |

## Known gaps

These are **not built yet**. A hospital should decide how to cover them before real patient data is used.

| Gap | Effect | Suggested cover |
| --- | --- | --- |
| No MFA in CareFlow itself | A stolen password is enough to sign in | Sign in through an OIDC provider (e.g. Keycloak) that enforces MFA (`CAREFLOW_OIDC_JWKS_URL`) |
| Sign-out doesn't revoke the token | A copied token stays valid until it expires | Keep `CAREFLOW_JWT_TTL_MINUTES` short; deactivate the account to cut access at once |
| No encryption at rest; raw integration messages contain names | A copy of the disk or a backup exposes patient data | Disk encryption (e.g. LUKS) and encrypted backups at the server level |
| No TLS on MLLP | HL7 over port 2575 is readable on the network | Keep it on a private network, send HL7 over HTTPS instead, or add a tunnel such as stunnel |
| Inbound connectors use a shared key, not a signature | Anyone holding the key can send events | Treat keys as secrets, one per system; revoke and re-issue if exposed |
| No separate security log | Sign-ins and lockouts are on the user record, not in the audit trail | Collect the API logs centrally |
| No retention or purge | Data is kept indefinitely | Agree a retention period; add a purge job (future work) |

CareFlow is not a certified medical device. This page is a design description, not a compliance certification;
see [Compliance notes](../security/compliance.md).

## Related pages

- [Architecture overview](overview.md) · [Agents and the graph](agents.md) · [Data model](data-model.md)
- [Roles and permissions](../reference/generated/roles-and-permissions.md) · [Security and compliance (current guide)](../security-and-compliance.md)
