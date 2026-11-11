# CareFlow manual test plan: full discharge process

Use this plan to test a discharge from admission to "patient has left" in the web app at http://localhost:3000.

Before writing this plan, every stage was run once through the API against the running stack, with the LLM agents
on Groq. The cases marked ✅ in the **Checked** column passed in that run. Cases marked ◻ follow the code and the
user guide but were not run. The wording CareFlow's agents write for a blocker comes from Groq, so it changes
from run to run. Check the meaning, not the exact text.

---

## 0. Before you start

### 0.1 Check the stack is up

| Check | How | Expected |
| --- | --- | --- |
| API healthy | Open http://localhost:8000/api/health | `{"database":"ok","redis":"ok","llm":"groq"}` |
| Web app up | Open http://localhost:3000 | Login page |

### 0.2 Test accounts

One account exists for every role, and each one has been checked to sign in. These are test logins only:
deactivate them on **Users** when testing is finished.

| Role | Email | Password |
| --- | --- | --- |
| Administrator | `temp.admin@careflow.local` | `Tmp-lBlPgztUj-NNzzJs` |
| Clinician | `temp.clinician@careflow.example.com` | `Tmp-vVAhnvmsVsD1VLGF` |
| Discharge coordinator | `coordinator@careflow.example.com` | `Test-AwumyE_3aHYW` |
| Insurance desk lead | `insurance@careflow.example.com` | `Test-nZaZvamP2bgN` |
| Pharmacy | `pharmacy@careflow.example.com` | `Test-g_n54h0ti9eZ` |
| Laboratory | `lab@careflow.example.com` | `Test-UdqkOjj79Uci` |
| Billing | `billing@careflow.example.com` | `Test-dnMUCyIhgmtt` |
| Ward clerk | `clerk@careflow.example.com` | `Test-c14QmM4JpKtC` |
| Auditor | `auditor@careflow.example.com` | `Test-p8RQ4ppY-WHW` |
| Hospital director (dean) | `director@careflow.example.com` | `Dean-DfpfQLnSFABvPA7` |

If you add more users: passwords need 12+ characters mixing letters with digits or symbols, and the email domain
must look real. `@careflow.example.com` works; `.local` and `.test`
are rejected.

### 0.2b Test patients to admit

Admit these on **Patients → Admit patient** (as the administrator, coordinator or ward clerk). All are fictional.

| # | Name | MRN | Ward | Bed | Payment type | Payer / insurer | Attending | Lab orders | Used in |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| P1 | Test Cashless | TC-001 | 3A | 5 | Cashless insurance (TPA) | Example TPA | Dr. Test | ✔ | Main test, TC-01 to TC-24 |
| P2 | Test SelfPay | TC-002 | 2B | 8 | Self-pay | Self-pay | Dr. Test | ✘ | TC-25, TC-26 |
| P3 | Test Denied | TC-003 | 4C | 2 | Cashless insurance (TPA) | Example Health | Dr. Test | ✘ | Denial, TC-30, TC-31 |
| P4 | Test Government | TC-004 | 1A | 11 | Government scheme (CGHS, PM-JAY, ...) | PM-JAY | Dr. Test | ✔ | Approval variations, TC-27 to TC-29 |
| P5 | Test Pharmacy Delay | TC-005 | 5D | 3 | Self-pay | Self-pay | Dr. Test | ✘ | Pharmacy escalation, TC-32 to TC-34 |

Expected departments after admission: P1 and P4 get all five. P2 and P5 get Documentation, Pharmacy and Billing.
P3 gets Documentation, Pharmacy, Insurance and Billing.

> **Tip:** use one browser window per role (for example a normal window plus incognito windows) so you don't have to
> sign out every time you switch roles.

### 0.3 Who can do what

| Action | Roles that can do it |
| --- | --- |
| Admit patient | Administrator, Coordinator, Ward clerk |
| **Authorize discharge** | **Clinician only** (not even the Administrator) |
| Documentation queue | Administrator, Clinician, Ward clerk |
| Pharmacy / Lab / Billing queue | Administrator, or that department's role |
| Insurance queue | Administrator, Insurance desk lead |
| Approve medium-risk actions (pharmacy escalation) | Discharge coordinator, Administrator |
| Approve high-risk insurance actions | Insurance desk lead, Administrator |
| Close discharge | Administrator, Coordinator |

---

## 1. Main test: complete discharge of a cashless patient with lab orders

This patient touches all five departments and includes one approval. Run the steps in order.

