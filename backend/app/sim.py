"""Simulated department systems.

Real hospitals run separate pharmacy, billing, TPA/insurance, documentation and lab systems. CareFlow talks to them
through `DepartmentGateway`; this module is the simulated implementation used for development, demos and evals.
Each record can carry a `_fault` to exercise failure handling:
  down     every call raises DepartmentUnavailable
  timeout  every call raises DepartmentTimeout
  slow     calls succeed after a delay
  noop     writes report success but change nothing (verification must catch this)
"""
import asyncio
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import SimDepartmentRecord

DEPARTMENTS = ("documentation", "pharmacy", "laboratory", "insurance", "billing")


class DepartmentError(Exception):
    def __init__(self, department: str, message: str):
        super().__init__(message)
        self.department = department


class DepartmentUnavailable(DepartmentError):
    pass


class DepartmentTimeout(DepartmentError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DepartmentGateway:
    """Typed tool surface. Each method maps to a tool named in the spec (get_pharmacy_tasks, ...)."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID, slow_seconds: float = 0.2):
        self.session = session
        self.tenant_id = tenant_id
        self.slow_seconds = slow_seconds

    async def _row(self, patient_ref: str, department: str) -> SimDepartmentRecord | None:
        return await self.session.scalar(
            select(SimDepartmentRecord).where(
                SimDepartmentRecord.tenant_id == self.tenant_id,
                SimDepartmentRecord.patient_ref == patient_ref,
                SimDepartmentRecord.department == department,
            )
        )

    async def _guard(self, department: str, data: dict) -> None:
        fault = data.get("_fault")
        if fault == "down":
            raise DepartmentUnavailable(department, f"{department} system returned 503")
        if fault == "timeout":
            raise DepartmentTimeout(department, f"{department} system timed out")
        if fault == "slow":
            await asyncio.sleep(self.slow_seconds)

    async def read(self, patient_ref: str, department: str) -> dict | None:
        row = await self._row(patient_ref, department)
        if row is None:
            return None
        await self._guard(department, row.data)
        return deepcopy(row.data)

    async def write(self, patient_ref: str, department: str, mutate) -> dict:
        row = await self._row(patient_ref, department)
        if row is None:
            raise DepartmentError(department, f"No {department} record for {patient_ref}")
        await self._guard(department, row.data)
        if row.data.get("_fault") == "noop":
            return {"ok": True, "note": "accepted"}  # lies: nothing changes
        data = deepcopy(row.data)
        result = mutate(data) or {}
        # CareFlow's own writes must not look like department progress, so the department's updated_at is left alone.
        data["_careflow_write_at"] = now_iso()
        row.data = data
        await self.session.flush()
        return {"ok": True, **result}

    # Spec tool names -------------------------------------------------------------------------
    async def get_pharmacy_tasks(self, patient_ref: str):
        return await self.read(patient_ref, "pharmacy")

    async def get_documents(self, patient_ref: str):
        return await self.read(patient_ref, "documentation")

    async def get_insurance_status(self, patient_ref: str):
        return await self.read(patient_ref, "insurance")

    async def get_billing_status(self, patient_ref: str):
        return await self.read(patient_ref, "billing")

    async def get_lab_orders(self, patient_ref: str):
        return await self.read(patient_ref, "laboratory")


async def upsert_record(session: AsyncSession, tenant_id: uuid.UUID, patient_ref: str, department: str, data: dict[str, Any]):
    row = await session.scalar(
        select(SimDepartmentRecord).where(
            SimDepartmentRecord.tenant_id == tenant_id,
            SimDepartmentRecord.patient_ref == patient_ref,
            SimDepartmentRecord.department == department,
        )
    )
    if row is None:
        row = SimDepartmentRecord(tenant_id=tenant_id, patient_ref=patient_ref, department=department, data=data)
        session.add(row)
    else:
        row.data = data
    await session.flush()
    return row


async def patch_record(session: AsyncSession, tenant_id: uuid.UUID, patient_ref: str, department: str, patch: dict):
    """Used by demo controls and eval scenarios to move a department forward (or break it)."""
    row = await session.scalar(
        select(SimDepartmentRecord).where(
            SimDepartmentRecord.tenant_id == tenant_id,
            SimDepartmentRecord.patient_ref == patient_ref,
            SimDepartmentRecord.department == department,
        )
    )
    if row is None:
        raise DepartmentError(department, f"No {department} record for {patient_ref}")
    data = deepcopy(row.data)
    for key, value in patch.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    data["updated_at"] = patch.get("updated_at", now_iso())
    row.data = data
    await session.flush()
    return data
