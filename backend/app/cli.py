"""Admin commands.

  python -m app.cli create-admin --hospital "City Hospital" --name "Asha Rao" --email asha@cityhospital.in
  python -m app.cli reset-password --email asha@cityhospital.in
  python -m app.cli load-examples        # two clearly-labelled test patients, admitted but not yet authorized
  python -m app.cli sync-beds            # fill the bed board from admitted patients' ward/bed (after upgrading)

The web app's first-run setup page does the same as create-admin when the database has no users.
"""
import argparse
import asyncio
import getpass
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from . import db
from .api.hospital import _ensure_roles
from .config import get_settings
from .db import Base
from .departments import initial_records
from .models import Patient, Tenant, User
from .repo import ensure_counters
from .security import ADMIN, hash_password, password_problem
from .sim import upsert_record


def _password(arg: str | None, email: str = "") -> str:
    pw = arg or getpass.getpass("Password (12+ characters, mix of letters/digits/symbols): ")
    if (problem := password_problem(pw, email)):
        sys.exit(problem)
    return pw


async def _prepare():
    s = get_settings()
    db.init_engine(s.database_url)
    if s.env == "dev":
        async with db.engine().begin() as c:
            await c.run_sync(Base.metadata.create_all)
    return db.sessionmaker()


async def create_admin(args) -> None:
    sm = await _prepare()
    async with sm() as s:
        tenant = await s.scalar(select(Tenant).order_by(Tenant.created_at).limit(1))
        if tenant is None:
            if not args.hospital:
                sys.exit("No hospital exists yet; pass --hospital NAME.")
            tenant = Tenant(slug=re.sub(r"[^a-z0-9]+", "-", args.hospital.lower()).strip("-")[:40], name=args.hospital)
            s.add(tenant)
            await s.flush()
        roles = await _ensure_roles(s)
        await ensure_counters(s)
        if await s.scalar(select(User).where(User.email == args.email.lower())):
            sys.exit(f"{args.email} already exists; use reset-password.")
        s.add(User(tenant_id=tenant.id, email=args.email.lower(), name=args.name, role_id=roles[ADMIN].id, password_hash=hash_password(_password(args.password, args.email))))
        await s.commit()
    print(f"Administrator {args.email} created for {tenant.name}.")


async def reset_password(args) -> None:
    sm = await _prepare()
    async with sm() as s:
        u = await s.scalar(select(User).where(User.email == args.email.lower()))
        if u is None:
            sys.exit(f"No user {args.email}.")
        u.password_hash = hash_password(_password(args.password, u.email))
        u.is_active = True
        u.failed_logins, u.locked_until = 0, None
        await s.commit()
    print(f"Password reset for {args.email}.")


async def load_examples(args) -> None:
    sm = await _prepare()
    now = datetime.now(timezone.utc)
    async with sm() as s:
        tenant = await s.scalar(select(Tenant).order_by(Tenant.created_at).limit(1))
        if tenant is None or not await s.scalar(select(func.count()).select_from(User)):
            sys.exit("Set up CareFlow first (web setup page or create-admin).")
        examples = [
            ("Test Patient One", "MRN TEST-0001", "Ward 2C", "Bed 7", "Self-pay", "self_pay", "Dr. Example", False,
             {"documentation": {"present": ["diagnosis_summary", "procedures", "discharge_medicines_list", "follow_up_plan", "diet_advice",
                                            "warning_signs", "contact_numbers", "investigations_summary", "condition_at_discharge", "attending_name",
                                            "registration_number"]}}),
            ("Test Patient Two", "MRN TEST-0002", "Ward 4B", "Bed 3", "Example Insurance (cashless)", "cashless", "Dr. Example", True,
             {"insurance": {"status": "PENDING_PAYER", "submitted_at": (now - timedelta(minutes=20)).isoformat()},
              "laboratory": {"reports": [{"id": "LAB-TEST-1", "status": "final", "acknowledged": False}]}}),
        ]
        added = 0
        for name, mrn, ward, bed, payer, ptype, attending, lab, overrides in examples:
            if await s.scalar(select(Patient).where(Patient.tenant_id == tenant.id, Patient.mrn == mrn)):
                continue
            ref = f"pt-{uuid.uuid4().hex[:10]}"
            s.add(Patient(tenant_id=tenant.id, ref=ref, encounter_ref=f"enc-{uuid.uuid4().hex[:10]}", display_name=name, mrn=mrn, ward=ward, bed=bed,
                          payer=payer, payer_type=ptype, attending=attending, admitted_at=now - timedelta(hours=30), has_lab_orders=lab))
            for dept, data in initial_records(ptype, lab).items():
                await upsert_record(s, tenant.id, ref, dept, {**data, **overrides.get(dept, {})})
            added += 1
        await s.commit()
    print(f"Added {added} example patient(s). Authorize discharge for them from the Patients page to see CareFlow work.")


async def sync_beds(args) -> None:
    """Create/occupy beds for admitted patients that have a ward and bed (for data from before bed tracking)."""
    from .integrations.service import occupy_bed

    sm = await _prepare()
    n = 0
    async with sm() as s:
        for pt in (await s.scalars(select(Patient).where(Patient.status == "admitted"))).all():
            if pt.ward and pt.bed:
                await occupy_bed(s, pt.tenant_id, pt.ward, pt.bed, pt.ref, "sync-beds")
                n += 1
        await s.commit()
    print(f"Beds synced for {n} admitted patient(s).")


def build_parser() -> argparse.ArgumentParser:
    """The command-line interface. scripts/gen_docs.py reads this to generate the CLI reference."""
    ap = argparse.ArgumentParser(prog="python -m app.cli", description="CareFlow administration commands.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("create-admin", help="Create the hospital (if needed) and an administrator account.")
    a.add_argument("--hospital", help="Hospital name; needed only when the database has no hospital yet.")
    a.add_argument("--name", required=True, help="The administrator's full name.")
    a.add_argument("--email", required=True, help="The administrator's sign-in email.")
    a.add_argument("--password", help="Password (12+ characters). Omit to be asked without echoing it.")
    r = sub.add_parser("reset-password", help="Set a new password for an account and unlock it.")
    r.add_argument("--email", required=True, help="Email of the account to reset.")
    r.add_argument("--password", help="New password (12+ characters). Omit to be asked without echoing it.")
    sub.add_parser("load-examples", help="Add two clearly labelled test patients, admitted but not yet authorized.")
    sub.add_parser("sync-beds", help="Fill the bed board from admitted patients' ward and bed (run once after upgrading).")
    return ap


def main() -> None:
    args = build_parser().parse_args()
    fn = {"create-admin": create_admin, "reset-password": reset_password, "load-examples": load_examples, "sync-beds": sync_beds}[args.cmd]
    asyncio.run(fn(args))


if __name__ == "__main__":
    main()
