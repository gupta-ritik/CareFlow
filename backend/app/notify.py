"""Staff alerts on phones: Web Push (installed app or browser) and Telegram. Both are free and both are optional.

When an action needs someone's approval, everyone with the approver role is alerted; when CareFlow sends a department a
reminder, everyone working that department's queue is alerted. Each person turns alerts on for themselves on the
Account page: "Turn on notifications on this device" (Web Push) and/or "Connect Telegram".

Privacy: alerts can appear on a locked phone, so they carry the ward, bed and what's needed, never the patient's
name or MRN (free text goes through `redaction.redact` with the patient's identifiers as a second guard).

Setup:
  Web Push  CAREFLOW_VAPID_PUBLIC_KEY / CAREFLOW_VAPID_PRIVATE_KEY from `python -m app.cli vapid-keys`.
  Telegram  CAREFLOW_TELEGRAM_BOT_TOKEN from @BotFather. The webhook (POST /api/telegram/webhook) registers itself at
            start-up at CAREFLOW_PUBLIC_API_URL, or RENDER_EXTERNAL_URL on Render.
Delivery runs in the background after the database commit; a failure is logged and never affects the workflow.
Subscriptions the push service or Telegram reports as gone are deleted.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete, select

from .config import Settings, get_settings
from .models import NotificationChannel, Role, User
from .redaction import redact
from .security import ADMIN, ROLE_PERMISSIONS

log = logging.getLogger("careflow.notify")

TELEGRAM_API = "https://api.telegram.org"
LINK_TTL_SECONDS = 15 * 60
# Web Push endpoints CareFlow will POST to. Anything else is refused, so a subscription can't point the server at
# an internal address.
PUSH_HOSTS = ("fcm.googleapis.com", "android.googleapis.com", "push.services.mozilla.com", "push.apple.com", "notify.windows.com")
DEPT_LABEL = {"documentation": "Documentation", "pharmacy": "Pharmacy", "laboratory": "Laboratory", "insurance": "Insurance",
              "billing": "Billing"}

_tasks: set[asyncio.Task] = set()
_bot_username: str | None = None


# ---- configuration ------------------------------------------------------------------------------------------------
def push_enabled(s: Settings) -> bool:
    return bool(s.vapid_public_key and s.vapid_private_key)


def telegram_enabled(s: Settings) -> bool:
    return bool(s.telegram_bot_token)


def web_url(s: Settings) -> str:
    """The website's public address, for "Open in CareFlow" links (empty if unknown)."""
    if s.public_web_url:
        return s.public_web_url.rstrip("/")
    return next((o.rstrip("/") for o in s.cors_origins if o.startswith("https://")), "")


def api_url(s: Settings) -> str:
    return (s.public_api_url or "").rstrip("/")


def push_host_allowed(endpoint: str) -> bool:
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


# ---- messages -------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Alert:
    title: str
    body: str
    path: str  # page to open, e.g. "/approvals"
    tag: str  # a newer alert with the same tag replaces the older one on the phone


def _where(ward: str | None, bed: str | None) -> str:
    return " · ".join(x for x in (ward, bed) if x) or "A patient"


def _label(action: str) -> str:
    return action.replace("_", " ").capitalize()


def approval_alert(code: str, department: str, ward: str | None, bed: str | None, action: str, approver_role: str) -> Alert:
    return Alert(title=f"Approval needed · {DEPT_LABEL.get(department, department.title())}",
                 body=f"{_where(ward, bed)}: {_label(action)}. Needs {approver_role.lower()} approval.",
                 path="/approvals", tag=f"approval-{code}")


def reminder_alert(code: str, department: str, ward: str | None, bed: str | None, message: str, identifiers: list[str]) -> Alert:
    return Alert(title=f"Reminder · {DEPT_LABEL.get(department, department.title())}",
                 body=f"{_where(ward, bed)}: {redact(message, identifiers)}", path="/queue", tag=f"reminder-{code}")


# ---- recipients -----------------------------------------------------------------------------------------------------
def dept_roles(department: str) -> list[str]:
    """Roles that work a department's queue (not the Administrator, who can act anywhere but isn't on duty for it)."""
    perm = f"dept:{department}"
    return [role for role, perms in ROLE_PERMISSIONS.items() if perm in perms and role != ADMIN]


async def _channels_for_roles(sm, tenant_id: uuid.UUID, roles: list[str]) -> list[NotificationChannel]:
    async with sm() as s:
        q = (select(NotificationChannel).join(User, User.id == NotificationChannel.user_id).join(Role, Role.id == User.role_id)
             .where(NotificationChannel.tenant_id == tenant_id, User.is_active.is_(True), Role.name.in_(roles)))
        return list((await s.scalars(q)).all())


