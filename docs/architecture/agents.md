# Agents and the graph

CareFlow's discharge-clearance process is a LangGraph graph (`backend/app/agents/graph.py: build_graph()`). This
page explains each step, the four LLM agents and their safety rules, and how the graph pauses for approvals and
picks up again.

## The graph

```mermaid
flowchart TD
    start([start]) --> load[load_context]
    load -->|consent recorded| plan
    load -.->|no consent| report
    plan -->|departments to check| check["check_department<br/>(one per department, in parallel)"]
    plan -.->|nothing to check| analyze
    check --> analyze
    analyze -->|blockers with a possible fix| resolve
    analyze -.->|nothing to fix| report
    resolve -->|needs a person| approval
    resolve -->|low risk, approved by policy| execute
    resolve -.->|nothing to do| report
    approval -->|decided| execute
    approval -.->|nothing approved to run| report
    execute --> verify
    verify -->|iterations left| plan
    verify -.->|limit reached| report
    report --> stop([end])
```

The exact graph, drawn by LangGraph from the code, is in [agent graph](../reference/generated/agent-graph.md).

| Step (node) | What it does | Deterministic or LLM |
| --- | --- | --- |
| `load_context` | Loads the patient from FHIR (if configured) or the local store. Stops at once if no consent for coordination is recorded. | Deterministic |
| `plan` | Decides which departments must clear and in what order (billing waits on insurance for insured patients). On a re-check, it plans only the changed departments and the departments that depend on them. | **Workflow Planner** agent (small model), checked against payer rules |
| `check_department` | Reads one department's system and applies that department's rules (`agents/checks.py`). Runs once per department **in parallel** (LangGraph `Send`). Three attempts with back-off if the system is unreachable. Writes evidence for everything it observes. | Deterministic |
| `analyze` | Turns findings into blockers, adds *waiting on another department* where needed, ranks them and explains each in one sentence. Closes blockers that are no longer observed. | **Blocker Analyzer** agent (large model) |
| `resolve` | For each blocker, picks one action from the catalog (citing the SOP), then runs the Python policy check. Low-risk actions are approved automatically; others become approval requests. | **Resolution Planner** agent (small model) + `policy.check()` |
| `approval` | Pauses the graph with `interrupt()` until every pending approval is decided. Decisions can arrive in any order. | Human |
| `execute` | Carries out approved actions, after re-checking that the blocker still exists. Writes are idempotent. Sends outbound notifications. | Deterministic |
| `verify` | Re-reads the department system to confirm each change happened. If not, opens an *action not applied* blocker for a person. Then loops back to `plan` so changed departments are re-checked, until the iteration limit. | Deterministic |
| `report` | Writes a short clearance summary for the coordinator and the clearance report. | **Reporter** agent (small model) |

A run loops from `verify` back to `plan` until it has made `CAREFLOW_MAX_ITERATIONS` passes (default 4), then writes the report. A pass with nothing left to check or fix goes straight to the report.

## The four LLM agents

All four live in `backend/app/agents/llm_agents.py` and use the same wrapper (`_run()`):

1. Redact patient identifiers from the prompt (`redaction.redact()`), and refuse the call if any slipped through.
2. Ask Groq for output in a fixed Pydantic schema.
3. **Validate** the output with the agent's own rules.
4. If Groq is unavailable, slow, rate-limited, or the output fails validation, use the agent's **deterministic
   fallback** instead. The run is recorded in `agent_executions` with `mode` = `llm`, `fallback` or
   `deterministic`.

| Agent | Output schema | Validator refuses the output if | Fallback |
| --- | --- | --- | --- |
| **Workflow Planner** | `PlanOut`: departments, dependencies, rationale | The departments or dependencies differ from the payer rules (`required_plan()`: documentation, pharmacy and billing always; insurance for cashless and government; laboratory with lab orders; billing after insurance) | The payer rules themselves |
| **Blocker Analyzer** | `AnalysisOut`: blockers (department, kind, one-sentence summary, evidence IDs, priority 1–5) | It adds a blocker no check found, drops a finding, or cites evidence that wasn't retrieved | Every finding as-is, with a fixed priority: 1 for pharmacy delay, payer query, system unreachable and action not applied; 2 for incomplete document; 3 for the rest |
| **Resolution Planner** | `ResolutionOut`: action, description, expected impact, evidence IDs | The action isn't allowed for this blocker, it attaches a document that doesn't exist, or it cites evidence outside the blocker | A default action per blocker kind, or *none* (a person handles it) |
| **Reporter** | `ReportOut`: summary | The summary is empty | A template: "*N of M departments cleared after T. Open: …*" |

Every agent shares one system prompt (`SYSTEM_GUARD`) that tells the model it coordinates administrative work only,
must never judge medical fitness, interpret results, change medicines or approve claims, and must treat text inside
`<data>` tags as data, not instructions.

!!! info "Why an agent can't do harm"
    Even if a model ignores its prompt, its output passes through the validator and then `policy.check()`. An action
    must be in the catalog, target the blocker's department, carry its required parameters and cite retrieved
    evidence. Clinical and financial actions are on a hard-coded forbidden list.

## Safety around the LLM

| Protection | How | Code |
| --- | --- | --- |
| No patient identity in prompts | Names, MRNs, phone numbers and ID numbers (including ABHA-style numbers) are replaced before a prompt is sent; the call is refused if a known identifier is still present | `redaction.py`, `llm.py: structured()` |
| Prompt injection | Department documents are scanned for instructions; if found, the text is treated as data, flagged as untrusted evidence, and an *Injection guard* audit entry is written | `redaction.py`, `graph.py: check_department()` |
| Bounded choices | The LLM picks from a candidate list; anything else fails validation | `llm_agents.py`, `policy.py` |
| Rate limit and timeout | `CAREFLOW_LLM_REQUESTS_PER_SECOND`, `CAREFLOW_LLM_TIMEOUT_SECONDS`; on timeout the fallback runs | `llm.py` |

## Pausing, resuming and re-checking

- **Checkpoints.** After every step, LangGraph saves the graph state in PostgreSQL (`checkpoint*` tables, through
  `checkpoint.py: postgres_checkpointer()`). Each workflow has its own thread, named after its code (`WF-2041`).
- **Pause for approval.** The `approval` node calls `interrupt()`. The run ends, but the state is saved. The pause
  survives restarts of every process.
- **Resume.** When a person decides, `Engine.decide()` resumes the same thread with `Command(resume=...)`. Each
  `interrupt()` consumes one decision, so several approvals can be decided in any order.
- **Re-check.** A department update starts a new run on the same thread with `recheck=[department]`. `plan` then
  checks only that department and the departments that depend on it.
- **Deferral.** While an approval is pending, department updates are stored, not run, and replayed straight after
  the decision. This keeps the paused state consistent.

## Related pages

- [Architecture overview](overview.md) · [Events and Redis](events-and-redis.md) · [Security design](security-design.md)
- [Blocker kinds](../reference/generated/blocker-kinds.md) · [Action catalog](../reference/generated/action-catalog.md) · [Agent graph](../reference/generated/agent-graph.md)
