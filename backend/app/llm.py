"""LLM access for agents.

Agents call `structured(...)`, which returns a validated Pydantic object or raises `LLMUnavailable`.
Callers then fall back to their deterministic implementation and record mode="fallback".
Inputs must already be redacted; `structured` checks this and refuses prompts containing an identifier.
"""
import time
from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

from .config import get_settings

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(Exception):
    pass


@dataclass
class LLMResult:
    value: BaseModel
    tokens: int
    latency_ms: int
    model: str


class LLMBackend(Protocol):
    name: str

    async def structured(self, schema: type[T], system: str, user: str, size: str) -> LLMResult: ...


class GroqBackend:
    name = "groq"

    def __init__(self):
        from langchain_core.rate_limiters import InMemoryRateLimiter
        from langchain_groq import ChatGroq

        s = get_settings()
        limiter = InMemoryRateLimiter(requests_per_second=s.llm_requests_per_second, check_every_n_seconds=0.1, max_bucket_size=4)
        common = dict(api_key=s.groq_api_key, temperature=0, timeout=s.llm_timeout_seconds, max_retries=2, rate_limiter=limiter)
        self._models = {
            "small": ChatGroq(model=s.groq_model_small, **common),
            "large": ChatGroq(model=s.groq_model_large, **common),
        }

    async def structured(self, schema, system, user, size="small") -> LLMResult:
        model = self._models[size]
        started = time.perf_counter()
        try:
            runnable = model.with_structured_output(schema, include_raw=True)
            out = await runnable.ainvoke([("system", system), ("human", user)])
        except Exception as exc:  # network, 429 after retries, tool_use_failed, ...
            raise LLMUnavailable(f"Groq call failed: {exc}") from exc
        if out.get("parsing_error") or out.get("parsed") is None:
            raise LLMUnavailable(f"Groq returned output that failed validation: {out.get('parsing_error')}")
        usage = getattr(out["raw"], "usage_metadata", None) or {}
        return LLMResult(out["parsed"], int(usage.get("total_tokens", 0)), int((time.perf_counter() - started) * 1000), model.model_name)


_backend: LLMBackend | None = None
_override: LLMBackend | None = None


def set_backend(backend: LLMBackend | None) -> None:
    """Tests and evals inject a fake backend here."""
    global _override
    _override = backend


def backend() -> LLMBackend | None:
    global _backend
    if _override is not None:
        return _override
    s = get_settings()
    if s.llm_mode == "off" or not s.groq_api_key:
        return None
    if _backend is None:
        _backend = GroqBackend()
    return _backend


async def structured(schema: type[T], system: str, user: str, size: str = "small", forbidden: list[str] | None = None) -> LLMResult:
    b = backend()
    if b is None:
        raise LLMUnavailable("No LLM configured (set CAREFLOW_GROQ_API_KEY)")
    for value in forbidden or []:
        if value and value.lower() in user.lower():
            raise LLMUnavailable("Refused: prompt contains a patient identifier")
    return await b.structured(schema, system, user, size)
