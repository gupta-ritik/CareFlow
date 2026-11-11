"""Standalone event consumer (docker-compose `worker` service). Run: python -m app.worker"""
import asyncio
import logging
import os

from redis.asyncio import Redis

from . import db
from .config import get_settings
from .engine import Engine
from .events import consume_forever


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = get_settings()
    db.init_engine(s.database_url)
    redis = Redis.from_url(s.redis_url)
    from .checkpoint import postgres_checkpointer

    async with postgres_checkpointer(s.database_url) as cp:
        engine = Engine(db.sessionmaker(), cp, s, redis)
        await consume_forever(redis, engine, db.sessionmaker(), os.getenv("HOSTNAME", "worker-1"))


if __name__ == "__main__":
    asyncio.run(main())