| # | Who | Steps | Expected result | Checked |
| --- | --- | --- | --- | --- |
| **TC-01** Admit | Admin (or Clerk) | **Patients → Admit patient**. Name `Test Cashless`, MRN `TC-001`, Ward `3A`, Bed `5`, Payer `Example TPA`, payment type **Cashless insurance**, attending `Dr. Test`, tick **has lab orders**. Save. | The patient appears under **In hospital** with departments *Documentation, Pharmacy, Laboratory, Insurance, Billing*, and the line "Waiting for a clinician to authorize discharge". | ✅ |
| **TC-02** Admin can't authorize | Admin | Look at the patient row. | No **Authorize discharge** button, only the "Waiting for a clinician…" text. | ✅ |
| **TC-03** Authorize | Clinician | **Patients → Authorize discharge → Confirm**. | Takes about 5 seconds (the Groq agents run). The patient appears on the **Discharge board** as **Blocked**. Blockers: documentation incomplete, insurance not submitted, billing waiting on insurance. Lab is **not** a blocker yet because it has no reports. | ✅ |
| **TC-04** Automatic reminders | Clerk (or Admin) | Open **My work queue → Documentation**. | The patient's card shows a **Request from CareFlow**, a low-risk document task. It ran without anyone approving it. | ✅ |
| **TC-05** No double authorization | Clinician | Go back to **Patients**. | No authorize button for this patient. (The API returns *409: Discharge is already authorized (WF-…)*.) | ✅ |
| **TC-06** Mark the document as available first | Insurance (or Admin) | **My work queue → Insurance → We have this document**, type `discharge summary`. | The record now lists the discharge summary as available. | ✅ |
| **TC-07** Submit to payer | Insurance | **Submit to payer**. | The "not submitted" blocker disappears. Insurance is now waiting on the payer. | ✅ |
| **TC-08** Payer query raises an approval | Insurance | **Payer asked for this**, type `discharge summary`. | Board status becomes **Awaiting approval**. **Approvals** shows a **high-risk** action *attach_document_to_claim* with approver role **Insurance desk lead**, plus evidence and SOP citations. | ✅ |
| **TC-09** Wrong role can't approve | Pharmacy or Clinician | Try to open **Approvals** or decide the action. | Not allowed: the page or button is missing, or you get the error *"can't do this (approval:decide)"*. | ✅ (API) |
| **TC-10** Reject needs a comment | Insurance | Click **Reject** without typing a comment. | Error: *"Add a comment explaining the rejection or what information is needed."* Nothing changes. | ✅ |
| **TC-11** Approve | Insurance (or Admin) | **Approve**. | The action status goes to **verified**: CareFlow did it and then re-read the insurance record to confirm. The audit trail shows who approved it. | ✅ |
| **TC-12** Can't close early | Admin or Coordinator | Open the workflow and try **Patient has left: close discharge**. | Blocked with *"Every department must be cleared before the patient leaves."* | ✅ |
| **TC-13** Payer approves | Insurance | **Payer approved**. | Insurance shows **cleared**. The billing "waiting on insurance" blocker goes away. | ✅ |
| **TC-14** Partial documentation | Clerk or Clinician | **Documentation**: tick about 6 of the 12 fields, **Save**. | The documentation blocker stays and now lists only the **remaining** fields. | ✅ |
| **TC-15** Complete documentation | Clerk or Clinician | Tick all 12 fields, **Save**. | Documentation shows **cleared**. | ✅ |
| **TC-16** Pharmacy without reconciliation | Pharmacy | **Assign pharmacist** (`Test Pharmacist`), then **Medicines ready**. | A new blocker: *Medication reconciliation form not completed*. | ✅ |
| **TC-17** Pharmacy complete | Pharmacy | **Reconciliation complete**. | Pharmacy shows **cleared**. | ✅ |
| **TC-18** Lab report not acknowledged | Lab | **Add report**, ID `LAB-1`, mark it **final**. | A new blocker: 1 final lab report not acknowledged by the ward. | ✅ |
| **TC-19** Acknowledge a report that isn't final | Lab | **Add report** `LAB-2` (not final), then **Ward acknowledged** on LAB-2. | Error: *"Report LAB-2 isn't final yet"*. Click **Mark final** on LAB-2 before going on. | ◻ |
| **TC-20** Lab complete | Lab | **Ward acknowledged** on every final report. | Laboratory shows **cleared**. | ✅ |
| **TC-21** Billing | Billing | **Start final bill**, then **Final bill ready**. | Billing shows **cleared**. Board status becomes **Ready to discharge** (`cleared`). | ✅ |
| **TC-22** Close | Admin or Coordinator | **Patient has left: close discharge**. | Status **Discharged**. The patient moves to **Patients → Discharged**. | ✅ |
| **TC-23** Clearance report | Admin or Coordinator | Open the workflow → **Clearance report** (or **Patients → Discharged → Report**). | Shows when each department cleared, every blocker, every action with who approved it, and the full timeline. **Print or save as PDF** works. | ✅ (API) |
| **TC-24** Can't re-authorize a discharged patient | Clinician | Try to authorize this patient again. | Not possible. The API returns *409: This patient has already been discharged.* | ✅ |

> ⚠️ **The order of TC-06 to TC-08 matters.** CareFlow proposes the "attach document" approval only if the document
> is already available **when the payer query is recorded**. If you record the query first, CareFlow sends a
> low-risk reminder instead. That reminder finishes as *verified*, and CareFlow doesn't propose a second action for
> the same blocker, so the approval never appears. In that case, finish insurance with **Payer approved**.

