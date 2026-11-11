"""Small persistence helpers shared by graph nodes and API services."""
import uuid
import weakref

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AgentExecution, AuditLog, Counter, Evidence, Workflow
from .redaction import redact

_STARTS = {"WF": 2000, "EV": 1000, "BL": 300, "AC": 500, "AU": 9000}
# Codes written many times per agent step are reserved in blocks, so most rows need no counter round trip.
# Low-volume codes that people read and quote (WF, BL, AC) stay gapless and are taken one at a time.
BLOCK_SIZES = {"AU": 50, "EV": 25}
# engine -> prefix -> unused reserved values. Keyed by engine so a test that recreates the database starts clean.
_blocks: "weakref.WeakKeyDictionary[object, dict[str, list[int]]]" = weakref.WeakKeyDictionary()


async def next_code(session: AsyncSession, prefix: str) -> str:
    size = BLOCK_SIZES.get(prefix, 1)
    if size > 1 and session.bind is not None:
        return f"{prefix}-{await _from_block(session.bind, prefix, size)}"
    return f"{prefix}-{await _bump(session, prefix, 1)}"


async def _bump(session_or_conn, prefix: str, by: int) -> int:
    """Atomic per-prefix counter (UPDATE ... RETURNING locks the row in Postgres). Returns the new value."""
    stmt = update(Counter).where(Counter.name == prefix).values(value=Counter.value + by).returning(Counter.value)
    value = await session_or_conn.scalar(stmt)
    if value is None:
        await ensure_counters(session_or_conn, [prefix])
        value = await session_or_conn.scalar(stmt)
    return value


async def _from_block(bind, prefix: str, size: int) -> int:
    engine = getattr(bind, "sync_engine", bind)
    pool = _blocks.setdefault(engine, {}).setdefault(prefix, [])
    if not pool:
        # Reserve on its own connection and commit at once: if the caller's transaction later rolls back, the block
        # must stay reserved, or another process could be handed the same values. Unused values become gaps.
        async with bind.begin() as conn:
            top = await _bump(conn, prefix, size)
        pool.extend(range(top - size + 1, top + 1))
    return pool.pop(0)


async def ensure_counters(session, prefixes: list[str] | None = None) -> None:
    """Create counters if missing; safe when several transactions race (ON CONFLICT DO NOTHING). Takes a session or connection."""
    dialect = session.bind.dialect.name if isinstance(session, AsyncSession) else session.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    for prefix in prefixes or list(_STARTS):
        await session.execute(insert(Counter).values(name=prefix, value=_STARTS.get(prefix, 1) - 1).on_conflict_do_nothing(index_elements=["name"]))


async def workflow_by_code(session: AsyncSession, tenant_id: uuid.UUID, code: str) -> Workflow | None:
    return await session.scalar(select(Workflow).where(Workflow.tenant_id == tenant_id, Workflow.code == code, Workflow.deleted_at.is_(None)))


async def audit(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    workflow_code: str,
    actor_kind: str,
    actor: str,
    event: str,
    detail: str = "",
    evidence_ids: list[str] | None = None,
    latency_ms: int | None = None,
    tokens: int | None = None,
) -> AuditLog:
    row = AuditLog(
        tenant_id=tenant_id,
        code=await next_code(session, "AU"),
        workflow_code=workflow_code,
        actor_kind=actor_kind,
        actor=actor,
        event=redact(event),
        detail=redact(detail),
        evidence_ids=evidence_ids or [],
        latency_ms=latency_ms,
        tokens=tokens,
    )
    session.add(row)  # no flush: the unit of work sends all pending audit rows in one INSERT at the next query or commit
    return row


async def record_agent(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    workflow_code: str,
    agent: str,
    mode: str,
    status: str,
    input_summary: str,
    output_summary: str,
    latency_ms: int,
    tokens: int = 0,
    retries: int = 0,
    error: str | None = None,
) -> None:
    session.add(
        AgentExecution(
            tenant_id=tenant_id,
            workflow_code=workflow_code,
            agent=agent,
            mode=mode,
            status=status,
            input_summary=redact(input_summary)[:2000],
            output_summary=redact(output_summary)[:2000],
            latency_ms=latency_ms,
            tokens=tokens,
            retries=retries,
            error=error,
        )
    )


async def add_evidence(session: AsyncSession, workflow_id: uuid.UUID, department: str, source: str, query: str, observed: str, observed_at, untrusted: bool) -> Evidence:
    row = Evidence(
        workflow_id=workflow_id,
        code=await next_code(session, "EV"),
        department=department,
        source=source,
        query=query,
        observed=redact(observed),
        observed_at=observed_at,
        untrusted=untrusted,
    )
    session.add(row)
    return row
