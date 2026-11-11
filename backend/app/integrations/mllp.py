"""HL7 v2 MLLP listener (docker-compose `hl7` service).

    python -m app.integrations.mllp                      # listen on CAREFLOW_HL7_MLLP_PORT (2575)
    python -m app.integrations.mllp send FILE [HOST PORT] # send a message file (segments one per line) and print the ACK

Point the HIS / interface engine (Mirth Connect, NextGen Connect, Rhapsody, ...) at this port. Each framed message
is processed and answered with an ACK (AA accepted, AE error, AR rejected). Department updates are queued on Redis
Streams for the workers; if Redis is down, they're processed in this process.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

from .. import db
from ..config import get_settings
from ..dburl import checkpoint_url
from . import hl7
from .service import Bus, IntegrationError, handle_hl7, resolve_tenant

log = logging.getLogger("careflow.mllp")
MAX_MESSAGE = 1_000_000


class Listener:
    def __init__(self, sm, bus: Bus, tenant_slug: str | None, allowed: list[str]):
        self.sm, self.bus, self.tenant_slug, self.allowed = sm, bus, tenant_slug, set(allowed)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        host = peer[0] if peer else "?"
        if self.allowed and host not in self.allowed:
            log.warning("MLLP connection from %s refused (not in CAREFLOW_HL7_ALLOWED_HOSTS)", host)
            writer.close()
            return
        log.info("MLLP connection from %s", host)
        try:
            while True:
                try:
                    await reader.readuntil(hl7.SB)
                    data = await reader.readuntil(hl7.EB + hl7.CR)
                except asyncio.IncompleteReadError:
                    break
                except asyncio.LimitOverrunError:
                    log.warning("MLLP message from %s too large; closing", host)
                    break
                text = data[: -len(hl7.EB + hl7.CR)].decode("utf-8", "replace")
                try:
                    async with self.sm() as s:
                        tenant = await resolve_tenant(s, self.tenant_slug)
                    ack, out = await handle_hl7(self.sm, self.bus, tenant, text)
                    log.info("HL7 from %s: %s %s", host, out.status, out.summary[:200])
                except IntegrationError as exc:
                    ack = hl7.ack(None, "AE", str(exc))
                except Exception as exc:  # never drop the connection without an answer
                    log.exception("HL7 processing failed")
                    ack = hl7.ack(None, "AE", f"Internal error: {type(exc).__name__}")
                writer.write(hl7.frame(ack))
                await writer.drain()
        finally:
            writer.close()


async def serve() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = get_settings()
    db.init_engine(s.database_url)
    sm = db.sessionmaker()
    redis, engine, stack = None, None, None
    try:
        from redis.asyncio import Redis

        redis = Redis.from_url(s.redis_url)
        await redis.ping()
    except Exception as exc:
        log.warning("Redis unavailable (%s); HL7 updates will be processed in this process", exc)
        redis = None
    if redis is None:
        import contextlib

        from ..checkpoint import postgres_checkpointer
        from ..engine import Engine

        stack = contextlib.AsyncExitStack()
        cp = await stack.enter_async_context(postgres_checkpointer(s.database_url))
        engine = Engine(sm, cp, s, None)
    listener = Listener(sm, Bus(redis, engine), s.hl7_tenant, s.hl7_allowed_hosts)
    server = await asyncio.start_server(listener.handle, host="0.0.0.0", port=s.hl7_mllp_port, limit=MAX_MESSAGE)
    log.info("HL7 MLLP listener on port %s", s.hl7_mllp_port)
    try:
        async with server:
            await server.serve_forever()
    finally:
        if stack:
            await stack.aclose()


async def send_file(path: str, host: str, port: int) -> None:
    from .outbound import mllp_send

    text = open(path, encoding="utf-8").read()
    text = "\r".join(ln.rstrip() for ln in text.splitlines() if ln.strip())
    print((await mllp_send(host, port, text)).replace("\r", "\n"))


if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "send":
        asyncio.run(send_file(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "localhost",
                              int(sys.argv[4]) if len(sys.argv) > 4 else int(os.getenv("CAREFLOW_HL7_MLLP_PORT", "2575"))))
    else:
        asyncio.run(serve())
