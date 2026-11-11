"""Cache-aside for read-heavy, non-clinical aggregates (the Director dashboard).

Never use this on the authorization, approval, department-record or audit paths: those are read by people and agents who
act on them, and a stale copy could approve or skip the wrong thing. The database stays the source of truth; Redis only
holds copies that expire.

- TTL with jitter, so many keys don't expire in the same second.
- Versioned invalidation: `bump(scope)` moves every key in the scope to a new version (no racing deletes).
- Stampede guard: when a hot key expires, one request rebuilds it and the others wait briefly for that result.
- Redis down or erroring: always load from the database. The cache can make a page faster, never make it fail.
"""
import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi.encoders import jsonable_encoder

log = logging.getLogger("careflow.cache")

HIT, MISS = "cf:stats:hit", "cf:stats:miss"


class Cache:
    def __init__(self, redis, prefix: str = "cf"):
        self.r = redis  # None = no Redis configured: always go to the database
        self.prefix = prefix

    async def _version(self, scope: str) -> int:
        return int(await self.r.get(f"{self.prefix}:v:{scope}") or 0)

    async def bump(self, scope: str) -> None:
        """Invalidate everything cached under `scope`. Safe to call when Redis is down."""
        if self.r is None:
            return
        try:
            await self.r.incr(f"{self.prefix}:v:{scope}")
        except Exception as exc:  # noqa: BLE001 - invalidation failure only means data stays at most one TTL old
            log.warning("cache bump failed for %s: %s", scope, exc)

    async def get_or_load(self, scope: str, name: str, ttl: int, load: Callable[[], Awaitable[Any]]) -> Any:
        if self.r is None or ttl <= 0:
            return await load()
        try:
            key = f"{self.prefix}:{scope}:v{await self._version(scope)}:{name}"
            if (hit := await self.r.get(key)) is not None:
                await self.r.incr(HIT)
                return json.loads(hit)
            await self.r.incr(MISS)
            have_lock = await self.r.set(key + ":lock", "1", nx=True, ex=10)
        except Exception as exc:  # noqa: BLE001
            log.warning("cache read failed, loading from the database: %s", exc)
            return await load()

        if have_lock:
            try:
                value = jsonable_encoder(await load())  # same JSON the API would send (datetimes as ISO strings)
                try:
                    await self.r.set(key, json.dumps(value), ex=ttl + random.randint(0, max(1, ttl // 5)))
                except Exception as exc:  # noqa: BLE001
                    log.warning("cache write failed: %s", exc)
                return value
            finally:
                try:
                    await self.r.delete(key + ":lock")
                except Exception:  # noqa: BLE001 - the lock expires by itself after 10 s
                    pass

        # Someone else is rebuilding this key: wait up to 2 s for their result, then load it ourselves.
        for _ in range(20):
            await asyncio.sleep(0.1)
            try:
                if (hit := await self.r.get(key)) is not None:
                    return json.loads(hit)
            except Exception:  # noqa: BLE001
                break
        return await load()

    async def stats(self) -> dict:
        if self.r is None:
            return {"hits": 0, "misses": 0, "hitRatio": None}
        try:
            hits, misses = (int(v or 0) for v in await self.r.mget(HIT, MISS))
        except Exception:  # noqa: BLE001
            return {"hits": 0, "misses": 0, "hitRatio": None}
        return {"hits": hits, "misses": misses, "hitRatio": round(hits / (hits + misses), 3) if hits + misses else None}


def executive_scope(tenant_id) -> str:
    """One scope per hospital, so one hospital never sees another's numbers."""
    return f"exec:{tenant_id}"
