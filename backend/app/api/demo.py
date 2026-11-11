"""Public demo endpoints (see app.demo). Both answer 404 unless CAREFLOW_DEMO_MODE=true."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import demo
from ..config import get_settings
from ..db import get_session
from ..models import User
from ..security import ROLE_PERMISSIONS, create_token
from .routes import rate_limit

router = APIRouter(prefix="/api/demo", tags=["demo"])


class DemoLoginIn(BaseModel):
    role: str


@router.get("")
async def demo_info(request: Request):
    """Whether the demo is on, which roles can be tried, and whether its data is being rebuilt right now."""
    s = get_settings()
    if not s.demo_mode:
        return {"enabled": False}
    await demo.refresh_if_stale(request.app.state.engine, request.app.state.sessionmaker, s)
    return {"enabled": True, "preparing": demo.preparing(), "resetHours": s.demo_reset_hours, "roles": demo.roles_payload()}


@router.post("/login")
async def demo_login(body: DemoLoginIn, request: Request, session: AsyncSession = Depends(get_session)):
    """Sign in to the demo hospital as one role. No password: these accounts hold only synthetic data."""
    s = get_settings()
    if not s.demo_mode:
        raise HTTPException(404, "Demo mode is off.")
    await rate_limit(request, f"demo:{request.client.host if request.client else 'x'}", 20)
    if body.role not in demo.ROLE_BY_KEY:
        raise HTTPException(422, f"Unknown demo role {body.role!r}.")
    await demo.ensure_accounts(session)
    await session.commit()
    await demo.refresh_if_stale(request.app.state.engine, request.app.state.sessionmaker, s)
    user = await session.scalar(select(User).where(User.email == demo.demo_email(body.role)))
    token, ttl = create_token(user)
    role, home = demo.ROLE_BY_KEY[body.role]
    return {"accessToken": token, "tokenType": "bearer", "expiresIn": ttl, "home": home, "preparing": demo.preparing(),
            "user": {"name": user.name, "email": user.email, "role": role, "permissions": ROLE_PERMISSIONS.get(role, []),
                     "demo": True, "demoRole": body.role}}
