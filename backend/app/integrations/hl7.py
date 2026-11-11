"""HL7 v2 (pipe-delimited, ER7) parsing and message building.

Dependency-free and deliberately small: CareFlow only needs a handful of fields from ADT, ORM/RDE/RDS and ORU
messages. Field numbering follows the standard (MSH-9 is the message type, PID-3 the identifier list, ...).
Clinical values (OBX) are never read: CareFlow tracks report status, not results.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

SB, EB, CR = b"\x0b", b"\x1c", b"\x0d"  # MLLP framing


class Hl7Error(ValueError):
    pass


@dataclass
class Segment:
    name: str
    fields: list[str]  # fields[n] is field n (fields[0] is the segment name)


@dataclass
class Message:
    segments: list[Segment]
    fs: str = "|"
    comp: str = "^"
    rep: str = "~"
    esc: str = "\\"
    sub: str = "&"
    raw: str = ""

    # ---- access ------------------------------------------------------------------------------------
    def all(self, name: str) -> list[Segment]:
        return [s for s in self.segments if s.name == name]

    def first(self, name: str) -> Segment | None:
        return next((s for s in self.segments if s.name == name), None)

    def value(self, seg: Segment | str | None, fld: int, comp: int = 1, rep: int = 0) -> str:
        if isinstance(seg, str):
            seg = self.first(seg)
        if seg is None or fld >= len(seg.fields):
            return ""
        raw = seg.fields[fld]
        if seg.name == "MSH" and fld <= 2:
            return raw
        reps = raw.split(self.rep)
        if rep >= len(reps):
            return ""
        comps = reps[rep].split(self.comp)
        if comp - 1 >= len(comps):
            return ""
        return self.unescape(comps[comp - 1].split(self.sub)[0]).strip()

    def unescape(self, v: str) -> str:
        if self.esc not in v:
            return v
        table = {"F": self.fs, "S": self.comp, "R": self.rep, "T": self.sub, "E": self.esc}
        return re.sub(re.escape(self.esc) + r"([FSRTE])" + re.escape(self.esc), lambda m: table[m.group(1)], v)

    # ---- header shortcuts --------------------------------------------------------------------------
    @property
    def message_type(self) -> str:
        code, trigger = self.value("MSH", 9, 1), self.value("MSH", 9, 2)
        return f"{code}^{trigger}" if trigger else code

    @property
    def control_id(self) -> str:
        return self.value("MSH", 10)

    @property
    def sending_app(self) -> str:
        return self.value("MSH", 3)

    @property
    def version(self) -> str:
        return self.value("MSH", 12) or "2.5"


def parse(text: str) -> Message:
    text = (text or "").strip().strip("\x0b\x1c").strip()
    if not text:
        raise Hl7Error("Empty message")
    text = text.replace("\r\n", "\r").replace("\n", "\r")
    lines = [ln for ln in text.split("\r") if ln.strip()]
    if not lines[0].startswith("MSH") or len(lines[0]) < 8:
        raise Hl7Error("Message must start with an MSH segment")
    fs = lines[0][3]
    enc = lines[0][4:8]
    if len(enc) < 4 or fs in enc:
        raise Hl7Error("MSH-2 encoding characters are invalid")
    comp, rep, esc, sub = enc[0], enc[1], enc[2], enc[3]
    segments = []
    for ln in lines:
        parts = ln.split(fs)
        name = parts[0][:3]
        if not re.fullmatch(r"[A-Z][A-Z0-9]{2}", name):
            raise Hl7Error(f"Invalid segment name {parts[0][:10]!r}")
        if name == "MSH":
            # MSH-1 is the field separator itself, MSH-2 the encoding characters.
            fields = ["MSH", fs, *parts[1:]]
        else:
            fields = parts
        segments.append(Segment(name, fields))
    msg = Message(segments, fs, comp, rep, esc, sub, raw=text)
    if not msg.value("MSH", 9, 1):
        raise Hl7Error("MSH-9 message type is missing")
    if not msg.control_id:
        raise Hl7Error("MSH-10 message control ID is missing")
    return msg


# ---- value helpers ---------------------------------------------------------------------------------
def parse_ts(v: str) -> datetime | None:
    """HL7 DTM: YYYY[MM[DD[HH[MM[SS[.S+]]]]]][+/-ZZZZ]. Without an offset the time is treated as UTC."""
    if not v:
        return None
    m = re.fullmatch(r"(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?(?:\.\d+)?([+-]\d{4})?", v.strip())
    if not m:
        return None
    y, mo, d, h, mi, s, tz = m.groups()
    try:
        dt = datetime(int(y), int(mo or 1), int(d or 1), int(h or 0), int(mi or 0), int(s or 0))
    except ValueError:
        return None
    if tz:
        from datetime import timedelta

        sign = 1 if tz[0] == "+" else -1
        offset = timezone(sign * timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])))
        return dt.replace(tzinfo=offset).astimezone(timezone.utc)
    return dt.replace(tzinfo=timezone.utc)


def ts(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")


def escape(v: str) -> str:
    v = str(v or "")
    return v.replace("\\", "\\E\\").replace("|", "\\F\\").replace("^", "\\S\\").replace("~", "\\R\\").replace("&", "\\T\\").replace("\r", " ").replace("\n", " ")


GOVERNMENT_HINTS = ("GOV", "CGHS", "PMJAY", "PM-JAY", "AYUSHMAN", "ESIC", "ECHS", "STATE SCHEME")


@dataclass
class PatientFields:
    mrn: str
    name: str
    ward: str = ""
    bed: str = ""
    attending: str = ""
    payer: str = ""
    payer_type: str = "self_pay"
    admitted_at: datetime | None = None
    visit_number: str = ""
    patient_class: str = ""
    discharged_at: datetime | None = None
    extra: dict = field(default_factory=dict)


def patient_fields(msg: Message) -> PatientFields:
    pid = msg.first("PID")
    if pid is None:
        raise Hl7Error("PID segment is missing")
    mrn = ""
    # PID-3 can repeat; prefer the identifier typed MR (medical record number).
    for i in range(10):
        ident = msg.value(pid, 3, 1, rep=i)
        if not ident:
            break
        if not mrn or msg.value(pid, 3, 5, rep=i).upper() == "MR":
            mrn = ident
    if not mrn:
        raise Hl7Error("PID-3 patient identifier is missing")
    family, given, middle = msg.value(pid, 5, 1), msg.value(pid, 5, 2), msg.value(pid, 5, 3)
    name = " ".join(x for x in (given, middle, family) if x) or mrn
    pv1 = msg.first("PV1")
    ward = msg.value(pv1, 3, 1)
    room, bed = msg.value(pv1, 3, 2), msg.value(pv1, 3, 3)
    bed = bed or room
    a_family, a_given, a_prefix = msg.value(pv1, 7, 2), msg.value(pv1, 7, 3), msg.value(pv1, 7, 6)
    attending = " ".join(x for x in (a_prefix or ("Dr." if a_family else ""), a_given, a_family) if x)
    in1 = msg.first("IN1")
    payer = msg.value(in1, 4, 1) if in1 else ""
    plan_type = (msg.value(in1, 15, 1) if in1 else "").upper()
    financial = msg.value(pv1, 20, 1).upper()
    if any(h in f"{payer.upper()} {plan_type} {financial}" for h in GOVERNMENT_HINTS):
        payer_type = "government"
    elif in1 is not None and payer and financial not in ("SELF", "SP", "CASH"):
        payer_type = "cashless"
    else:
        payer_type = "self_pay"
    return PatientFields(
        mrn=mrn, name=name, ward=ward, bed=bed, attending=attending, payer=payer or ("Self-pay" if payer_type == "self_pay" else ""),
        payer_type=payer_type, admitted_at=parse_ts(msg.value(pv1, 44)) or parse_ts(msg.value("EVN", 2)) or parse_ts(msg.value("MSH", 7)),
        visit_number=msg.value(pv1, 19, 1), patient_class=msg.value(pv1, 2, 1), discharged_at=parse_ts(msg.value(pv1, 45)),
    )


@dataclass
class Order:
    kind: str  # pharmacy | lab
    control: str  # ORC-1: NW new, CA cancel, DC discontinue, SC status changed, XO change, OK accepted ...
    status: str  # ORC-5 / OBR-25 result status
    order_id: str
    at: datetime | None


RADIOLOGY = {"RAD", "CT", "US", "MR", "XR", "NMS", "RX"}


def orders(msg: Message) -> list[Order]:
    """Group ORC with the RXO/RXE/RXD/OBR that follows it (ORM^O01, RDE^O11, RDS^O13, ORU^R01, OML^O21)."""
    out: list[Order] = []
    current_orc: Segment | None = None
    for seg in msg.segments:
        if seg.name == "ORC":
            current_orc = seg
            continue
        control = msg.value(current_orc, 1) if current_orc else ""
        orc_status = msg.value(current_orc, 5) if current_orc else ""
        at = parse_ts(msg.value(current_orc, 9)) if current_orc else None
        if seg.name in ("RXO", "RXE", "RXD", "RXG"):
            oid = (msg.value(current_orc, 2) or msg.value(current_orc, 3)) if current_orc else ""
            status = "CM" if seg.name == "RXD" else orc_status
            out.append(Order("pharmacy", control or "NW", status, oid or msg.control_id, at))
            current_orc = None if seg.name != "RXE" else current_orc
        elif seg.name == "OBR":
            if msg.value(seg, 24).upper() in RADIOLOGY:
                continue
            oid = msg.value(seg, 3) or msg.value(seg, 2) or (msg.value(current_orc, 3) if current_orc else "")
            if not oid:
                continue
            out.append(Order("lab", control or "NW", msg.value(seg, 25) or orc_status, oid, parse_ts(msg.value(seg, 22)) or at))
    # A message can repeat a segment per item (several RXE lines for one order); keep one entry per (kind, id).
    seen, unique = set(), []
    for o in out:
        if (o.kind, o.order_id) not in seen:
            seen.add((o.kind, o.order_id))
            unique.append(o)
    return unique


FINAL_RESULT = {"F", "C"}  # final, corrected


# ---- building messages -----------------------------------------------------------------------------
def ack(msg: Message | None, code: str, text: str = "", facility: str = "CAREFLOW", control_id: str | None = None) -> str:
    """MSA-1: AA accepted, AE error, AR rejected."""
    trigger = msg.value("MSH", 9, 2) if msg else ""
    to_app = msg.sending_app if msg else ""
    to_fac = msg.value("MSH", 4) if msg else ""
    ctrl = msg.control_id if msg else (control_id or "")
    version = msg.version if msg else "2.5"
    segs = [
        f"MSH|^~\\&|CAREFLOW|{escape(facility)}|{escape(to_app)}|{escape(to_fac)}|{ts()}||ACK^{trigger}^ACK|ACK{uuid.uuid4().hex[:16]}|P|{version}",
        f"MSA|{code}|{escape(ctrl)}|{escape(text)[:200]}",
    ]
    if code != "AA":
        segs.append(f"ERR|||{'207' if code == 'AE' else '200'}|E||||{escape(text)[:200]}")
    return "\r".join(segs) + "\r"


def adt_a03(*, facility: str, mrn: str, name: str, ward: str, bed: str, attending: str, visit_number: str,
            admitted_at: datetime | None, discharged_at: datetime, control_id: str | None = None) -> str:
    """Discharge notification sent to the HIS when CareFlow closes a discharge."""
    parts = name.split()
    family, given = (parts[-1], " ".join(parts[:-1])) if len(parts) > 1 else (name, "")
    bare_mrn = re.sub(r"^MRN\s+", "", mrn, flags=re.I)
    pv1 = [""] * 46
    pv1[0], pv1[1], pv1[2], pv1[3] = "PV1", "1", "I", f"{escape(ward)}^^{escape(bed)}"
    pv1[7] = f"^{escape(attending)}"
    pv1[19] = escape(visit_number)
    pv1[44] = ts(admitted_at) if admitted_at else ""
    pv1[45] = ts(discharged_at)
    segs = [
        f"MSH|^~\\&|CAREFLOW|{escape(facility)}|HIS|{escape(facility)}|{ts()}||ADT^A03^ADT_A03|{control_id or 'CF' + uuid.uuid4().hex[:18]}|P|2.5",
        f"EVN|A03|{ts()}|||CAREFLOW|{ts(discharged_at)}",
        f"PID|1||{escape(bare_mrn)}^^^{escape(facility)}^MR||{escape(family)}^{escape(given)}",
        "|".join(pv1),
    ]
    return "\r".join(segs) + "\r"


def frame(text: str) -> bytes:
    return SB + text.encode("utf-8") + EB + CR
