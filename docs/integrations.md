# Connecting hospital systems to CareFlow

CareFlow can run on its own: staff work their queues in the browser. This guide is for the hospital IT team that
wants CareFlow to take updates from the systems the hospital already runs, so staff don't enter things twice.

Everything described here is built into CareFlow and uses only free, open-source software. No paid interface engine
or cloud service is needed.

| System | How it talks to CareFlow | What CareFlow does with it |
| --- | --- | --- |
| Hospital information system (HIS / ADT) | HL7 v2 over MLLP (port 2575) or HTTPS, or JSON | Admits, transfers and updates patients; fills the bed board; HIS discharge releases the bed |
| Laboratory (LIS) | HL7 v2 ORM/ORU, or JSON | Lab orders become pending reports; final results and ward acknowledgement clear the lab task |
| Pharmacy | HL7 v2 RDE/RDS/ORM, or JSON | Starts the discharge-medicine clock; dispensing and reconciliation clear the pharmacy task |
| TPA / payer gateway | JSON | Submission, payer queries, approval and denial update the insurance task |
| Any of the above (outbound) | Signed JSON webhooks; HL7 ADT^A03 to the HIS | CareFlow tells the system when staff or CareFlow's agents do something it should know about |

Every inbound update goes through the same path as a staff click: it is saved with history, CareFlow's agents
re-check that department, and the message is logged on the **Integrations** page (with the raw message) for
troubleshooting.

---

## 1. Before you start

1. Sign in as an **Administrator** and open **Integrations**.
2. Under **Integration keys**, create one key per connected system (choose the system: Hospital system, TPA,
   Pharmacy, Laboratory, or *Any* for an interface engine that sends everything). Copy the key when it's shown;
   CareFlow stores only a hash.
3. Use the **HL7 test console** on the same page to send sample messages and see CareFlow's ACK before connecting a
   real system. Use test MRNs.

Keys are sent in the `X-CareFlow-Key` header. A key only works for its own system, and can be revoked at any time.

---

## 2. HL7 v2

### Transport

- **MLLP (TCP), port 2575**: the `hl7` container. Point your HIS or interface engine at `careflow-server:2575`.
  Restrict who can connect with `CAREFLOW_HL7_ALLOWED_HOSTS=["10.0.4.20"]` *and* your firewall. MLLP has no
  authentication of its own, so never expose port 2575 to the internet.
- **HTTPS**: `POST /api/integrations/hl7` with the message as the body (segments separated by CR or newlines) and the
  `X-CareFlow-Key` header. The response body is the HL7 ACK; the `X-HL7-Ack-Code` header repeats MSA-1.

If the hospital has several CareFlow tenants, set `CAREFLOW_HL7_TENANT` to the hospital's slug for the MLLP listener.

### Acknowledgements

| MSA-1 | Meaning | What to do |
| --- | --- | --- |
| `AA` | Accepted (also returned for a duplicate MSH-10, which is not processed again) | Nothing |
| `AE` | Understood but couldn't be applied (unknown MRN, lab report not final yet, ...) | Fix the data and resend with a new MSH-10 |
| `AR` | Rejected: unparseable, or a message type CareFlow doesn't handle | Check the message; don't retry unchanged |

Duplicate detection uses MSH-10 (message control ID), so interface engine retries are safe.

### Messages handled

| Message | Effect in CareFlow |
| --- | --- |
| `ADT^A01` admit, `ADT^A04` register | Creates the patient (or updates them if the MRN exists; re-admits a discharged MRN with fresh department work). Occupies the bed. |
| `ADT^A08` update | Updates name, ward, bed, attending, payer. Creates the patient if unknown. |
| `ADT^A02` transfer | Moves the patient; the old bed goes to *cleaning*, the new one is *occupied*. |
| `ADT^A03` discharge | Releases the bed to *cleaning*. If the CareFlow discharge is already cleared, it is closed. If not, the message is accepted with a **warning** and the workflow is left open for staff (the HIS and CareFlow disagree). |
| `ORM^O01`, `OML^O21` with `OBR` | Each OBR becomes a lab report (id = OBR-3 filler number, else OBR-2). Radiology OBRs (OBR-24 = RAD, CT, US, MR, XR) are ignored. ORC-1 = CA/DC cancels a pending report. |
| `ORU^R01` | OBR-25 = F or C marks the report final (a corrected result needs a fresh ward acknowledgement). Result values (OBX) are **never read**. |
| `ORM^O01`, `RDE^O11` with `RXO`/`RXE` | Discharge medicines ordered: sets the order time and starts the pharmacy clock. **Only counts if** it arrives after discharge authorization, or ORC-29 = `O` (discharge/outpatient order). Inpatient orders during the stay are logged and ignored. |
| `RDS^O13` (or ORC-5 = CM) | Discharge medicines dispensed: pharmacy *Medicines ready*. |

