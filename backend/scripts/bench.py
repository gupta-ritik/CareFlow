"""CareFlow benchmark: discharge authorization latency and Director dashboard cache behaviour.

Uses only the Python standard library. Run it against any CareFlow API (local, Render, a server):

  # 20 measured authorizations (+3 warm-up). Needs an admin (to admit test patients) and a clinician (to authorize).
  python scripts/bench.py authorize --api https://careflow-api.onrender.com/api \
      --admin admin@hospital.in --clinician doc@hospital.in --runs 20

  # 4 viewers polling the Director dashboard every 15 s for 5 minutes; reports latency and cache hit ratio.
  python scripts/bench.py dashboard --api https://careflow-api.onrender.com/api --director dean@hospital.in

Passwords come from the environment (CAREFLOW_BENCH_ADMIN_PASSWORD, CAREFLOW_BENCH_CLINICIAN_PASSWORD,
CAREFLOW_BENCH_DIRECTOR_PASSWORD) or the matching --...-password options.
On Windows, use 127.0.0.1 rather than localhost: "localhost" tries IPv6 first and adds about 2 s to every request.
Start the API with CAREFLOW_DB_STATS=true to get SQL statement counts and database time per request.

The authorize run admits synthetic patients named "Bench Patient" with MRNs starting BENCH-. Use a test hospital or a
Neon branch, never a hospital with real patients.
"""
import argparse
import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid


class Api:
    def __init__(self, base: str, timeout: float = 900):
        self.base, self.timeout = base.rstrip("/"), timeout

    def call(self, method: str, path: str, body=None, token: str | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={"content-type": "application/json"})
        if token:
            req.add_header("authorization", f"Bearer {token}")
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw, status, headers = r.read(), r.status, r.headers
        except urllib.error.HTTPError as e:
            raw, status, headers = e.read(), e.code, e.headers
        ms = (time.perf_counter() - t0) * 1000
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = raw.decode(errors="replace")
        return status, payload, headers, ms

    def login(self, email: str, password: str) -> str:
        status, payload, _, _ = self.call("POST", "/auth/login", {"email": email, "password": password})
        if status != 200:
            sys.exit(f"Login failed for {email}: {status} {payload}")
        return payload["accessToken"]


def pct(values: list[float], p: int) -> float:
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[p - 1]


def summary(label: str, ms: list[float]) -> str:
    return (f"{label}: n={len(ms)} p50={pct(ms, 50):.0f} ms  p95={pct(ms, 95):.0f} ms  p99={pct(ms, 99):.0f} ms  "
            f"mean={statistics.mean(ms):.0f} ms  max={max(ms):.0f} ms")


def header_num(headers, name: str) -> float | None:
    v = headers.get(name)
    return float(v) if v is not None else None


def authorize(a: argparse.Namespace) -> None:
    api = Api(a.api)
    status, health, _, cold_ms = api.call("GET", "/health")
    print(f"health {status} in {cold_ms:.0f} ms (includes wake-up if the service was asleep): {health}")
    admin = api.login(a.admin, a.admin_password)
    doc = api.login(a.clinician, a.clinician_password)

    times, server_ms, queries, db_ms = [], [], [], []
    for i in range(a.warmup + a.runs):
        body = {"displayName": "Bench Patient", "mrn": f"BENCH-{uuid.uuid4().hex[:8]}", "ward": "Bench", "bed": "",
                "payer": "Example TPA", "payerType": "cashless", "attending": "Dr. Bench", "hasLabOrders": True}
        st, pt, _, _ = api.call("POST", "/patients", body, admin)
        if st != 201:
            sys.exit(f"Admitting a test patient failed: {st} {pt}")
        st, out, h, ms = api.call("POST", f"/patients/{pt['ref']}/authorize-discharge", {}, doc)
        if st != 200:
            sys.exit(f"Authorization failed: {st} {out}")
        warm = i < a.warmup
        q, dbt = header_num(h, "X-DB-Queries"), header_num(h, "X-DB-Time-Ms")
        extra = f"  sql={q:.0f} db={dbt:.0f} ms" if q is not None else ""
        print(f"{'warm-up' if warm else 'run'} {i + 1 if warm else i - a.warmup + 1}: {ms:.0f} ms  "
              f"{out.get('workflowId')} {out.get('status')}{extra}")
        if warm:
            continue
        times.append(ms)
        if (srv := header_num(h, "X-Server-Time-Ms")) is not None:
            server_ms.append(srv)
        if q is not None:
            queries.append(q)
            db_ms.append(dbt or 0)

    print()
    print(summary("authorize (client)", times))
    if server_ms:
        print(summary("authorize (server)", server_ms))
        print(f"network between you and the API: about {statistics.mean(times) - statistics.mean(server_ms):.0f} ms per request")
    if queries:
        avg_q, avg_db = statistics.mean(queries), statistics.mean(db_ms)
        print(f"SQL statements/run={avg_q:.0f}  database time/run={avg_db:.0f} ms  avg per statement={avg_db / max(avg_q, 1):.1f} ms")
    else:
        print("No X-DB-* headers: start the API with CAREFLOW_DB_STATS=true to see statement counts.")