---

## 2. Self-pay patient (simplest path)

| # | Who | Steps | Expected result | Checked |
| --- | --- | --- | --- | --- |
| **TC-25** Admit self-pay | Admin | Admit `Test SelfPay`, payment type **Self-pay**, no lab orders. | Departments: *Documentation, Pharmacy, Billing* only (no Insurance, no Lab). | ◻ |
| **TC-26** Authorize and clear | Clinician, then each department | Authorize. Complete documentation, pharmacy (medicines ready and reconciliation), and billing. | Billing is **not** blocked waiting on insurance. Status goes to **Ready to discharge** and the patient can be closed. | ◻ |

---

## 3. Approval variations

| # | Who | Steps | Expected result | Checked |
| --- | --- | --- | --- | --- |
| **TC-27** Request more information | Insurance or Coordinator | On a pending approval, click **Request information** with a comment. | The original action shows **info_requested**. A new pending action is proposed for the same blocker (re-proposed with SOP context). | ✅ |
| **TC-28** Reject | Approver | Reject a pending action with a comment. | The action shows **rejected** and is not executed. The blocker stays for staff to handle. The rejection and comment appear in the **Audit trail**. | ◻ |
| **TC-29** Updates held during an approval | Departments | While an action is **pending**, have other departments complete their work (for example billing **Final bill ready**). | The board does **not** update yet: the workflow stays *Awaiting approval* and shows the old blockers. When the last pending approval is decided, the held updates replay and the board catches up (for example straight to **Ready to discharge**). This is by design. | ✅ |

---

## 4. Insurance denial

| # | Who | Steps | Expected result | Checked |
| --- | --- | --- | --- | --- |
| **TC-30** Payer denied | Insurance | On a cashless patient: **Submit to payer**, then **Payer denied** with reason `Policy lapsed`. | A blocker: *Payer denied final authorization (Policy lapsed); the payment route must change*. CareFlow does **not** propose an automatic fix. A person has to decide. | ◻ |
| **TC-31** Convert to self-pay | Insurance | **Convert to self-pay**. | Insurance shows **cleared** (marked converted). Billing is no longer waiting on insurance. | ◻ |

---

## 5. Pharmacy delay and escalation (advanced, uses the integration API)

The pharmacy "late" blocker starts 60 minutes after the medicine order time. CareFlow sets that time when discharge
is authorized (a pharmacy system can also send it). So the delay appears on its own if pharmacy isn't done an hour
after authorization. To test the escalation approval without waiting an hour, send an event that says the order
was placed 2 hours ago.

1. Authorize a patient and leave pharmacy unfinished.
2. Find the patient's `ref` (`pt-…`). It's in the address bar when you open the patient or workflow, or in the
   response of `GET /api/patients`.
3. In PowerShell:

```powershell
$login = Invoke-RestMethod -Method Post http://localhost:8000/api/auth/login -ContentType application/json `
  -Body '{"email":"temp.admin@careflow.local","password":"Tmp-lBlPgztUj-NNzzJs"}'
$ordered = (Get-Date).ToUniversalTime().AddHours(-2).ToString("o")
$body = @{
  type = "PHARMACY_TASK_UPDATED"; patientRef = "pt-REPLACE_ME"
  payload = @{ department = "pharmacy"; record = @{ status = "IN_PROGRESS"; ordered_at = $ordered } }
} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post http://localhost:8000/api/events -ContentType application/json `
  -Headers @{ Authorization = "Bearer $($login.accessToken)" } -Body $body
