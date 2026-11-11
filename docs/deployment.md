# Deploying CareFlow in production

This guide puts CareFlow on one hospital server with HTTPS, nightly backups and automatic restarts, using only
free and open-source software:

| Piece | Software | Cost |
| --- | --- | --- |
| Operating system | Ubuntu Server 24.04 LTS (or any Linux with Docker) | Free |
| Containers | Docker Engine + Docker Compose | Free |
| HTTPS | Caddy with Let's Encrypt certificates (renewed automatically) | Free |
| Database | PostgreSQL 16 | Free |
| Queue and locks | Redis 7 | Free |
| AI agents | Groq free tier, or no key at all (rule-based fallback) | Free |
| Single sign-on (optional) | Keycloak | Free |
| Uptime monitoring (optional) | Uptime Kuma | Free |

A small server is enough to start: 2 vCPU, 4 GB RAM, 40 GB SSD for a hospital with a few hundred beds.

> CareFlow is not a certified medical device and never makes clinical decisions. Before real patient data goes in,
> read [security-and-compliance.md](security-and-compliance.md) and agree on data handling with the hospital's
> privacy officer (DPDP Act 2023 in India).

---

## 1. Prepare the server

```bash
# Docker (official convenience script)
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # log out and back in

# Firewall: only SSH and HTTPS (80 is needed for the certificate challenge and redirects)
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
# Only if a hospital system will send HL7 over MLLP, and only from its address:
# sudo ufw allow from 10.0.4.20 to any port 2575 proto tcp
sudo ufw enable
```

Point a DNS name (for example `careflow.your-hospital.in`) at the server's address.

## 2. Configure

```bash
git clone <your copy of this repository> careflow && cd careflow
cp .env.production.example .env.production
```

Fill in `.env.production`. Generate each secret separately:

```bash
openssl rand -hex 32    # CAREFLOW_JWT_SECRET
openssl rand -hex 24    # POSTGRES_PASSWORD
openssl rand -hex 24    # REDIS_PASSWORD
openssl rand -hex 32    # CAREFLOW_OUTBOUND_SECRET (if you use outbound webhooks)
```

Required: `CAREFLOW_DOMAIN`, `ACME_EMAIL`, `CAREFLOW_JWT_SECRET`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`.
Keep `.env.production` readable only by the admin account (`chmod 600 .env.production`). It's in `.gitignore`.

## 3. Start

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
docker compose -f docker-compose.prod.yml --env-file .env.production ps
```

Open `https://careflow.your-hospital.in`. The first visit shows the setup page: name the hospital and create the
administrator. Then add staff on **Users** (including the **Hospital director** account for the dean).

What the production stack does differently from `docker-compose.yml`:

- **HTTPS only** through Caddy; the web app and API share one origin (`/api` is proxied), so no CORS is needed.
- **No database or Redis ports** are published; both are on a private Docker network. Redis requires a password.
- **API docs (`/docs`) are off**, HSTS and other security headers are on.
- **Restart policies** on every service, **health checks** on the API and web, **log rotation** (5 × 20 MB per service).
- The **HL7 listener** is bound to `127.0.0.1` unless you set `CAREFLOW_HL7_BIND=0.0.0.0` (then firewall it).
- **Nightly backups** (see below).

### Hospital network without public DNS

If the server is only reachable inside the hospital, Let's Encrypt can't issue a certificate. Edit
`deploy/Caddyfile`: change `{$CAREFLOW_DOMAIN}` to the internal name and add `tls internal` inside the block. Caddy
then uses its own certificate authority; install its root certificate
(`docker compose -f docker-compose.prod.yml exec caddy cat /data/caddy/pki/authorities/local/root.crt`) on hospital PCs
through group policy.

### Using Neon instead of the bundled PostgreSQL

Set `DATABASE_URL` in `.env.production` to the project's **pooled** connection string
(`neon connection-string production --pooled`). The API, workers and HL7 listener then use Neon; migrations and the
LangGraph checkpointer automatically use the direct host. The `postgres` container keeps running but holds no data,
and the `backup` container backs up only that empty local database: rely on Neon's point-in-time restore instead,
and take a periodic `pg_dump` from the **direct** connection string if you need a copy off Neon.

