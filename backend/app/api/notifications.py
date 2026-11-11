"""Each person's alert settings (Account page) and the Telegram bot webhook. See app/notify.py."""
import hmac
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import notify
from ..config import get_settings
from ..db import get_session
from ..demo import DEMO_SLUG
from ..models import NotificationChannel, Tenant, User
from ..security import Principal, current_principal
from .routes import rate_limit

router = APIRouter(prefix="/api", tags=["notifications"])


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=10, max_length=200)
    auth: str = Field(min_length=10, max_length=100)


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(max_length=2000)
    keys: PushKeys
    label: str = Field("This device", max_length=200)


def _channel_out(ch: NotificationChannel) -> dict:
    return {"id": str(ch.id), "kind": ch.kind, "label": ch.label, "createdAt": ch.created_at, "endpoint": ch.address if ch.kind == "webpush" else None}


async def _is_demo(s: AsyncSession, p: Principal) -> bool:
    return await s.scalar(select(Tenant.slug).where(Tenant.id == p.tenant_id)) == DEMO_SLUG


async def _own_user(s: AsyncSession, p: Principal) -> User:
    if await _is_demo(s, p):  # shared demo accounts: alerts would go to whoever connected last
        raise HTTPException(403, "Alerts aren't available for demo accounts.")
    return await s.get(User, p.user_id)


@router.get("/notifications")
async def notification_settings(p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    cfg = get_settings()
    channels = (await s.scalars(select(NotificationChannel).where(NotificationChannel.user_id == p.user_id)
                                .order_by(NotificationChannel.created_at))).all()
    return {"webPush": {"enabled": notify.push_enabled(cfg), "publicKey": cfg.vapid_public_key or None},
            "telegram": {"enabled": notify.telegram_enabled(cfg)},
            "demo": await _is_demo(s, p), "channels": [_channel_out(c) for c in channels]}


@router.post("/notifications/webpush", status_code=201)
async def add_push_subscription(body: PushSubscriptionIn, p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    if not notify.push_enabled(get_settings()):
        raise HTTPException(503, "Push notifications aren't set up on this server (CAREFLOW_VAPID_PUBLIC_KEY / _PRIVATE_KEY).")
    if not notify.push_host_allowed(body.endpoint):
        raise HTTPException(422, "That isn't a browser push service address.")
    user = await _own_user(s, p)
    ch = await notify.save_channel(s, user, "webpush", body.endpoint, body.keys.model_dump(), body.label)
    await s.commit()
    return _channel_out(ch)


@router.post("/notifications/telegram/link")
async def telegram_link(p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    """A one-time t.me link (valid 15 minutes) that connects the Telegram chat that opens it to this account."""
    cfg = get_settings()
    if not notify.telegram_enabled(cfg):
        raise HTTPException(503, "Telegram isn't set up on this server (CAREFLOW_TELEGRAM_BOT_TOKEN).")
    await _own_user(s, p)
    username = await notify.bot_username(cfg)
    if not username:
        raise HTTPException(502, "Couldn't reach the Telegram bot. Check CAREFLOW_TELEGRAM_BOT_TOKEN.")
    return {"url": f"https://t.me/{username}?start={notify.link_code(p.user_id, cfg)}", "expiresInMinutes": notify.LINK_TTL_SECONDS // 60}


@router.delete("/notifications/{channel_id}", status_code=204)
async def remove_channel(channel_id: uuid.UUID, p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    result = await s.execute(delete(NotificationChannel).where(NotificationChannel.id == channel_id, NotificationChannel.user_id == p.user_id))
    if result.rowcount == 0:
        raise HTTPException(404, "No such alert channel on your account.")
    await s.commit()
    return Response(status_code=204)


@router.post("/notifications/test")
async def send_test(request: Request, p: Principal = Depends(current_principal), s: AsyncSession = Depends(get_session)):
    await rate_limit(request, f"notify-test:{p.user_id}", 5)
    channels = (await s.scalars(select(NotificationChannel).where(NotificationChannel.user_id == p.user_id))).all()
    if not channels:
        raise HTTPException(409, "Turn on notifications on this device or connect Telegram first.")
    alert = notify.Alert(title="CareFlow test alert", body="Alerts are working. Approvals and reminders for your role will arrive like this.",
                         path="/account", tag="careflow-test")
    sent = await notify.deliver(request.app.state.sessionmaker, list(channels), alert)
    return {"sent": sent, "channels": len(channels)}


@router.post("/telegram/webhook", include_in_schema=False)
async def telegram_webhook(request: Request, x_telegram_bot_api_secret_token: str = Header(default="")):
    cfg = get_settings()
    if not notify.telegram_enabled(cfg):
        raise HTTPException(404, "Not found")
    if not hmac.compare_digest(x_telegram_bot_api_secret_token, notify.webhook_secret(cfg)):
        raise HTTPException(403, "Bad secret")
    try:
        update = await request.json()
    except ValueError:
        return {"ok": True}
    await notify.handle_telegram_update(request.app.state.sessionmaker, update, cfg)
    return {"ok": True}  # always 200, or Telegram retries the same update
