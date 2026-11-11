# CareFlow

CareFlow helps a hospital get patients home once a doctor has said they can go. After a clinician authorizes
discharge, CareFlow checks every department at the same time (documentation, pharmacy, laboratory, insurance and
billing), shows exactly what is holding each patient up, sends simple reminders by itself, and asks a person to
approve anything riskier. It then re-checks the department's own system to prove each fix really happened.

CareFlow handles **administrative work only**. It never decides whether a patient is medically fit, never changes
medicines, and never approves insurance claims or payments.

!!! warning "Not a certified medical device"
    Read [Compliance notes](security/compliance.md) and [PHI handling](security/phi-handling.md) before CareFlow
    holds real patient data.

## Who these docs are for

| You are | Start here |
| --- | --- |
| Hospital staff (nurses, clerks, pharmacy, lab, insurance, billing, coordinators, doctors, directors) | [User guide](user-guide/discharge-start-to-finish.md): one page per role |
| Hospital IT, installing or running CareFlow | [Getting started](getting-started/installation.md) and [Operations](operations/deployment.md) |
| A developer or vendor connecting a system | [Integrations](integrations/hl7-v2.md) and the [API](api/authentication.md) |
| A developer working on CareFlow | [Architecture](architecture/overview.md) and [Development](development/local-setup.md) |
| A security or compliance reviewer | [Security](security/roles-and-permissions.md) |

## How the docs are organized

| Section | What it contains |
| --- | --- |
| **Getting started** | Installing, configuring, first-run setup and upgrading |
| **User guide** | Step-by-step instructions for each role, and one discharge from start to finish |
| **Concepts** | The ideas CareFlow is built on: workflows, departments, blockers, evidence, approvals, verification, reports |
| **Architecture** | How the system works inside: components, agents, events, data model, frontend, security design |
| **Integrations** | Connecting hospital systems: HL7 v2, FHIR, pharmacy, laboratory and insurance (TPA) connectors, outbound messages |
| **API** | Every endpoint, with permissions, parameters, examples and errors |
| **Operations** | Deployment, backups, monitoring, incident runbook, temporary access, secrets |
| **Security** | Roles, authentication, patient-data handling, audit, compliance notes |
| **Development** | Repository layout, local setup, conventions, how to extend CareFlow, testing, evals, CI |
| **Reference** | Tables generated from the code (settings, roles, actions, blockers, events, API, schema, HL7), glossary, FAQ, changelog |
| **Decisions** | Why the main design choices were made |
| **Current guides** | The guides written before this site; their content is being moved into the sections above |

Pages under **Reference** marked *Generated from the code* are produced by `scripts/gen_docs.py` and checked in CI,
so they always match the code. Anything CareFlow doesn't do yet is labelled **Not built yet**.

## Build these docs

```bash
pip install -r docs/requirements.txt
python scripts/gen_docs.py      # refresh the generated reference pages
mkdocs serve                    # http://localhost:8001
```
