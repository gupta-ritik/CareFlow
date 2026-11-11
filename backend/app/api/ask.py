"""Ask CareFlow API: a question streamed as Server-Sent Events, history, the numbers behind an answer, and feedback."""
import hashlib
import json
import logging
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ask.graph import AskContext, run_ask, save_turn
from ..ask.schemas import AskIn, FeedbackIn
from ..config import get_settings
from ..db import get_session
from ..models import AskSession, AskTurn
from ..redaction import redact
from ..security import Principal, require

router = APIRouter(prefix="/api/ask")
log = logging.getLogger("careflow.ask")
_local_hits: dict[str, list[float]] = defaultdict(list)


async def _rate_limit(redis, user_id) -> None:
    """30 questions per user per hour (CAREFLOW_ASK_RATE_PER_HOUR). Redis first; an in-memory counter if Redis is down."""
    limit = get_settings().ask_rate_per_hour
    key = f"careflow:ask:rl:{user_id}"
    n = None
    if redis is not None:
        try:
            bucket = f"{key}:{int(time.time() // 3600)}"
            n = await redis.incr(bucket)
            if n == 1:
                await redis.expire(bucket, 3600)
        except Exception as exc:  # noqa: BLE001
            log.warning("Ask rate limit: Redis unavailable (%s), counting in memory", exc)
            n = None
    if n is None:
        now = time.time()
        _local_hits[key] = [t for t in _local_hits[key] if now - t < 3600] + [now]
        n = len(_local_hits[key])
    if n > limit:
        raise HTTPException(429, f"You've asked {limit} questions in the last hour, the limit. Please try again later.")


def _cache_key(tenant_id, question: str, local_date: str) -> str:
    norm = re.sub(r"\s+", " ", re.sub(r"[^\w\s:-]", "", question.lower())).strip()
    # Relative periods ("last Monday") resolve per local day, so the date is part of the key.
    return f"careflow:ask:cache:{tenant_id}:{local_date}:{hashlib.sha256(norm.encode()).hexdigest()[:32]}"


def _sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"


@router.post("")
async def ask(body: AskIn, request: Request, p: Principal = Depends(require("ask:use"))):
    s = get_settings()
    redis = getattr(request.app.state, "redis", None)
    await _rate_limit(redis, p.user_id)
    sm = request.app.state.sessionmaker
    ctx = AskContext(sm, p.tenant_id, p.user_id, p.name, body.session_id)
    local_date = datetime.now(ZoneInfo(ctx.tz)).date().isoformat()
    key = _cache_key(p.tenant_id, body.question, local_date)

    async def stream():
        if redis is not None and s.ask_cache_seconds > 0:
            try:
                hit = await redis.get(key)
            except Exception:  # noqa: BLE001
                hit = None
            if hit:
                cached = json.loads(hit)
                yield _sse({"type": "progress", "node": "cache", "message": "Using the answer to the same question from the last few minutes…"})
                state = {"question": body.question, "redacted": cached.get("question"), "status": cached["status"],
                         "answer": {"sentences": cached["sentences"], "text": cached["answer"], "based_on": cached["basedOn"],
                                    "follow_ups": cached["followUps"], "chart": cached["chart"], "partial": cached["partial"]},
                         "results": cached["results"], "llm_calls": [], "plan_mode": "cache"}
                ids = await save_turn(ctx, state, 0)
                yield _sse({**cached, **{"turnId": ids["turn_id"], "sessionId": ids["session_id"], "cached": True, "latencyMs": 0, "tokens": 0}})
                return
        final = None
        async for event in run_ask(ctx, body.question):
            if event["type"] == "answer":
                final = event
            yield _sse(event)
        if final and final["status"] == "answered" and not final["partial"] and redis is not None and s.ask_cache_seconds > 0:
            try:
                await redis.set(key, json.dumps(final, default=str), ex=s.ask_cache_seconds)
            except Exception:  # noqa: BLE001
                pass

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


def _turn_out(t: AskTurn) -> dict:
    a = t.answer or {}
    return {"id": str(t.id), "sessionId": str(t.session_id), "question": t.question, "status": t.status, "partial": t.partial,
            "answer": a.get("text", ""), "message": a.get("message", ""), "sentences": a.get("sentences", []),
            "basedOn": a.get("based_on", ""), "followUps": a.get("follow_ups", []), "chart": a.get("chart"), "mode": t.mode,
            "model": t.model, "tokens": t.tokens, "latencyMs": t.latency_ms, "rating": t.rating, "feedback": t.feedback,
            "createdAt": t.created_at, "results": t.results}


async def _own_session(s: AsyncSession, p: Principal, session_id: str) -> AskSession:
    try:
        sid = uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(404, "Conversation not found.") from exc
    sess = await s.scalar(select(AskSession).where(AskSession.id == sid, AskSession.tenant_id == p.tenant_id, AskSession.user_id == p.user_id))
    if sess is None:
        raise HTTPException(404, "Conversation not found.")
    return sess


async def _own_turn(s: AsyncSession, p: Principal, turn_id: str) -> AskTurn:
    try:
        tid = uuid.UUID(turn_id)
    except ValueError as exc:
        raise HTTPException(404, "Answer not found.") from exc
    t = await s.scalar(select(AskTurn).where(AskTurn.id == tid, AskTurn.tenant_id == p.tenant_id, AskTurn.user_id == p.user_id))
    if t is None:
        raise HTTPException(404, "Answer not found.")
    return t


@router.get("/sessions")
async def sessions(p: Principal = Depends(require("ask:use")), s: AsyncSession = Depends(get_session)):
    rows = (await s.execute(select(AskSession, func.count(AskTurn.id)).outerjoin(AskTurn, AskTurn.session_id == AskSession.id)
                            .where(AskSession.tenant_id == p.tenant_id, AskSession.user_id == p.user_id)
                            .group_by(AskSession.id).order_by(AskSession.updated_at.desc()).limit(50))).all()
    return [{"id": str(x.id), "title": x.title, "turns": n, "createdAt": x.created_at, "updatedAt": x.updated_at} for x, n in rows]


@router.get("/sessions/{session_id}")
async def session_detail(session_id: str, p: Principal = Depends(require("ask:use")), s: AsyncSession = Depends(get_session)):
    sess = await _own_session(s, p, session_id)
    turns = (await s.scalars(select(AskTurn).where(AskTurn.session_id == sess.id).order_by(AskTurn.created_at))).all()
    return {"id": str(sess.id), "title": sess.title, "createdAt": sess.created_at, "turns": [_turn_out(t) for t in turns]}


@router.get("/turns/{turn_id}/evidence")
async def evidence(turn_id: str, p: Principal = Depends(require("ask:use")), s: AsyncSession = Depends(get_session)):
    """The stored results behind an answer ("See the numbers"): measure, filters, period and the aggregate values."""
    t = await _own_turn(s, p, turn_id)
    return {"turnId": str(t.id), "question": t.question, "claims": t.claims, "steps": t.steps, "results": t.results}


@router.post("/turns/{turn_id}/feedback")
async def feedback(turn_id: str, body: FeedbackIn, p: Principal = Depends(require("ask:use")), s: AsyncSession = Depends(get_session)):
    t = await _own_turn(s, p, turn_id)
    t.rating = body.rating
    t.feedback = redact(body.comment.strip())[:1000] if body.comment else None
    await s.commit()
    return {"ok": True, "rating": t.rating}
