"""The "analytics" schema Ask CareFlow reads, and the read-only role it reads it as.

The views hold no names, MRNs, beds or free text: only ids, tenant, times, ward, payer type, TPA (for cashless
patients), department, blocker kind and approver role. The role `careflow_analytics` may SELECT these views and
nothing else (no INSERT/UPDATE/DELETE, no access to the base tables). Views run with their owner's rights, so the role
can read a view without being able to read `patients` itself.

The application user switches to the role per query (`SET LOCAL ROLE careflow_analytics`), so no second password is
needed. Applied by the Alembic migration in production and by `ensure_analytics` on dev start-up and in tests.
"""
import logging

from sqlalchemy import text

log = logging.getLogger("careflow.ask")

ROLE = "careflow_analytics"

# A timestamp copied from a department record's JSON only when it looks like one (real records may lack it).
_TS = "CASE WHEN r.data->>'{k}' ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}' THEN (r.data->>'{k}')::timestamptz END"

VIEWS = [
    "CREATE SCHEMA IF NOT EXISTS analytics",
    # One row per discharge workflow.
    """CREATE OR REPLACE VIEW analytics.discharges AS
    SELECT w.id AS workflow_id, w.tenant_id, w.authorized_at, w.cleared_at, w.discharged_at,
           p.ward, p.payer_type, CASE WHEN p.payer_type = 'cashless' THEN p.payer END AS tpa, p.has_lab_orders,
           EXTRACT(EPOCH FROM (w.discharged_at - w.authorized_at)) / 60.0 AS discharge_minutes,
           EXTRACT(EPOCH FROM (w.cleared_at - w.authorized_at)) / 60.0 AS clearance_minutes
    FROM workflows w JOIN patients p ON p.id = w.patient_id
    WHERE w.deleted_at IS NULL""",
    # One row per department task. minutes = working time: from when the department could start (authorization, or the
    # moment the department it waits on cleared, e.g. billing after insurance) to its clearance.
    """CREATE OR REPLACE VIEW analytics.department_tasks AS
    SELECT t.workflow_id, w.tenant_id, t.department, w.authorized_at,
           COALESCE(dep.cleared_at, w.authorized_at) AS started_at, t.cleared_at,
           EXTRACT(EPOCH FROM (t.cleared_at - COALESCE(dep.cleared_at, w.authorized_at))) / 60.0 AS minutes,
           p.ward, p.payer_type, CASE WHEN p.payer_type = 'cashless' THEN p.payer END AS tpa
    FROM workflow_tasks t
    JOIN workflows w ON w.id = t.workflow_id
    JOIN patients p ON p.id = w.patient_id
    LEFT JOIN LATERAL (SELECT max(x.cleared_at) AS cleared_at FROM task_dependencies d
                       JOIN workflow_tasks x ON x.workflow_id = d.workflow_id AND x.department = d.depends_on
                       WHERE d.workflow_id = t.workflow_id AND d.department = t.department) dep ON true
    WHERE w.deleted_at IS NULL""",
    # One row per insurance claim (final authorization). Pre-authorization is not recorded by CareFlow.
    f"""CREATE OR REPLACE VIEW analytics.claims AS
    SELECT r.tenant_id, w.id AS workflow_id, p.ward, p.payer_type,
           CASE WHEN p.payer_type = 'cashless' THEN p.payer END AS tpa, p.payer AS payer,
           {_TS.format(k='submitted_at')} AS submitted_at,
           COALESCE({_TS.format(k='approved_at')}, it.cleared_at) AS approved_at,
           EXTRACT(EPOCH FROM (COALESCE({_TS.format(k='approved_at')}, it.cleared_at) - {_TS.format(k='submitted_at')})) / 60.0 AS minutes,
           (jsonb_typeof(r.data->'queries') = 'array' AND jsonb_array_length(r.data->'queries') > 0)
             OR COALESCE(r.data->>'requested_document', '') <> '' AS queried,
           COALESCE(CASE WHEN jsonb_typeof(r.data->'queries') = 'array' THEN r.data->'queries'->>0 END,
                    NULLIF(r.data->>'requested_document', '')) AS query_document
    FROM sim_department_records r
    JOIN patients p ON p.tenant_id = r.tenant_id AND p.ref = r.patient_ref
    JOIN LATERAL (SELECT w2.id, w2.authorized_at FROM workflows w2 WHERE w2.patient_id = p.id AND w2.deleted_at IS NULL
                  ORDER BY w2.authorized_at DESC LIMIT 1) w ON true
    LEFT JOIN workflow_tasks it ON it.workflow_id = w.id AND it.department = 'insurance'
    WHERE r.department = 'insurance'""",
    """CREATE OR REPLACE VIEW analytics.blockers AS
    SELECT b.workflow_id, w.tenant_id, b.department, b.kind, b.since, b.resolved_at, w.authorized_at,
           EXTRACT(EPOCH FROM (b.resolved_at - b.since)) / 60.0 AS minutes,
           p.ward, p.payer_type, CASE WHEN p.payer_type = 'cashless' THEN p.payer END AS tpa
    FROM blockers b JOIN workflows w ON w.id = b.workflow_id JOIN patients p ON p.id = w.patient_id
    WHERE w.deleted_at IS NULL""",
    """CREATE OR REPLACE VIEW analytics.approvals AS
    SELECT a.workflow_id, w.tenant_id, a.action, a.risk, a.approver_role, a.status, a.requires_approval,
           a.created_at, a.decided_at, EXTRACT(EPOCH FROM (a.decided_at - a.created_at)) / 60.0 AS minutes
    FROM approval_requests a JOIN workflows w ON w.id = a.workflow_id
    WHERE w.deleted_at IS NULL""",
    # Staff updates: when people (not agents or systems) did something. No names, no event text.
    """CREATE OR REPLACE VIEW analytics.staff_updates AS
    SELECT tenant_id, at FROM audit_logs WHERE actor_kind = 'human'""",
    """CREATE OR REPLACE VIEW analytics.agent_runs AS
    SELECT tenant_id, started_at, agent, mode, status, tokens, latency_ms FROM agent_executions""",
    """CREATE OR REPLACE VIEW analytics.beds AS
    SELECT tenant_id, ward, status FROM beds""",
]

ROLE_SQL = [
    f"""DO $$ BEGIN
      IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{ROLE}') THEN CREATE ROLE {ROLE} NOLOGIN; END IF;
    END $$""",
    f"GRANT {ROLE} TO CURRENT_USER",
    f"GRANT USAGE ON SCHEMA analytics TO {ROLE}",
    f"GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO {ROLE}",
]

DROP = ["DROP SCHEMA IF EXISTS analytics CASCADE"]


async def ensure_analytics(conn) -> bool:
    """Create the views and the read-only role. Returns False (and logs) if the role can't be created; the views are
    still created, and queries then run without switching role."""
    if conn.dialect.name != "postgresql":
        return False
    for sql in VIEWS:
        await conn.execute(text(sql))
    try:
        async with conn.begin_nested():
            for sql in ROLE_SQL:
                await conn.execute(text(sql))
        return True
    except Exception as exc:  # noqa: BLE001 - e.g. the database user has no CREATEROLE
        log.warning("Could not create the read-only analytics role (%s); Ask CareFlow queries will use the app role on views only", exc)
        return False


async def role_available(conn) -> bool:
    return bool(await conn.scalar(text("SELECT pg_has_role(current_user, :r, 'MEMBER') FROM pg_roles WHERE rolname = :r"), {"r": ROLE}))