def dashboard(a: argparse.Namespace) -> None:
    api = Api(a.api, timeout=120)
    token = api.login(a.director, a.director_password)
    before = api.call("GET", "/executive/cache-stats", token=token)[1]
    stop_at = time.monotonic() + a.minutes * 60
    lock, times, queries, errors = threading.Lock(), [], [], []

    def viewer(n: int) -> None:
        time.sleep(n * a.interval / max(a.viewers, 1))  # stagger viewers like people opening the page at different times
        while time.monotonic() < stop_at:
            st, _, h, ms = api.call("GET", f"/executive/overview?days={a.days}", token=token)
            with lock:
                if st == 200:
                    times.append(ms)
                else:
                    errors.append(st)
                if (q := header_num(h, "X-DB-Queries")) is not None:
                    queries.append(q)
            time.sleep(a.interval)

    threads = [threading.Thread(target=viewer, args=(n,), daemon=True) for n in range(a.viewers)]
    print(f"{a.viewers} viewers polling every {a.interval:.0f} s for {a.minutes} min ...")
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    after = api.call("GET", "/executive/cache-stats", token=token)[1]
    if times:
        print(summary("dashboard", times))
    if errors:
        print(f"errors: {len(errors)} (status codes {sorted(set(errors))})")
    if queries:
        print(f"SQL statements per poll: mean={statistics.mean(queries):.1f} (0 = served from cache)  total={sum(queries):.0f}")
    hits, misses = after["hits"] - before["hits"], after["misses"] - before["misses"]
    if hits + misses:
        print(f"cache: hits={hits} misses={misses} hit ratio={hits / (hits + misses):.0%} (TTL {after['ttlSeconds']} s)")
    else:
        print("cache: no hits or misses recorded (Redis not connected, or CAREFLOW_EXECUTIVE_CACHE_SECONDS=0)")


def main() -> None:
    p = argparse.ArgumentParser(description="CareFlow benchmark")
    sub = p.add_subparsers(dest="cmd", required=True)
    au = sub.add_parser("authorize", help="time discharge authorizations")
    au.add_argument("--api", default="http://127.0.0.1:8000/api")
    au.add_argument("--admin", required=True)
    au.add_argument("--admin-password", default=os.getenv("CAREFLOW_BENCH_ADMIN_PASSWORD"))
    au.add_argument("--clinician", required=True)
    au.add_argument("--clinician-password", default=os.getenv("CAREFLOW_BENCH_CLINICIAN_PASSWORD"))
    au.add_argument("--runs", type=int, default=20)
    au.add_argument("--warmup", type=int, default=3)
    da = sub.add_parser("dashboard", help="poll the Director dashboard from several viewers")
    da.add_argument("--api", default="http://127.0.0.1:8000/api")
    da.add_argument("--director", required=True)
    da.add_argument("--director-password", default=os.getenv("CAREFLOW_BENCH_DIRECTOR_PASSWORD"))
    da.add_argument("--viewers", type=int, default=4)
    da.add_argument("--minutes", type=float, default=5)
    da.add_argument("--interval", type=float, default=15)
    da.add_argument("--days", type=int, default=30)
    a = p.parse_args()
    for name in ("admin_password", "clinician_password", "director_password"):
        if hasattr(a, name) and not getattr(a, name):
            p.error(f"--{name.replace('_', '-')} is required (or set CAREFLOW_BENCH_{name.upper()})")
    if a.cmd == "authorize":
        authorize(a)
    else:
        dashboard(a)


if __name__ == "__main__":
    main()