### Fields CareFlow reads

| Field | Used as |
| --- | --- |
| MSH-3, MSH-9, MSH-10 | Sender (shown in history), message type, duplicate detection |
| PID-3 | MRN: the repetition with identifier type `MR`, else the first |
| PID-5 | Name (family^given^middle) |
| PV1-3 | Ward ^ room ^ bed (bed falls back to room) |
| PV1-7 | Attending doctor (id^family^given^...^prefix) |
| PV1-19 | Visit/encounter number |
| PV1-20 | Financial class (`SELF`/`CASH` = self-pay) |
| PV1-44, PV1-45 | Admit and discharge time (else EVN-2, else MSH-7). Times without an offset are treated as UTC. |
| IN1-4, IN1-15 | Insurer name and plan type. `GOV`, `CGHS`, `PMJAY`, `PM-JAY`, `AYUSHMAN`, `ESIC`, `ECHS` = government scheme; any other insurer = cashless; no IN1 = self-pay |
| ORC-1, ORC-2/3, ORC-5, ORC-9, ORC-29 | Order control, order id, status, time, order type |
| OBR-2, OBR-3, OBR-22, OBR-24, OBR-25 | Report id, result time, service section, result status |

### Example

```text
MSH|^~\&|HIS|CITY|CAREFLOW|CITY|20261106083000||ADT^A01^ADT_A01|MSG0001|P|2.5
EVN|A01|20261106083000
PID|1||70001^^^CITY^MR||Kumar^Arjun||19800101|M
PV1|1|I|Ward 3^12^B||||D123^Iyer^Kavya^^^Dr.|||MED|||||||||V5001
IN1|1|STAR01|STAR|Star Health|||||||||||CASHLESS
```