# ---- sending --------------------------------------------------------------------------------------------------------
def _schedule(coro) -> None:
    try:
        task = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def send_to_roles(sm, tenant_id: uuid.UUID, roles: list[str], alert: Alert) -> None:
    """Alert everyone in these roles, in the background. Returns at once; does nothing if no channel is configured."""
    s = get_settings()
    if not roles or not (push_enabled(s) or telegram_enabled(s)):
        return

    async def run():
        try:
            await deliver(sm, await _channels_for_roles(sm, tenant_id, roles), alert)
        except Exception:  # noqa: BLE001 - alerts are best-effort; the workflow already committed
            log.exception("Sending alert %s failed", alert.tag)

    _schedule(run())


async def deliver(sm, channels: list[NotificationChannel], alert: Alert) -> int:
    """Send one alert to each channel; delete channels that no longer exist. Returns how many were delivered."""
    s = get_settings()
    results = await asyncio.gather(*(_send(ch, alert, s) for ch in channels))
    gone = [ch.id for ch, alive in zip(channels, results) if alive is False]
    if gone:
        async with sm() as session:
            await session.execute(delete(NotificationChannel).where(NotificationChannel.id.in_(gone)))
            await session.commit()
    return sum(1 for r in results if r is True)


async def _send(ch: NotificationChannel, alert: Alert, s: Settings) -> bool | None:
    """True = delivered, False = the channel is gone for good, None = a temporary failure."""
    try:
        if ch.kind == "webpush" and push_enabled(s):
            return await _send_push(ch, alert, s)
        if ch.kind == "telegram" and telegram_enabled(s):
            return await _send_telegram(ch, alert, s)
    except Exception as exc:  # noqa: BLE001
        log.warning("Alert to %s channel %s failed: %s", ch.kind, ch.id, exc)
    return None


async def _send_push(ch: NotificationChannel, alert: Alert, s: Settings) -> bool | None:
    from py_vapid import Vapid01
    from pywebpush import WebPushException, webpush

    if not push_host_allowed(ch.address):
        return False
    payload = json.dumps({"title": alert.title, "body": alert.body, "url": alert.path, "tag": alert.tag})
    subject = s.vapid_subject or web_url(s) or "mailto:careflow@example.com"
    try:
        await asyncio.to_thread(webpush, subscription_info={"endpoint": ch.address, "keys": ch.keys}, data=payload,
                                vapid_private_key=Vapid01.from_string(s.vapid_private_key), vapid_claims={"sub": subject},
                                ttl=3600, timeout=10)
    except WebPushException as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status in (404, 410):  # the browser unsubscribed or the app was uninstalled
            return False
        log.warning("Web Push failed (%s): %s", status, exc)
        return None
    return True