```

| # | Steps | Expected result | Checked |
| --- | --- | --- | --- |
| **TC-32** Event goes through Redis | Run the command. | The response is `queued: true` with a `streamId` (the event went through Redis Streams to the worker). | ✅ |
| **TC-33** Pharmacy delay plus escalation | Wait a few seconds and refresh the board. | A blocker: *Discharge medicines not prepared 2h after the order was placed*. **Approvals** shows a **medium-risk** *escalate_pharmacy_task* for **Discharge coordinator**. | ✅ |
| **TC-34** Coordinator approves | Sign in as the coordinator and approve it. | The action is **verified**. The pharmacy queue shows *Escalated to duty pharmacist* under requests from CareFlow. | ◻ |

---

## 6. Security and roles

| # | Who | Steps | Expected result | Checked |
| --- | --- | --- | --- | --- |
| **TC-35** Department isolation | Pharmacy | Try to work on Billing or Insurance in the queue. | Only the Pharmacy queue is available. The API says *"Your role (Pharmacy) can't update billing."* | ◻ |
| **TC-36** Clinician can't admit | Clinician | Look for **Admit patient**. | Not available (the API returns 403). | ✅ |
| **TC-37** Auditor sees masked identity | Auditor | Open the board, a workflow, and the audit trail. | Everything is readable, but patient **names and MRNs are masked**. No action buttons. | ◻ |
| **TC-38** Only admins manage users | Coordinator | Look for **Users**. | Not shown, or access denied. | ◻ |
| **TC-39** Change password | Any test user | **Password** (bottom of the sidebar) → change it → sign out → sign in with the new password. | The new password works and the old one is rejected. | ◻ |

---

## 7. Reports and monitoring

| # | Who | Steps | Expected result | Checked |
| --- | --- | --- | --- | --- |
| **TC-40** Audit trail | Admin | **Audit trail** | Every authorization, agent decision, policy check, approval and staff action, with who did it and when. | ◻ |
| **TC-41** Agents ran on Groq | Admin | **Operations** → agent activity | Workflow Planner, Blocker Analyzer, Resolution Planner and Reporter runs show mode **groq**, with latency and tokens. **fallback** would mean Groq failed. | ◻ |
| **TC-42** CSV export | Admin or Coordinator | **Operations → Export discharges**, pick today's date → **Download CSV**. | One row per discharged patient from your tests. | ◻ |

---

## 8. Optional: time-based delays

Payer delay (90 min) and billing delay (45 min) start from timestamps the buttons do set, so you can test them by
waiting. To test them faster, shorten the limits. Add these lines to `.env`:

```dotenv
CAREFLOW_SLA_INSURANCE=1
CAREFLOW_SLA_BILLING=1
```

The compose file doesn't pass these through automatically. Add them under `x-backend-env` in `docker-compose.yml`
(for example `CAREFLOW_SLA_INSURANCE: ${CAREFLOW_SLA_INSURANCE:-90}`), then run
`docker compose up -d --force-recreate --no-deps api worker`. **Undo this after testing.**

| # | Steps | Expected result |
| --- | --- | --- |
| **TC-43** Payer delay | **Submit to payer**, wait 2 minutes, then **Recheck** the workflow. | A *Payer has not sent final authorization …* blocker. |
| **TC-44** Billing delay | **Start final bill**, wait 2 minutes, then **Recheck**. | A *Final bill still in preparation …* blocker. |

---

## 9. Director dashboard (dean / hospital director)

Sign in as `director@careflow.example.com`.

| # | Steps | Expected result | Checked |
| --- | --- | --- | --- |
| **TC-45** Dashboard opens | The sidebar starts with **Director dashboard**. Open it. | Census (in hospital, in clearance, ready to leave, discharged today), clearance times, longest waits, decisions waiting, departments table with the current bottleneck, 14-day chart, beds by ward, connected systems, patients/agents/staff. Switch between 7 / 30 / 90 days. | ✅ (API) |
| **TC-46** Sees every department, read-only | **Department queues**: switch between the five departments. | Every queue is visible with real patient names. Each card says *View only*; there are no action buttons. | ✅ (API) |
| **TC-47** Decides any approval | Create a pending approval (TC-06 to TC-08 on a new cashless patient), then approve it as the director. | Allowed, even though the approver role is *Insurance desk lead*. The coordinator gets *Only the Insurance desk lead role can decide this*. | ✅ |
| **TC-48** Closes a discharge | On a **Ready to discharge** workflow, **Patient has left: close discharge**. | Discharged; the bed goes to *cleaning*. | ✅ |
| **TC-49** Limits | Look for **Users**; try **Authorize discharge**. | No Users page; no authorize button (clinical decision stays with clinicians). | ✅ (API) |
| **TC-50** Others can't open it | Sign in as the coordinator and open `/director`. | The menu item isn't shown; the page says the dashboard isn't part of your role. The API returns 403. | ✅ (API) |

## 10. Beds

| # | Who | Steps | Expected result | Checked |
| --- | --- | --- | --- | --- |
| **TC-51** Admission fills a bed | Admin | Admit a patient with ward `Ward 7`, bed `1`. Open **Beds**. | *Ward 7 / 1* is **Occupied** with the patient's name. | ✅ |
| **TC-52** Transfer | Admin | Edit the patient's bed to `2` (Patients page, or HL7 ADT^A02). | Bed 1 → **Cleaning**, bed 2 → **Occupied**. | ✅ |
| **TC-53** Can't free an occupied bed | Coordinator | Try to mark the occupied bed available (API) | Refused: *occupied by an admitted patient; transfer or discharge them first*. | ✅ |
| **TC-54** Discharge releases the bed | Coordinator | Close the patient's discharge. | Bed → **Cleaning**; the close response lists the released bed. | ✅ |
| **TC-55** Housekeeping | Ward clerk | **Beds → Mark clean**. | Bed → **Available**. | ✅ |
| **TC-56** Add beds | Admin | **Add beds**: ward `Ward 8`, beds `1, 2, 3`. | Three available beds in Ward 8. Directors and auditors can view but not add. | ✅ |

## 11. Integrations (HL7 v2 and JSON connectors)

Sign in as the administrator and open **Integrations**. Full reference: [integrations.md](integrations.md).

| # | Steps | Expected result | Checked |
| --- | --- | --- | --- |
| **TC-57** HL7 admit | **HL7 test console**: test MRN `HL7-1001`, click **ADT^A01 admit (cashless)**, **Send**. | **Accepted (AA)**. Patient *Neha Sharma*, MRN `MRN HL7-1001`, cashless, appears on **Patients** (source *hl7*) and in **Ward 5 / 7** on **Beds**. | ✅ (API) |
| **TC-58** Duplicate | Click **Send** again without changing anything. | AA *Duplicate; already processed*; no second patient. The log shows *duplicate*. | ✅ |
| **TC-59** Lab order then result | Send **ORM^O01 lab order**, then **ORU^R01 lab result final**. | Patient gains a **Laboratory** department; report LAB-100 pending, then final (needs ward acknowledgement). | ✅ |
| **TC-60** Unknown patient | Change the MRN in an ORU message to one that doesn't exist and send. | **Error (AE)** *Unknown patient MRN ...*. | ✅ |
| **TC-61** Garbage | Replace the text with `hello` and send. | **Rejected (AR)**. | ✅ |
| **TC-62** Inpatient vs discharge pharmacy order | Before authorization, send **RDE^O11** with ORC-29 removed; then with ORC-29 = `O`. | First: accepted, *not tracked*. Second: discharge medicines ordered. | ✅ first part (API test); ◻ second |
| **TC-63** Transfer and HIS discharge | Send **ADT^A02 transfer**, then authorize the patient and send **ADT^A03 discharge** before clearance. | Transfer moves the bed. A03: accepted with a **warning**; bed → cleaning; workflow left open for staff. | ✅ |
| **TC-64** Integration key | **Integration keys**: name `TPA test`, system **TPA / payer**, **Create key**. Copy it. | Key starting `cfk_` shown once; the list shows only its prefix. | ✅ |
| **TC-65** JSON connector | With a cashless patient authorized, run the PowerShell below with your key. | `status: accepted`; the insurance task moves; the message appears in the log as *in · tpa*. | ✅ (live) |
| **TC-66** Wrong key / revoked key | Use the TPA key on `/integrations/lis/events`; then revoke the key and retry. | 403 *This key is for the tpa system*; after revoking, 401. | ✅ |
| **TC-67** MLLP | From a machine with Python: `docker exec -i careflow-hl7-1 python -m app.integrations.mllp send /dev/stdin localhost 2575 < admit.hl7` | ACK printed with `MSA|AA`. | ✅ (live, MLLP from the host) |

```powershell
$key = "cfk_PASTE_YOUR_KEY"
$body = @{ event = "approved"; eventId = "tpa-test-1"; mrn = "HL7-1001"; data = @{} } | ConvertTo-Json
Invoke-RestMethod -Method Post http://localhost:8000/api/integrations/tpa/events -ContentType application/json `
  -Headers @{ "X-CareFlow-Key" = $key } -Body $body
