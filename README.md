# CareFlow AI

**CareFlow gets discharged patients out of the hospital faster.**

When a doctor says a patient can go home, several departments still have work to finish: the discharge summary,
medicines, lab reports, insurance approval and the final bill. CareFlow checks all of them at the same time, shows
exactly what is holding the patient up, reminds the right people, and asks a person to approve anything risky.

CareFlow handles **paperwork only**. It never decides if a patient is medically fit, never changes medicines, and
never approves claims or payments.

> **Not a certified medical device.** Read [docs/security-and-compliance.md](docs/security-and-compliance.md) before
> using real patient data.

## Contents

1. [How it works](#how-it-works)
2. [Start it on your computer](#start-it-on-your-computer)
3. [Sign in and try it](#sign-in-and-try-it)
4. [Who can do what](#who-can-do-what)
5. [Screens](#screens)
6. [Director dashboard](#director-dashboard)
7. [Ask CareFlow](#ask-careflow)
8. [Where the data is stored](#where-the-data-is-stored)
9. [Put it online](#put-it-online)
10. [Connect hospital systems](#connect-hospital-systems)
11. [Important settings](#important-settings)
12. [Run the tests](#run-the-tests)
13. [Troubleshooting](#troubleshooting)
14. [Project layout](#project-layout)
15. [More documentation](#more-documentation)

---

## How it works

```
 Patient admitted ──► Doctor authorizes discharge ──► CareFlow checks every department at once
                                                            │
        ┌──────────────┬──────────────┬──────────────┬──────┴───────┐
   Documentation    Pharmacy     Laboratory      Insurance       Billing
        └──────────────┴──────────────┴──────────────┴──────┬───────┘
                                                            ▼
                         Finds what's blocking the patient, with proof
                                                            ▼
            Low-risk fix (a reminder) ──► done automatically
            Risky fix ──► waits for a person to approve, reject or ask for more information
                                                            ▼
            Re-checks the department to make sure the fix really happened
                                                            ▼
            All departments clear ──► "Ready to discharge" ──► patient leaves, bed goes to cleaning
```

A typical day:

1. A ward clerk **admits** the patient (or the hospital system sends it automatically). The bed shows *Occupied*.
2. A **clinician authorizes discharge**. Only the Clinician role can do this.
3. Each department works through its **queue**. CareFlow re-checks a department whenever it changes.
4. Risky fixes appear on **Approvals** for the right person to decide.
5. When everything is clear, a coordinator **closes the discharge**. The bed goes to *Cleaning*.
6. The **Director dashboard** shows the whole hospital's flow, and every step is in the **Audit trail**.

## Start it on your computer

You need **Docker Desktop**. Everything else runs inside it.

1. Start Docker Desktop.
2. Create a file called `.env` in this folder:

   ```dotenv
   CAREFLOW_JWT_SECRET=any-random-text-of-at-least-32-characters
   CAREFLOW_GROQ_API_KEY=your-groq-key   # optional: turns on the AI agents (free at console.groq.com)
   ```

3. Start everything:

   ```powershell
   docker compose up --build -d
   ```

4. Open **http://localhost:3000**.
5. Check **http://localhost:8000/api/health**. You should see `{"database":"ok","redis":"ok","llm":"groq"}`.

On a brand-new database, the first page asks you to name the hospital and create the administrator account.

> **Changed `.env`?** Recreate the backend so it picks up the new values:
> `docker compose up -d --force-recreate --no-deps api worker hl7`

<details>
<summary>Run without Docker (for development)</summary>

Needs Python 3.12+, Node 20+, PostgreSQL 16 and Redis 7.

```bash
# backend
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
cd backend
cp ../.env.example .env                              # set CAREFLOW_DATABASE_URL and CAREFLOW_JWT_SECRET
alembic upgrade head
uvicorn app.main:app --port 8000

# frontend (second terminal)
cd web && npm install && npm run build && npm start  # http://localhost:3000
```

If Docker can't use port 3000, run the backend in Docker (`docker compose up -d postgres redis api worker`) and the
frontend with the last command above.
</details>

## Sign in and try it

**Test accounts and test patients** for every role are in
[docs/manual-test-plan.md](docs/manual-test-plan.md), together with step-by-step test cases.

> The test accounts are for trying CareFlow only. **Deactivate them on the Users page before real use.**

Quickest way to see the whole flow:

1. Sign in as the **Ward clerk** and admit a test patient on **Patients**.
2. Sign in as the **Clinician** and click **Authorize discharge** on that patient.
3. Open the **Discharge board** to see what's blocking the patient.
4. Sign in as **Pharmacy**, **Billing** and the other departments, and clear their items on **Department queues**.
5. Sign in as the **Discharge coordinator**: decide anything on **Approvals**, then close the discharge.
6. Sign in as the **Hospital director** to see it all on the **Director dashboard**.

Want sample patients? `docker exec careflow-api-1 python -m app.cli load-examples` adds two clearly named ones.

## Who can do what

| Role | Main jobs |
| --- | --- |
| **Administrator** | Add users, manage integrations, admit patients, work any queue, decide any approval, close discharges |
| **Hospital director** | Director dashboard, view every queue, decide any approval, close discharges (can't change department work or users) |
| **Discharge coordinator** | Admit, decide medium-risk approvals, close discharges, manage beds |
| **Clinician** | **Authorize discharge** (the only role that can), documentation queue |
| **Ward clerk** | Admit, documentation queue, manage beds |
| **Pharmacy** · **Laboratory** · **Billing** | Their own department queue |
| **Insurance desk lead** | Insurance queue and insurance approvals |
| **Auditor** | Read-only; patient names are hidden |

Each person should have their own account. Passwords need 12+ characters with letters plus digits or symbols.
Five wrong passwords lock an account for 15 minutes; an administrator can unlock it on **Users**.

## Screens

The sidebar only shows the screens your role can use.

| Screen | What it's for |
| --- | --- |
| **Discharge board** | Every patient waiting to leave, stuck ones first, with the reason |
| **Patients** | Admit, search, edit ward/bed, authorize discharge, print the clearance report |
| **Department queues** | Each department's to-do list with one-click actions |
| **Approvals** | Approve, reject or ask for more information |
| **Beds** | Bed board by ward: occupied, cleaning, available |
| **Director dashboard** | Hospital-wide view for the dean (below) |
| **Operations** | Blockers, queue sizes, AI agent activity, CSV export |
| **Audit trail** | Every decision and event, with who did it |
| **Integrations** | Connected hospital systems, keys, message log, HL7 test console |
| **Users** | Add people, reset passwords, unlock accounts |

## Director dashboard

For the dean or hospital director. Sign in with a **Hospital director** account and open **Director dashboard**
(the first item in the sidebar, or http://localhost:3000/director).

To give the dean access: an administrator opens **Users → Add user** and picks the role **Hospital director**.

It refreshes every 15 seconds and shows:

- **Census:** patients in hospital, waiting for authorization, blocked, ready to leave, discharged today
- **Longest waits:** the 10 patients waiting longest, and which departments they're waiting on
- **Departments:** open problems per department, with the current bottleneck highlighted
- **Decisions waiting:** pending approvals and how long decisions take
- **Trends:** the last 14 days of discharges, and flow times for the last 7, 30 or 90 days
- **Beds, connected systems and AI usage**
- **Ask CareFlow:** a question box (see below)

## Ask CareFlow

The director can type a question about operations, for example *"Why were discharges slow last Monday?"*, and get a
short plain-English answer. Every number in the answer is checked against the hospital's data. Open it from the
**Ask CareFlow** panel on the Director dashboard, or the full page at http://localhost:3000/ask (history on the right).

- It only uses **totals**: no patient names or MRNs, and groups smaller than 5 patients are hidden.
- It refuses questions about patient identity, clinical matters, other hospitals, and any change to data.
- **See the numbers** shows the exact results (measure, period, filters, values) that each sentence cites.
- It works without an LLM (rule-based answers). With `CAREFLOW_GROQ_API_KEY` set, Groq writes the answers, and a
  checker still removes any sentence whose numbers don't match the data.

Who can use it: the **Hospital director** role (permission `ask:use`).

**Try it with synthetic data** (fictional patients only, in its own hospital `synthetic-hospital`):

```bash
cd backend
python -m app.synthetic.generator --days 120 --seed 42
```

It prints a director sign-in (`director@synthetic-hospital.test`) and a one-time password. Set
`CAREFLOW_SYNTHETIC_DIRECTOR_PASSWORD` first if you want to choose the password.

### How accurate is it?

The synthetic history has six problems planted on purpose (for example *"TPA Gamma is slow on Mondays from day 60"*
and a red-herring day with only 2 discharges). `evals/ask/run_ask_evals.py` asks 32 questions and scores the answers.
Full results: [evals/ask/results.md](evals/ask/results.md).

<!-- ask-eval-table -->
| Mode | Planted cause found (top-1) | Red herring not called a trend | Refused correctly | Clarified | "Data can't answer" | Unsupported claims | Identity leaks | Median time |
|---|---|---|---|---|---|---|---|---|
| Rule-based fallback (no LLM) | 15 of 15 | 2 of 2 | 4 of 4 | 2 of 2 | 3 of 3 | 0 | 0 | 0.1 s |
| Groq (LLM used in 20 of 32 answers) | 15 of 15 | 2 of 2 | 4 of 4 | 2 of 2 | 3 of 3 | 0 | 0 | 24 s |
<!-- /ask-eval-table -->

Be careful with these numbers:

- They are measured on the same synthetic data the rules were tuned on. They show that the pipeline works end to
  end; they don't predict accuracy on real hospital data or on questions worded differently.
- In Groq mode, the planted cause is found because the deterministic statistics find it. The LLM only plans and
  writes, and the critic removes any sentence whose numbers don't match.
- In Groq mode, 12 answers didn't use the LLM: refusals and clarifying questions need none, and a few ran short of
  the 30-second time budget, so those steps used the rules.
- On Groq's free tier, an answer takes about 24 seconds, mostly waiting on the rate limit.
- CI runs the fallback mode on every push.

```bash
python evals/ask/run_ask_evals.py --mode fallback   # no key needed
python evals/ask/run_ask_evals.py --mode both       # also with Groq, if CAREFLOW_GROQ_API_KEY is set
```

## Where the data is stored

| What | Where |
| --- | --- |
| Patients, workflows, users, audit trail | **The local Docker database** (the `careflow_pgdata` volume). This is the default |
| Event queue, locks, dashboard cache | Redis (in Docker) |

**Why local?** The app runs on your computer, so its database should be on your computer too. Pages load in under
0.1 seconds and an AI check takes about 4 seconds. With the cloud database in the USA, every query from India took
about 0.35 seconds, pages took 3–14 seconds and AI checks took about 30 seconds.

### Using the Neon cloud database instead

This project is also linked to the Neon project **purple-dew-45371420** (branch `production`). Its connection
lines are in `.env`, commented out. Use Neon when the backend runs **in the cloud next to it** (Render, Ohio; see
[Put it online](#put-it-online)), not when it runs on your PC.

To switch to Neon: uncomment `DATABASE_URL` and `DATABASE_URL_UNPOOLED` in `.env`, then run
`docker compose up -d --force-recreate --no-deps api worker hl7`. To switch back, comment them out again and run
the same command.

Each database keeps its own data. Switching doesn't copy patients or users across.

Set up Neon on a new machine:

```bash
npm i -g neon@latest
neon login                                   # once, in the browser
neon env pull                                # writes DATABASE_URL and DATABASE_URL_UNPOOLED into .env
cd backend && alembic upgrade head           # creates the tables
```

## Put it online

| Option | Good for | Cost | Guide |
| --- | --- | --- | --- |
| **Your computer** (Docker) | Development and testing | Free | [Start it on your computer](#start-it-on-your-computer) |
| **Free cloud demo:** Vercel (website) + Render (API and Redis) + Neon (database) | Demos with test data only | Free | Below |
| **Hospital server** with HTTPS and nightly backups | Real use | Free software, your own server | [docs/deployment.md](docs/deployment.md) |

### Free cloud demo

1. **API on Render.** In Render, choose **New → Blueprint** and pick this repository. [render.yaml](render.yaml)
   creates the API and Redis in the Ohio region, next to the Neon database. Fill in:
   - `CAREFLOW_DATABASE_URL`: the output of `neon connection-string production`
   - `CAREFLOW_CORS_ORIGINS`: your Vercel address, for example `["https://careflow.vercel.app"]`
   - `CAREFLOW_GROQ_API_KEY`: optional
2. **Website on Vercel.** Import the repository, set the root directory to `web`, and add
   `NEXT_PUBLIC_API_BASE=https://<your-render-service>.onrender.com/api`.
3. Open the Vercel address and sign in.

Free-plan limits: the API sleeps after 15 idle minutes (the next visit waits 30–60 seconds), hospital systems must send
HL7 over HTTPS instead of port 2575, and the free Redis loses its contents on restart. **Don't put real patient data
on the free plans.**

## Connect hospital systems

CareFlow can receive updates directly from the hospital's systems, so staff don't have to retype them.

- **HL7 v2** from the HIS, lab or pharmacy: admissions, transfers, orders, lab results and discharges, over port 2575
  (MLLP) or HTTPS.
- **JSON connectors** for the TPA/insurer, pharmacy, lab and HIS.
- **Outgoing notifications** to each system when something changes (signed webhooks, and HL7 ADT^A03 on discharge).
- **FHIR** patient import.

Each system gets its own key on the **Integrations** page. Full guide for hospital IT:
[docs/integrations.md](docs/integrations.md).

## Important settings

Put these in `.env`. The full list is in [docs/reference.md](docs/reference.md#configuration).

| Setting | What it does |
| --- | --- |
| `CAREFLOW_JWT_SECRET` | Signs sign-in sessions. **Required**: 32+ random characters (`openssl rand -hex 32`) |
| `DATABASE_URL` / `CAREFLOW_DATABASE_URL` | Which database to use (Neon or local). `CAREFLOW_DATABASE_URL` wins if both are set |
| `CAREFLOW_GROQ_API_KEY` | Turns on the AI agents. Without it, CareFlow uses built-in rules and still works |
| `CAREFLOW_CORS_ORIGINS` | Website addresses allowed to call the API |
| `CAREFLOW_ENV` | `dev` locally, `production` online (refuses to start with a weak secret) |
| `CAREFLOW_SLA_PHARMACY` (and `_INSURANCE`, `_BILLING`, ...) | Minutes before a department counts as delayed |
| `NEXT_PUBLIC_API_BASE` | Where the website finds the API. Set when building the website |

## Run the tests

```bash
cd backend && python -m pytest -q          # 65 automated tests (needs the Docker database and Redis running)
python evals/run_evals.py                  # 30 discharge scenarios; all pass
```

The tests use a separate `careflow_test` database. Create it once:

```powershell
docker exec careflow-postgres-1 psql -U careflow -d postgres -c "CREATE DATABASE careflow_test OWNER careflow;"
```

For testing by hand, follow [docs/manual-test-plan.md](docs/manual-test-plan.md).

## Troubleshooting

| Problem | Fix |
| --- | --- |
| No **Authorize discharge** button | Only the **Clinician** role can authorize. Sign in as a clinician |
| Health shows `deterministic fallback` although the Groq key is in `.env` | Recreate the backend: `docker compose up -d --force-recreate --no-deps api worker` |
| Website won't start: *port 3000 is already allocated* | Stop whatever uses port 3000, or restart Docker Desktop, or run the website without Docker |
| *This account is locked* | Wait 15 minutes, or an administrator clicks **Unlock** on **Users** |
| New password refused | Use 12+ characters, mix letters with digits or symbols, and don't include your email name |
| Creating a user fails with *reserved name* | Emails ending in `.local`, `.test` or `localhost` aren't allowed. Use a normal domain |
| The board doesn't change after a department finishes | An approval is pending; updates wait until it's decided |
| A pharmacy delay doesn't show yet | It appears 60 minutes after discharge authorization (`CAREFLOW_SLA_PHARMACY`) |
| *Every department must be cleared before the patient leaves* | Clear the remaining items first |
| Everything is slow (several seconds per page) | The app is using a far-away database. Use the local one: see [Where the data is stored](#where-the-data-is-stored) |
| *Can't reach the CareFlow server* | Check that the backend is running: `docker compose ps` and http://localhost:8000/api/health |
| Locked out of every admin account | `docker exec careflow-api-1 python -m app.cli reset-password --email you@hospital.in` |

Logs: `docker logs --tail 100 careflow-api-1`

## Project layout

```
web/        Website (Next.js)
backend/    API and AI agents (FastAPI, LangGraph, PostgreSQL, Redis)
  app/agents/       the discharge workflow and the four AI agents
  app/api/          API endpoints
  app/integrations/ HL7 and connectors for hospital systems
  tests/            automated tests
  scripts/bench.py  speed benchmark
evals/      30 test scenarios for the agents
deploy/     HTTPS and backup files for the hospital-server setup
docs/       guides (below)
render.yaml free cloud demo setup on Render
```

## More documentation

| Document | For |
| --- | --- |
| [docs/user-guide.md](docs/user-guide.md) | Hospital staff: daily use |
| [docs/manual-test-plan.md](docs/manual-test-plan.md) | Testers: test accounts, test patients, step-by-step test cases |
| [docs/deployment.md](docs/deployment.md) | IT: install on a hospital server with HTTPS and backups |
| [docs/integrations.md](docs/integrations.md) | IT: connect HL7, TPA, pharmacy, lab and HIS systems |
| [docs/fhir.md](docs/fhir.md) | IT: connect a FHIR server |
| [docs/security-and-compliance.md](docs/security-and-compliance.md) | Security and privacy review |
| [docs/reference.md](docs/reference.md) | Developers: code layout, every setting, API, rules, test results, performance, roadmap |