async def telegram_call(s: Settings, method: str, payload: dict) -> dict:
    """Call the Telegram Bot API. The token is in the URL, so the URL is never logged."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(f"{TELEGRAM_API}/bot{s.telegram_bot_token}/{method}", json=payload)
    try:
        return r.json()
    except ValueError:
        return {"ok": False, "error_code": r.status_code, "description": "not JSON"}


async def _send_telegram(ch: NotificationChannel, alert: Alert, s: Settings) -> bool | None:
    payload: dict = {"chat_id": ch.address, "text": f"🔔 {alert.title}\n{alert.body}", "disable_web_page_preview": True}
    if (base := web_url(s)).startswith("https://"):  # Telegram only allows https buttons
        payload["reply_markup"] = {"inline_keyboard": [[{"text": "Open in CareFlow", "url": base + alert.path}]]}
    res = await telegram_call(s, "sendMessage", payload)
    if res.get("ok"):
        return True
    if res.get("error_code") in (400, 403):  # chat not found, or the person blocked the bot
        log.info("Telegram chat %s is gone: %s", ch.address, res.get("description"))
        return False
    log.warning("Telegram sendMessage failed: %s", res.get("description"))
    return None


# ---- Telegram: connect a chat to a CareFlow account ----------------------------------------------------------------
def _key(s: Settings) -> bytes:
    return hashlib.sha256(("careflow-telegram:" + s.jwt_secret).encode()).digest()


def link_code(user_id: uuid.UUID, s: Settings, now: float | None = None) -> str:
    """A signed, expiring code for t.me/<bot>?start=<code> (56 characters, within Telegram's 64-character limit)."""
    body = user_id.hex + format(int(now or time.time()) + LINK_TTL_SECONDS, "08x")
    return body + hmac.new(_key(s), body.encode(), hashlib.sha256).hexdigest()[:16]


def read_link_code(code: str, s: Settings, now: float | None = None) -> uuid.UUID | None:
    if len(code) != 56 or any(c not in "0123456789abcdef" for c in code):
        return None
    body, sig = code[:40], code[40:]
    if not hmac.compare_digest(sig, hmac.new(_key(s), body.encode(), hashlib.sha256).hexdigest()[:16]):
        return None
    if int(body[32:], 16) < (now or time.time()):
        return None
    return uuid.UUID(body[:32])


def webhook_secret(s: Settings) -> str:
    """Telegram sends this back in X-Telegram-Bot-Api-Secret-Token, proving an update really came from Telegram."""
    return hmac.new(_key(s), b"webhook", hashlib.sha256).hexdigest()


async def bot_username(s: Settings) -> str | None:
    global _bot_username
    if _bot_username is None and telegram_enabled(s):
        res = await telegram_call(s, "getMe", {})
        _bot_username = (res.get("result") or {}).get("username") if res.get("ok") else None
    return _bot_username


async def register_webhook(s: Settings) -> None:
    if not telegram_enabled(s):
        return
    if not api_url(s).startswith("https://"):
        log.info("Telegram webhook not registered: set CAREFLOW_PUBLIC_API_URL to the API's public https address")
        return
    res = await telegram_call(s, "setWebhook", {"url": f"{api_url(s)}/api/telegram/webhook", "secret_token": webhook_secret(s),
                                                "allowed_updates": ["message"]})
    if res.get("ok"):
        log.info("Telegram webhook registered at %s/api/telegram/webhook", api_url(s))
    else:
        log.warning("Telegram setWebhook failed: %s", res.get("description"))


async def save_channel(session, user: User, kind: str, address: str, keys: dict | None, label: str) -> NotificationChannel:
    """Create or re-own a channel (one Telegram chat or one push subscription belongs to one person at a time)."""
    ch = await session.scalar(select(NotificationChannel).where(NotificationChannel.kind == kind, NotificationChannel.address == address))
    if ch is None:
        ch = NotificationChannel(kind=kind, address=address)
        session.add(ch)
    ch.tenant_id, ch.user_id, ch.keys, ch.label = user.tenant_id, user.id, keys or {}, label[:200]
    await session.flush()
    return ch


async def handle_telegram_update(sm, update: dict, s: Settings) -> None:
    """The bot's only conversation: /start <code> connects this chat to an account, /stop disconnects it."""
    from .demo import DEMO_SLUG
    from .models import Tenant

    msg = update.get("message") or {}
    chat = msg.get("chat") or {}
    text = (msg.get("text") or "").strip()
    if not chat.get("id") or not text:
        return
    chat_id = str(chat["id"])

    async def reply(t: str) -> None:
        await telegram_call(s, "sendMessage", {"chat_id": chat_id, "text": t})

    if chat.get("type") != "private":
        return await reply("CareFlow alerts are personal. Please message me directly instead of in a group.")
    if text.startswith("/start"):
        parts = text.split(maxsplit=1)
        user_id = read_link_code(parts[1], s) if len(parts) == 2 else None
        async with sm() as session:
            user = await session.get(User, user_id) if user_id else None
            tenant = await session.get(Tenant, user.tenant_id) if user else None
            if user is None or not user.is_active or tenant is None or tenant.slug == DEMO_SLUG:
                return await reply("This link has expired or isn't valid. In CareFlow, open Account → Connect Telegram to get a new one.")
            who = msg.get("from") or {}
            label = f"Telegram @{who['username']}" if who.get("username") else f"Telegram ({who.get('first_name') or 'chat'})"
            await save_channel(session, user, "telegram", chat_id, None, label)
            await session.commit()
            name, role = user.name, user.role.name
        return await reply(f"Connected to CareFlow as {name} ({role}). You'll get approvals and reminders for your role here. "
                           "Alerts show the ward and bed, never patient names. Send /stop to turn them off.")
    if text in ("/stop", "/disconnect"):
        async with sm() as session:
            await session.execute(delete(NotificationChannel).where(NotificationChannel.kind == "telegram", NotificationChannel.address == chat_id))
            await session.commit()
        return await reply("Disconnected. You won't get CareFlow alerts here any more.")
    await reply("I only send CareFlow alerts. To connect, open CareFlow → Account → Connect Telegram. Send /stop to disconnect.")