```

## 11b. Automatic admission from the hospital system (HL7 ADT)

These cases check what happens when the hospital's main system (HIS) admits, updates or discharges a patient and
CareFlow receives the HL7 message, with no one typing the patient in.

**How to send a message:** sign in as the **administrator**, open **Integrations → HL7 test console**, delete the
text in the box, paste the message for the case, and click **Send**. The reply (ACK) appears under the box:
**Accepted (AA)**, **Error (AE)** or **Rejected (AR)**. The *Test MRN* box only fills in the built-in samples; it
doesn't change pasted text.

**Run the cases in order.** Later cases use the patients created by earlier ones. To run them again on the same
database, change the MRNs (`HL7-2001` → `HL7-3001`, …) and the message IDs (`TC71-1` → `TC71-2`, …) everywhere, or
CareFlow will treat the messages as duplicates.

| # | Steps | Expected result | Checked |
| --- | --- | --- | --- |
| **TC-71** Admission creates the patient | Send **message A**. Open **Patients**, search `HL7-2001`. Open **Beds**. | **Accepted (AA)**. Patient **Ravi Kumar**, MRN `MRN HL7-2001`, ward **3A**, bed **12**, doctor **Dr. Kavya Iyer**, payer **Star Health**, payment type **cashless**, source **hl7**. Departments: documentation, pharmacy, insurance, billing. No workflow yet. Bed **3A / 12** is **Occupied** by Ravi Kumar. | ✅ (automated) |
| **TC-72** Government scheme is recognised | Send **message B**. Search `HL7-2002`. | AA. **Lakshmi Devi**, payment type **government** (the payer name contains *PM-JAY* / *Ayushman*). Insurance department included. | ✅ (automated) |
| **TC-73** No insurance means self-pay | Send **message C**. Search `HL7-2003`. | AA. **Arjun Singh**, payer **Self-pay**, payment type **self_pay**. Departments: documentation, pharmacy, billing; **no insurance**. | ✅ (automated) |
| **TC-74** Same patient again updates, no duplicate | Send **message D** (same MRN as A, new message ID, middle name added). Search `HL7-2001`. | AA. Still **one** patient with MRN HL7-2001, now named **Ravi S Kumar**. | ✅ (automated) |
| **TC-75** Update moves the bed | Send **message E** (ADT^A08, new location 4B / 20). Check **Patients** and **Beds**. | AA. Ravi S Kumar is in ward **4B**, bed **20**. Bed **4B / 20** is **Occupied**; the old bed **3A / 12** is **Cleaning**. | ✅ (automated) |
| **TC-76** Message without patient ID is refused | Send **message F** (the patient ID in PID-3 is missing). | **Error (AE)** mentioning **PID-3** (patient identifier missing). No new patient appears. | ✅ (automated) |
| **TC-77** Discharge in the HIS, then readmission | Send **message G** (ADT^A03 for Arjun Singh). Search `HL7-2003` on **Patients** with the discharged list. Then send **message H** (ADT^A01, bed 16). | G: **Accepted (AA)** with a **warning** (discharged in the HIS without a CareFlow authorization); Arjun Singh is **discharged** and bed 3A / 15 goes to Cleaning. H: AA; the **same** patient is **admitted** again in **3A / 16**, with fresh department work. | ✅ (automated) |
| **TC-78** Only the hospital system may send | Run the PowerShell block below: (1) no key, (2) a made-up key, (3) a coordinator's sign-in. Then (4) create a key for **Hospital system (HIS)** on **Integration keys** and send message A with it. | (1) and (2): **401** *Integration key is invalid or revoked* / sign-in required. (3): **403** (only an integration key or an administrator). (4): **200** with **AA** (*Duplicate; already processed*, because message A was already received). No patient `HL7-2009` exists after steps 1–3. | ✅ (automated) |
| **TC-79** Every message is logged | **Integrations → Message log**. | One row per message sent above, with its type and result: TC71-1 *accepted*, TC76-1 *error*, TC77-1 *warning*, and so on. Click a row to see what CareFlow did. | ✅ (automated) |
| **TC-80** The HL7 patient goes through a full discharge | Sign in as the **clinician**, authorize discharge for **Lakshmi Devi** (`HL7-2002`). Clear her departments in the queues as in section 1, then close the discharge as the **coordinator**. | Same behaviour as a patient admitted by hand: workflow created, blockers found, discharge closes, bed **3A / 14** goes to **Cleaning**. | ◻ |

**Message A** (TC-71: cashless admission)

```
MSH|^~\&|HIS|CITYHOSP|CAREFLOW|CITYHOSP|20261107093000||ADT^A01|TC71-1|P|2.5
EVN|A01|20261107093000
PID|1||HL7-2001^^^CITYHOSP^MR||Kumar^Ravi||19800512|M
PV1|1|I|3A^^12||||D123^Iyer^Kavya^^^Dr.||||||||||||V5501|
IN1|1|STAR01|STAR|Star Health
```

**Message B** (TC-72: government scheme)

```
MSH|^~\&|HIS|CITYHOSP|CAREFLOW|CITYHOSP|20261107093000||ADT^A01|TC72-1|P|2.5
EVN|A01|20261107093000
PID|1||HL7-2002^^^CITYHOSP^MR||Devi^Lakshmi||19800512|M
PV1|1|I|3A^^14||||D123^Iyer^Kavya^^^Dr.||||||||||||V5501|
IN1|1|PMJAY01|PMJAY|Ayushman Bharat PM-JAY
```

**Message C** (TC-73: self-pay, no insurance segment)

```
MSH|^~\&|HIS|CITYHOSP|CAREFLOW|CITYHOSP|20261107093000||ADT^A01|TC73-1|P|2.5
EVN|A01|20261107093000
PID|1||HL7-2003^^^CITYHOSP^MR||Singh^Arjun||19800512|M
PV1|1|I|3A^^15||||D123^Iyer^Kavya^^^Dr.||||||||||||V5501|
```

**Message D** (TC-74: same patient, new message ID)

```
MSH|^~\&|HIS|CITYHOSP|CAREFLOW|CITYHOSP|20261107093000||ADT^A01|TC74-1|P|2.5
EVN|A01|20261107093000
PID|1||HL7-2001^^^CITYHOSP^MR||Kumar^Ravi^S||19800512|M
PV1|1|I|3A^^12||||D123^Iyer^Kavya^^^Dr.||||||||||||V5501|
IN1|1|STAR01|STAR|Star Health
```

**Message E** (TC-75: update, moves to 4B / 20)

```
MSH|^~\&|HIS|CITYHOSP|CAREFLOW|CITYHOSP|20261107093000||ADT^A08|TC75-1|P|2.5
EVN|A08|20261107093000
PID|1||HL7-2001^^^CITYHOSP^MR||Kumar^Ravi^S||19800512|M
PV1|1|I|4B^^20||||D123^Iyer^Kavya^^^Dr.||||||||||||V5501|
IN1|1|STAR01|STAR|Star Health
```

**Message F** (TC-76: patient ID missing)

```
MSH|^~\&|HIS|CITYHOSP|CAREFLOW|CITYHOSP|20261107093000||ADT^A01|TC76-1|P|2.5
EVN|A01|20261107093000
PID|1||||Kumar^Ravi||19800512|M
PV1|1|I|3A^^12||||D123^Iyer^Kavya^^^Dr.||||||||||||V5501|
IN1|1|STAR01|STAR|Star Health
```

**Message G** (TC-77: discharged in the HIS)

```
MSH|^~\&|HIS|CITYHOSP|CAREFLOW|CITYHOSP|20261107093000||ADT^A03|TC77-1|P|2.5
EVN|A03|20261107093000
PID|1||HL7-2003^^^CITYHOSP^MR||Singh^Arjun||19800512|M
PV1|1|I|3A^^15||||D123^Iyer^Kavya^^^Dr.||||||||||||V5501|
```

**Message H** (TC-77: readmitted to 3A / 16)

```
MSH|^~\&|HIS|CITYHOSP|CAREFLOW|CITYHOSP|20261107093000||ADT^A01|TC77-2|P|2.5
EVN|A01|20261107093000
PID|1||HL7-2003^^^CITYHOSP^MR||Singh^Arjun||19800512|M
PV1|1|I|3A^^16||||D123^Iyer^Kavya^^^Dr.||||||||||||V5501|
```

**TC-78 commands** (PowerShell). Steps 1–3 must fail; step 4 needs the HIS key from **Integration keys**:

```powershell
$api = "http://localhost:8000/api/integrations/hl7"
$msg = "MSH|^~\&|HIS|CITYHOSP|CAREFLOW|CITYHOSP|20261107093000||ADT^A01|TC78-1|P|2.5`rPID|1||HL7-2009^^^CITYHOSP^MR||Test^Security`rPV1|1|I|3A^^30"

