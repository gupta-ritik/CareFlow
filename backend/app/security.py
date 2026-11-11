"""Authentication and role-based access control.

Local JWTs (HS256) for development; when CAREFLOW_OIDC_JWKS_URL is set, tokens from an OIDC provider
such as Keycloak are verified against its JWKS instead. Every request resolves to a Principal with a tenant,
and every query in the API is filtered by that tenant.
"""
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_session
from .models import User

# Role display names double as approver roles in the action catalog.
COORDINATOR = "Discharge coordinator"
INSURANCE_LEAD = "Insurance desk lead"
PHARMACY = "Pharmacy"
BILLING = "Billing"
LABORATORY = "Laboratory"
WARD_CLERK = "Ward clerk"
CLINICIAN = "Clinician"
AUDITOR = "Auditor"
ADMIN = "Administrator"
DIRECTOR = "Hospital director"

DEPT_PERMISSIONS = {d: f"dept:{d}" for d in ("documentation", "pharmacy", "laboratory", "insurance", "billing")}
_ALL_DEPTS = list(DEPT_PERMISSIONS.values())

ROLE_PERMISSIONS: dict[str, list[str]] = {
    ADMIN: ["workflow:read", "workflow:start", "approval:decide", "audit:read", "analytics:read", "patient:write",
            "discharge:close", "user:manage", "integration:write", "integration:read", "executive:read", "bed:manage", "dept:read", *_ALL_DEPTS],
    # Dean / medical director: sees everything (real names, every department queue read-only, integrations, executive
    # dashboard), decides any approval and closes discharges. Doesn't edit department work, manage users, or authorize discharge.
    DIRECTOR: ["workflow:read", "workflow:start", "approval:decide", "audit:read", "analytics:read", "discharge:close",
               "executive:read", "integration:read", "dept:read", "ask:use"],
    COORDINATOR: ["workflow:read", "workflow:start", "approval:decide", "audit:read", "analytics:read", "patient:write", "discharge:close",
                  "bed:manage"],
    CLINICIAN: ["workflow:read", "discharge:authorize", DEPT_PERMISSIONS["documentation"]],
    WARD_CLERK: ["workflow:read", "patient:write", "bed:manage", DEPT_PERMISSIONS["documentation"]],
    PHARMACY: ["workflow:read", "analytics:read", DEPT_PERMISSIONS["pharmacy"]],
    LABORATORY: ["workflow:read", DEPT_PERMISSIONS["laboratory"]],
    INSURANCE_LEAD: ["workflow:read", "approval:decide", "audit:read", "analytics:read", DEPT_PERMISSIONS["insurance"]],
    BILLING: ["workflow:read", "analytics:read", DEPT_PERMISSIONS["billing"]],
    AUDITOR: ["workflow:read", "audit:read", "analytics:read"],
}

ROLE_DESCRIPTIONS = {
    DIRECTOR: "Dean or hospital director: executive dashboard, sees every department, approves actions, closes discharges",
    ADMIN: "Manages users and settings; can act for any department",
    COORDINATOR: "Admits patients, approves coordination actions, closes discharges",
    CLINICIAN: "Records clinical discharge authorization; completes discharge documents",
    WARD_CLERK: "Admits patients and completes discharge documentation",
    PHARMACY: "Prepares discharge medicines and reconciliation",
    LABORATORY: "Finalizes and gets acknowledgement for lab reports",
    INSURANCE_LEAD: "Handles payer authorization; approves insurance actions",
    BILLING: "Prepares the final bill",
    AUDITOR: "Read-only review of decisions; patient identity masked",
}


# Roles that can decide any approval, whatever its approver role.
APPROVE_ANY = (ADMIN, DIRECTOR)

PASSWORD_MIN = 12


def password_problem(password: str, email: str = "") -> str | None:
    """Return why a new password is too weak, or None. Existing passwords keep working until they're changed."""
    if len(password) < PASSWORD_MIN:
        return f"Password must be at least {PASSWORD_MIN} characters."
    classes = sum(bool(re.search(p, password)) for p in (r"[a-z]", r"[A-Z]", r"[0-9]", r"[^A-Za-z0-9]"))
    if classes < 2:
        return "Password must mix at least two of: lowercase, uppercase, digits, symbols."
    local = email.split("@")[0].lower()
    if local and len(local) >= 4 and local in password.lower():
        return "Password must not contain your email name."
    if password.lower() in {"password1234", "welcome@1234", "careflow1234", "hospital1234", "admin@123456"}:
        return "That password is too common."
    return None


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    email: str
    role: str
    permissions: tuple[str, ...]

    def can(self, permission: str) -> bool:
        return permission in self.permissions


def create_token(user: User) -> tuple[str, int]:
    s = get_settings()
    ttl = s.jwt_ttl_minutes * 60
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "tid": str(user.tenant_id),
        "role": user.role.name,
        "name": user.name,
        "email": user.email,
        "iss": s.jwt_issuer,
        "iat": now,
        "exp": now + timedelta(seconds=ttl),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256"), ttl


_jwks_client: jwt.PyJWKClient | None = None


def decode_token(token: str) -> dict:
    s = get_settings()
    if s.oidc_jwks_url:
        global _jwks_client
        _jwks_client = _jwks_client or jwt.PyJWKClient(s.oidc_jwks_url)
        key = _jwks_client.get_signing_key_from_jwt(token).key
        return jwt.decode(token, key, algorithms=["RS256"], audience=s.oidc_audience)
    return jwt.decode(token, s.jwt_secret, algorithms=["HS256"], issuer=s.jwt_issuer)


bearer = HTTPBearer(auto_error=False)


async def current_principal(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    try:
        claims = decode_token(creds.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Session is invalid or expired: {exc}") from exc

    try:
        user_id = uuid.UUID(str(claims["sub"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session is invalid.") from exc
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This account is disabled.")
    role = user.role.name
    return Principal(
        user_id=user.id,
        tenant_id=user.tenant_id,
        name=user.name,
        email=user.email,
        role=role,
        permissions=tuple(ROLE_PERMISSIONS.get(role, [])),
    )


def require(permission: str):
    async def checker(p: Principal = Depends(current_principal)) -> Principal:
        if not p.can(permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Your role ({p.role}) can't do this ({permission}).")
        return p

    return checker