Send a file from the command line (inside any backend container, or locally with the backend's Python):

```bash
docker exec -i careflow-hl7-1 python -m app.integrations.mllp send /dev/stdin localhost 2575 < admit.hl7
```

### Interface engines (free options)

CareFlow doesn't need an interface engine: the HIS can connect directly. If the hospital already routes HL7 through
an engine, add a destination channel of type **MLLP / TCP sender** to `careflow-server:2575`, or **HTTP sender** to
`https://careflow.your-hospital.in/api/integrations/hl7` with the `X-CareFlow-Key` header. Free, open-source engines
that work include the community releases of Mirth Connect (4.5.x and earlier) and its open-source forks.

---

## 3. JSON connectors (TPA, pharmacy, LIS, HIS)

For systems that can call a web service but don't speak HL7.

```http
POST /api/integrations/{system}/events        system = tpa | pharmacy | lis | his
X-CareFlow-Key: cfk_...
Content-Type: application/json

{"event": "approved", "eventId": "tpa-2026-000123", "mrn": "70001", "data": {}}
```

- `mrn` (or `patientRef`) identifies the patient. Both `70001` and `MRN 70001` work.
- `eventId` is optional but recommended: a repeated `eventId` is answered `{"status": "duplicate"}` and not applied again.
- Success: `200 {"status": "accepted" | "duplicate" | "warning", "detail": "...", "patientRef": "pt-..."}`.
- Failure: `422 {"error": {"message": "..."}}` (unknown event, missing field, patient not found, report not final, ...).
- `GET /api/integrations/catalog` lists every event.

| System | Event | `data` | Same as staff clicking |
| --- | --- | --- | --- |
| `tpa` | `preauth_submitted` | | Submit to payer |
| `tpa` | `query_raised` | `document` | Payer asked for this |
| `tpa` | `document_available` | `document` | We have this document |
| `tpa` | `approved` | | Payer approved |
| `tpa` | `denied` | `reason` | Payer denied |
| `pharmacy` | `order_placed` | `orderedAt` (ISO 8601, optional) | Starts the discharge-medicine clock |
| `pharmacy` | `pharmacist_assigned` | `pharmacist` | Assign pharmacist |
| `pharmacy` | `dispensed` | | Medicines ready |
| `pharmacy` | `reconciled` | | Reconciliation complete |
| `lis` | `report_registered` | `reportId` | Add report (pending) |
| `lis` | `report_final` | `reportId` | Mark final (adds it if new) |
| `lis` | `report_acknowledged` | `reportId` | Ward acknowledged (report must be final) |
| `lis` | `order_cancelled` | `reportId` | Removes a pending report |
| `his` | `admitted` | `name`, `ward`, `bed`, `attending`, `payer`, `payerType`, `admittedAt` | Admit / update |
| `his` | `transferred` | `ward`, `bed` | Moves the patient and the bed |
| `his` | `discharged` | | Same as ADT^A03 |
| `his` | `bed_status` | `ward`, `bed`, `status` (`available`, `cleaning`, `blocked`) | Housekeeping update |

Example (curl):

```bash
curl -X POST https://careflow.your-hospital.in/api/integrations/lis/events \
  -H "X-CareFlow-Key: $LIS_KEY" -H "Content-Type: application/json" \
  -d '{"event":"report_final","eventId":"lis-88121","mrn":"70001","data":{"reportId":"LAB-CBC"}}'
```

---

## 4. Outbound: CareFlow tells your systems

Set one URL per system (all optional) and a shared secret:

```dotenv
CAREFLOW_HIS_WEBHOOK_URL=https://his.local/careflow-hook
CAREFLOW_TPA_WEBHOOK_URL=https://tpa-gateway.local/careflow
CAREFLOW_PHARMACY_WEBHOOK_URL=
CAREFLOW_LIS_WEBHOOK_URL=
CAREFLOW_OUTBOUND_SECRET=<random 32+ characters, shared with the receivers>
CAREFLOW_HL7_OUTBOUND_HOST=his-mllp.local      # optional: ADT^A03 over MLLP on discharge
CAREFLOW_HL7_OUTBOUND_PORT=2575
```

Each webhook is a `POST` with this body:

```json
{"id": "cfo-1a2b...", "event": "patient.discharged", "occurredAt": "2026-11-06T15:00:00+00:00",
 "data": {"patientRef": "pt-...", "mrn": "MRN 70001", "name": "...", "ward": "...", "bed": "...", "workflowId": "WF-2041", ...}}
```

Headers: `X-CareFlow-Event`, `X-CareFlow-Id`, and `X-CareFlow-Signature: sha256=<HMAC-SHA256 of the raw body with
CAREFLOW_OUTBOUND_SECRET>`. Reply with any 2xx. CareFlow retries 3 times on timeouts, 5xx, 408 and 429, and logs every
attempt on the Integrations page. Delivery never slows down the person using CareFlow.

| Event | Sent to | When |
| --- | --- | --- |
| `preauth.final_submitted`, `claim.document_available`, `claim.withdrawn_self_pay` | TPA | Insurance staff action in CareFlow |
| `claim.document_attached`, `claim.resubmitted` | TPA | An approved CareFlow action ran |
| `discharge_medicines.ready`, `reconciliation.completed` | Pharmacy | Pharmacy staff action |
| `task.escalated` | Pharmacy | An approved escalation to the duty pharmacist ran |
| `report.acknowledged` | LIS | Ward acknowledged a report in CareFlow |
| `report.acknowledgement_requested` | LIS | CareFlow asked the lab desk for acknowledgement |
| `final_bill.ready` | HIS | Billing marked the final bill ready |
| `patient.discharged` (+ HL7 `ADT^A03`) | HIS | The discharge was closed in CareFlow |
| `bed.status_changed` | HIS | A bed went to cleaning, was marked clean, or taken out of service |

Verifying the signature (Python):

```python
import hmac, hashlib

def verified(raw_body: bytes, header: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")
```

---

## 5. Beds

The **Beds** page shows every bed by ward: *available*, *occupied*, *cleaning*, *out of service*.

- A bed is **occupied** when a patient is admitted into it (by staff, HL7, or JSON) and released when they transfer.
- When a discharge is **closed** in CareFlow (or the HIS sends ADT^A03), the bed goes to **cleaning** and the HIS is
  notified (`bed.status_changed`).
- Housekeeping (or a coordinator / ward clerk) clicks **Mark clean** to make it **available** again, or the HIS
  sends `bed_status`.
- Add beds that have never been used from the Beds page. After upgrading an existing installation, run
  `python -m app.cli sync-beds` once to fill the board from patients already in hospital.

---

## 6. Troubleshooting

| Symptom | Check |
| --- | --- |
| `AE Unknown patient MRN` | Send ADT^A01 before orders/results, or check PID-3 matches the MRN in CareFlow |
| `401 Integration key is invalid or revoked` | Header name is `X-CareFlow-Key`; the key may have been revoked |
| `403 This key is for the lis system` | Each key works for one system; create a key for this one (or an *Any* key) |
| Pharmacy order shows "not tracked" | Inpatient order before discharge authorization. Mark discharge orders with ORC-29 = `O`, or use the JSON `order_placed` event |
| Updates arrive but the board doesn't change | The workflow is waiting for an approval; updates are held and replay after the decision |
| Outbound shows `failed` | The receiver's URL, TLS certificate or firewall; the log shows the HTTP status or error |
| MLLP connection refused | Is the `hl7` container running (`docker compose ps`)? Is the sender's address in `CAREFLOW_HL7_ALLOWED_HOSTS`? |