# 1. no key: expect 401
try { Invoke-WebRequest -Method Post $api -Body $msg -ContentType text/plain } catch { $_.Exception.Response.StatusCode.value__ }

# 2. made-up key: expect 401
try { Invoke-WebRequest -Method Post $api -Body $msg -ContentType text/plain -Headers @{ "X-CareFlow-Key" = "cfk_not_a_real_key" } } catch { $_.Exception.Response.StatusCode.value__ }

# 3. a coordinator's sign-in: expect 403
$t = (Invoke-RestMethod -Method Post http://localhost:8000/api/auth/login -ContentType application/json `
  -Body '{"email":"coordinator@careflow.example.com","password":"Test-AwumyE_3aHYW"}').accessToken
try { Invoke-WebRequest -Method Post $api -Body $msg -ContentType text/plain -Headers @{ Authorization = "Bearer $t" } } catch { $_.Exception.Response.StatusCode.value__ }

# 4. the real HIS key with message A: expect 200 and MSA|AA in the reply
$key = "cfk_PASTE_YOUR_HIS_KEY"
$a = "MSH|^~\&|HIS|CITYHOSP|CAREFLOW|CITYHOSP|20261107093000||ADT^A01|TC71-1|P|2.5`rEVN|A01|20261107093000`rPID|1||HL7-2001^^^CITYHOSP^MR||Kumar^Ravi||19800512|M`rPV1|1|I|3A^^12||||D123^Iyer^Kavya^^^Dr.||||||||||||V5501|`rIN1|1|STAR01|STAR|Star Health"
(Invoke-WebRequest -Method Post $api -Body $a -ContentType text/plain -Headers @{ "X-CareFlow-Key" = $key }).Content
```

## 11c. More test patients from the hospital system

Six more patients to admit through the **HL7 test console**, one message each (plus a lab order for Priya Nair).
Paste each message on its own and click **Send**; every one should return **Accepted (AA)**. Then check
**Patients** and **Beds** against the table. These were sent through CareFlow in the automated test environment
before being added here, and gave exactly these results.

| # | Patient | MRN | Ward / bed | Doctor | Payer | Payment type | Departments CareFlow creates |
| --- | --- | --- | --- | --- | --- | --- | --- |
| P1 | Priya Nair | `HL7-2101` | 5B / 3 | Dr. Anil Menon | HDFC ERGO Health | **cashless**, plus a lab order (send **message P1-lab** right after P1) | documentation, pharmacy, **laboratory**, insurance, billing |
| P2 | Mohammed Irfan Khan | `HL7-2102` | 5B / 4 | Dr. Meera Shetty | ESIC | **government** (ESIC) | documentation, pharmacy, insurance, billing |
| P3 | Sunita Reddy | `HL7-2103` | 6A / 1 | Dr. Arun Bose | Self-pay | **self-pay** (no insurance segment) | documentation, pharmacy, billing |
| P4 | Joseph Thomas | `HL7-2104` | 6A / 2 | Dr. Anil Menon | Niva Bupa | **cashless** | documentation, pharmacy, insurance, billing |
| P5 | Kavitha Rao | `HL7-2105` | ICU / 1 | Dr. Ramesh Pillai | CGHS | **government** (CGHS) | documentation, pharmacy, insurance, billing |
| P6 | Rahul Verma | `HL7-2106` | 6A / 3 | Dr. Arun Bose | Star Health | **self-pay**: the insurance segment is there, but the visit is marked `SELF` (PV1-20), so CareFlow treats it as self-pay | documentation, pharmacy, billing |

Use them for any test case that needs a patient: authorize as the **clinician**, clear the departments, close as
the **coordinator**. Good pairs to compare: **P1** (5 departments, with lab) against **P3** (only 3), and **P2** /
**P5** (government) against **P4** (cashless). To add them again on the same database, change the MRN and the
message ID (`PAT-2101` → `PAT-2101b`) in each message.

**Message P1** (Priya Nair)

```
MSH|^~\&|HIS|CITYHOSP|CAREFLOW|CITYHOSP|20261107100000||ADT^A01|PAT-2101|P|2.5
EVN|A01|20261107100000
PID|1||HL7-2101^^^CITYHOSP^MR||Nair^Priya||19910315|F
PV1|1|I|5B^^3||||D201^Menon^Anil^^^Dr.||||||||||||V6100|
IN1|1|HDFC01|HDFC|HDFC ERGO Health
```

**Message P1-lab** (lab order for Priya Nair; send after P1)

```
MSH|^~\&|HIS|CITYHOSP|CAREFLOW|CITYHOSP|20261107101000||ORM^O01|LAB-2101|P|2.5
PID|1||HL7-2101^^^CITYHOSP^MR||Nair^Priya
ORC|NW|PL-2101
OBR|1|PL-2101|LAB-2101|CBC^Complete blood count
```

**Message P2** (Mohammed Irfan Khan)

```
MSH|^~\&|HIS|CITYHOSP|CAREFLOW|CITYHOSP|20261107100000||ADT^A01|PAT-2102|P|2.5
EVN|A01|20261107100000
PID|1||HL7-2102^^^CITYHOSP^MR||Khan^Mohammed^Irfan||19750822|M
PV1|1|I|5B^^4||||D202^Shetty^Meera^^^Dr.||||||||||||V6101|
IN1|1|ESIC01|ESIC|ESIC
```

**Message P3** (Sunita Reddy)

```
MSH|^~\&|HIS|CITYHOSP|CAREFLOW|CITYHOSP|20261107100000||ADT^A01|PAT-2103|P|2.5
EVN|A01|20261107100000
PID|1||HL7-2103^^^CITYHOSP^MR||Reddy^Sunita||19680130|F
PV1|1|I|6A^^1||||D203^Bose^Arun^^^Dr.||||||||||||V6102|
```

**Message P4** (Joseph Thomas)

```
MSH|^~\&|HIS|CITYHOSP|CAREFLOW|CITYHOSP|20261107100000||ADT^A01|PAT-2104|P|2.5
EVN|A01|20261107100000
PID|1||HL7-2104^^^CITYHOSP^MR||Thomas^Joseph||19990704|M
PV1|1|I|6A^^2||||D201^Menon^Anil^^^Dr.||||||||||||V6103|
IN1|1|NIVA01|NIVA|Niva Bupa
```

**Message P5** (Kavitha Rao)

```
MSH|^~\&|HIS|CITYHOSP|CAREFLOW|CITYHOSP|20261107100000||ADT^A01|PAT-2105|P|2.5
EVN|A01|20261107100000
PID|1||HL7-2105^^^CITYHOSP^MR||Rao^Kavitha||19570911|F
PV1|1|I|ICU^^1||||D204^Pillai^Ramesh^^^Dr.||||||||||||V6104|
IN1|1|CGHS01|CGHS|CGHS
```

**Message P6** (Rahul Verma)

```
MSH|^~\&|HIS|CITYHOSP|CAREFLOW|CITYHOSP|20261107100000||ADT^A01|PAT-2106|P|2.5
EVN|A01|20261107100000
PID|1||HL7-2106^^^CITYHOSP^MR||Verma^Rahul||19880219|M
PV1|1|I|6A^^3||||D203^Bose^Arun^^^Dr.||||||||||||V6105|SELF
IN1|1|STAR01|STAR|Star Health
```

## 12. Account security

| # | Steps | Expected result | Checked |
| --- | --- | --- | --- |
| **TC-68** Lockout | Sign in as the billing user with a wrong password 5 times, then the right one. | 6th attempt: *This account is locked ... Try again in 15 minute(s)*. **Users** shows *Locked*. | ✅ |
| **TC-69** Unlock | Admin: **Users → Unlock** on that account. | The billing user can sign in again. | ✅ |
| **TC-70** Password policy | Admin: add a user with password `short-1`, then `alllowercaseletters`. | Both refused with the policy message. `Ward-Clerk-2026` works. | ✅ |

## Recording results

For each case, note: **Pass / Fail**, the workflow ID (`WF-…`), and a screenshot of any failure. If something
fails, look at the API logs:

```powershell
docker logs --tail 100 careflow-api-1
docker logs --tail 100 careflow-worker-1
```
