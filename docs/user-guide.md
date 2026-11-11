# CareFlow user guide

CareFlow gets a patient from "doctor says they can go" to "they've left" without people chasing each other. Each department works from its own queue; CareFlow's agents watch every department, find what's holding a discharge up, send reminders, and ask a person to approve anything risky.

## First time

1. Open CareFlow. The **setup page** appears once: enter the hospital name and create your administrator account.
2. On **Users**, add an account for everyone who will use it. Pick the role that matches their job:

| Role | What they do in CareFlow |
| --- | --- |
| Administrator | Manages users; can act for any department |
| Discharge coordinator | Admits patients, approves coordination actions, closes discharges, sees reports |
| Clinician | Authorizes discharge (the clinical decision); can complete discharge documents |
| Ward clerk | Admits patients; completes discharge documentation |
| Pharmacy | Prepares discharge medicines and reconciliation |
| Laboratory | Records lab reports and ward acknowledgement |
| Insurance desk lead | Submits to the payer, records queries and approvals, approves insurance actions |
| Billing | Prepares the final bill |
| Auditor | Read-only review; patient names and MRNs are masked |

Give each person their temporary password; they change it under **Password** (bottom of the sidebar).

## Every day

**1. Admit the patient** (ward clerk or coordinator): **Patients → Admit patient**. Enter name, MRN, ward, attending doctor and payment type, and tick "has lab orders" if relevant. CareFlow creates a work item for each department this patient needs:

- every patient: documentation, pharmacy, billing
- cashless insurance or government scheme: insurance too
- lab orders: laboratory too

Departments can start their work straight away.

**2. Authorize discharge** (clinician only): **Patients → Authorize discharge → Confirm**. This is the doctor's clinical decision; CareFlow never makes it. From this moment CareFlow starts clearing the patient.

**3. Departments work their queue**: each person opens **My work queue**. Blocked and overdue patients are at the top. Each card shows what's blocking, any **CareFlow reminder** for you, and buttons for your work:

| Department | Buttons |
| --- | --- |
| Documentation | Tick the completed fields, Save |
| Pharmacy | Assign pharmacist, Medicines ready, Reconciliation complete |
| Laboratory | Add report, Mark final, Ward acknowledged |
| Insurance | Submit to payer, Payer asked for this, We have this document, Payer approved, Payer denied, Convert to self-pay |
| Billing | Start final bill, Final bill ready |

Every click is re-checked by CareFlow straight away.

**4. Approvals** (coordinator or insurance desk lead): when CareFlow wants to do something with risk, such as escalating a late pharmacy task or attaching a document to an insurance claim, it waits on **Approvals**. Read the evidence, then approve, reject (with a reason) or ask for more information. Low-risk reminders run without approval.

**5. Watch the board** (coordinator): **Discharge board** shows every authorized patient, stuck ones first, with what's holding each one up.

**6. Close the discharge**: when every department is cleared the patient shows **Ready to discharge**. When they've actually left, open them and click **Patient has left: close discharge**.

## Reports

- **Clearance report** (one patient): open the patient's workflow, then **Clearance report**, then **Print or save as PDF**. It shows when each department cleared, what held it up, every action and who approved it, and the full decision trail. You can also reach it from **Patients → Discharged → Report**.
- **Discharge export** (all patients): **Operations → Export discharges**. Choose dates, then **Download CSV**. It opens in Excel or Google Sheets with one row per discharge.

## Things CareFlow will flag

| You'll see | What it means |
| --- | --- |
| Pharmacy delay | Medicines not ready 60+ minutes after authorization |
| Incomplete document | Discharge summary is missing required fields |
| Not submitted / Payer delay | Final authorization not sent, or the payer is taking 90+ minutes |
| Payer query | The payer asked for a document. If you have it, CareFlow proposes attaching it |
| Payer denied | Needs a person; record the decision or convert to self-pay |
| Waiting on another department | Billing for insured patients waits on insurance |
| Unacknowledged lab report | A final report the ward hasn't acknowledged |
| Action not applied | CareFlow did something but the change didn't show up; check manually |

Time limits are set on the server (`CAREFLOW_SLA_PHARMACY=60` and similar, in minutes).

## Admin commands

Run these on the server, in the `backend` folder:

```bash
python -m app.cli create-admin --hospital "City Hospital" --name "Asha Rao" --email asha@city.in   # if you can't use the setup page
python -m app.cli reset-password --email someone@city.in
python -m app.cli load-examples    # adds 2 test patients ("Test Patient One/Two") to practise on
```
