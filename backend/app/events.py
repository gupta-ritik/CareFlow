"""Redis Streams event bus.

Stream `careflow:events`, consumer group `careflow-workers`. Each message carries event_id, type, tenant,
patient_ref, occurred_at, correlation_id, payload and attempt. The consumer:
  - processes through Engine.handle_event (idempotent on (tenant, event_id) in Postgres)
  - acks on success
  - on failure re-publishes with attempt+1, and after MAX_ATTEMPTS moves it to `careflow:events:dlq`
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from redis.asyncio import Redis
from sqlalchemy import select

from .models import Tenant

STREAM = "careflow:events"
DLQ = "careflow:events:dlq"
GROUP = "careflow-workers"
MAX_ATTEMPTS = 3
log = logging.getLogger("careflow.events")


def make_event(etype: str, tenant: str, patient_ref: str, payload: dict | None = None, event_id: str | None = None, correlation_id: str | None = None) -> dict:
    eid = event_id or f"evt-{uuid.uuid4().hex[:12]}"
    return {
        "event_id": eid,
        "type": etype,
        "tenant": tenant,
        "patient_ref": patient_ref,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id or eid,
        "payload": payload or {},
        "attempt": 1,
    }


async def publish(redis: Redis, event: dict, stream: str = STREAM) -> str:
    fields = {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in event.items()}
    return await redis.xadd(stream, fields, maxlen=100_000, approximate=True)


def _decode(fields: dict) -> dict:
    out = {k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v for k, v in fields.items()}
    out["payload"] = json.loads(out.get("payload") or "{}")
    out["attempt"] = int(out.get("attempt", 1))
    return out


async def ensure_group(redis: Redis) -> None:
    try:
        await redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except Exception as exc:  # BUSYGROUP: already exists
        if "BUSYGROUP" not in str(exc):
            raise


async def process_batch(redis: Redis, engine, sessionmaker, consumer: str = "worker-1", block_ms: int = 2000, count: int = 10) -> int:
    """Read and process one batch. Returns how many messages were handled."""
    resp = await redis.xreadgroup(GROUP, consumer, {STREAM: ">"}, count=count, block=block_ms)
    handled = 0
    for _stream, messages in resp or []:
        for msg_id, fields in messages:
            event = _decode(fields)
            try:
                async with sessionmaker() as s:
                    tenant = await s.scalar(select(Tenant).where(Tenant.slug == event["tenant"]))
                if tenant is None:
                    raise ValueError(f"Unknown tenant {event['tenant']}")
                outcome = await engine.handle_event(tenant.id, event)
                log.info("event %s %s -> %s", event["event_id"], event["type"], outcome)
            except Exception as exc:
                log.exception("event %s failed (attempt %s)", event.get("event_id"), event["attempt"])
                if event["attempt"] >= MAX_ATTEMPTS:
                    await publish(redis, {**event, "error": str(exc)[:500]}, DLQ)
                else:
                    await publish(redis, {**event, "attempt": event["attempt"] + 1})
            finally:
                await redis.xack(STREAM, GROUP, msg_id)
                handled += 1
    return handled


async def consume_forever(redis: Redis, engine, sessionmaker, consumer: str = "worker-1", stop: asyncio.Event | None = None) -> None:
    await ensure_group(redis)
    while not (stop and stop.is_set()):
        try:
            await process_batch(redis, engine, sessionmaker, consumer)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("consumer loop error; retrying in 2s")
            await asyncio.sleep(2)