For a single long-running server, the **direct** string (no `-pooler`) is slightly faster: asyncpg keeps its
prepared-statement cache, saving a round trip per statement. Use the pooled string when many processes connect.

Run the CareFlow server **in the same region as the Neon project**. Each agent step makes several sequential
queries; with roughly 300 ms between server and database, a discharge authorization takes minutes instead of seconds.

## 4. Backups

The `backup` container writes `./backups/careflow-YYYYmmdd-HHMM.sql.gz` at start-up and every night at
`CAREFLOW_BACKUP_HOUR` (default 02:00, server time), keeping `CAREFLOW_BACKUP_KEEP_DAYS` days (default 14).

Copy backups off the server: a backup on the same disk doesn't survive a disk failure. A free option is `rclone` or
`rsync` from a cron job to another hospital machine or NAS:

```bash
# crontab -e   (03:30 every night)
30 3 * * * rsync -a --delete /home/admin/careflow/backups/ backup-nas:/careflow-backups/
```

### Restore a backup

Practise this once before go-live.

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production stop api worker hl7 web
gunzip -c backups/careflow-20261106-0200.sql.gz | \
  docker compose -f docker-compose.prod.yml --env-file .env.production exec -T postgres \
  sh -c 'dropdb -U careflow --if-exists careflow_restore && createdb -U careflow careflow_restore && psql -q -U careflow careflow_restore'
# check the restored copy, then swap it in:
docker compose -f docker-compose.prod.yml --env-file .env.production exec -T postgres \
  psql -U careflow -d postgres -c "ALTER DATABASE careflow RENAME TO careflow_old;" -c "ALTER DATABASE careflow_restore RENAME TO careflow;"
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
```

## 5. Upgrades

```bash
git pull
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

The API container runs `alembic upgrade head` on start, so database changes apply automatically. Take a backup first
(`docker compose ... restart backup` makes one immediately). After upgrading to the version with bed management,
run once: `docker compose -f docker-compose.prod.yml --env-file .env.production exec api python -m app.cli sync-beds`.

## 6. Accounts and security

- Passwords: at least 12 characters, mixing letters with digits or symbols. 5 wrong attempts lock an account for 15
  minutes (`CAREFLOW_LOGIN_MAX_FAILURES`, `CAREFLOW_LOGIN_LOCKOUT_MINUTES`); an administrator can unlock it on **Users**.
- Sessions last `CAREFLOW_JWT_TTL_MINUTES` (480 = one shift in the production example).
- Give each person their own account. Deactivate accounts on the day someone leaves, and remove test accounts
  before go-live.
- Single sign-on (optional, free): run Keycloak, create a realm, and set `CAREFLOW_OIDC_JWKS_URL` and
  `CAREFLOW_OIDC_AUDIENCE`. Keycloak can enforce MFA.
- Locked out of every admin account:
  `docker compose -f docker-compose.prod.yml --env-file .env.production exec api python -m app.cli reset-password --email you@hospital.in`

## 7. Monitoring

- `https://careflow.your-hospital.in/api/health` returns `{"database":"ok","redis":"ok","llm":"groq"}`. Point a free
  monitor (Uptime Kuma, self-hosted) at it.
- `docker compose -f docker-compose.prod.yml logs -f api worker hl7` for live logs.
- The **Integrations** page shows errors from connected systems; the **Director dashboard** shows the hospital's flow.

## 8. Go-live checklist

- [ ] DNS points at the server; `https://` loads with a valid certificate
- [ ] `.env.production` has unique random secrets and `chmod 600`
- [ ] Only ports 80/443 (and 2575 from the HIS address, if used) are open
- [ ] Setup done; administrator and director accounts created; test accounts removed
- [ ] A backup was restored successfully on a test machine
- [ ] Backups are copied off the server every night
- [ ] Integration keys created per system; HL7 test console messages accepted
- [ ] Uptime monitor on `/api/health`
- [ ] Staff trained with [user-guide.md](user-guide.md); privacy officer has signed off
