"""Pydantic schemas for Ask CareFlow: what the LLM nodes must return, and the validated plan the data agent runs."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .measures import DIMENSIONS, REGISTRY


class Period(BaseModel):
    start: date
    end: date
    label: str = ""

    @model_validator(mode="after")
    def _order(self):
        if self.end < self.start:
            raise ValueError("period end is before its start")
        if (self.end - self.start).days > 400:
            raise ValueError("period is longer than 400 days")
        return self


class Compare(BaseModel):
    """period: the same measure in another period. rest: the focus values against everything else in the same period."""

    kind: Literal["period", "rest"]
    period: Period | None = None
    focus: dict[str, list[str]] = Field(default_factory=dict)
    focus_label: str = ""
    baseline_label: str = ""

    @model_validator(mode="after")
    def _complete(self):
        if self.kind == "period" and self.period is None:
            raise ValueError("a period comparison needs a baseline period")
        if self.kind == "rest" and not self.focus:
            raise ValueError("a 'rest' comparison needs focus values")
        return self


class Step(BaseModel):
    measure: str
    group_by: list[str] = Field(default_factory=list, max_length=2)
    filters: dict[str, list[str]] = Field(default_factory=dict)
    period: Period
    compare: Compare | None = None
    rank: Literal["highest", "lowest"] = "highest"
    why: str = ""  # one line for the progress list, e.g. "Splitting by department"

    @model_validator(mode="after")
    def _registry(self):
        m = REGISTRY.get(self.measure)
        if m is None:
            raise ValueError(f"unknown measure {self.measure!r}; allowed: {', '.join(REGISTRY)}")
        for d in self.group_by:
            if d not in m.dimensions:
                raise ValueError(f"measure {self.measure} can't be split by {d!r}; allowed: {', '.join(m.dimensions)}")
        keys = list(self.filters) + (list(self.compare.focus) if self.compare else [])
        for f in keys:
            if f not in m.filters:
                raise ValueError(f"measure {self.measure} can't be filtered by {f!r}; allowed: {', '.join(m.filters)}")
        for f, values in {**self.filters, **(self.compare.focus if self.compare else {})}.items():
            if not values or len(values) > 30:
                raise ValueError(f"filter {f} needs 1-30 values")
            allowed = DIMENSIONS[f].values if f in DIMENSIONS else None
            if allowed and any(v not in allowed for v in values):
                raise ValueError(f"filter {f}: values must be among {', '.join(allowed)}")
        return self


class Plan(BaseModel):
    steps: list[Step] = Field(min_length=1, max_length=4)


# ---- LLM outputs ------------------------------------------------------------------------------------------------
class GuardOut(BaseModel):
    allowed: bool
    category: Literal["ok", "identity", "clinical", "other_hospital", "write", "injection", "off_topic"]
    reason: str = ""


class UnderstandOut(BaseModel):
    measure: str | None = Field(None, description="one of the registry measure names, or null if unclear")
    intent: Literal["why_slow", "ranking", "change", "focus_vs_rest", "describe", "trend"] | None = None
    group_by: str | None = None
    clarify: str | None = Field(None, description="ONE short clarifying question if something essential is missing")

    @field_validator("measure")
    @classmethod
    def _known(cls, v):
        if v is not None and v not in REGISTRY:
            raise ValueError(f"unknown measure {v!r}")
        return v


class PlanOut(BaseModel):
    steps: list[Step] = Field(min_length=1, max_length=4)


class AnalystOut(BaseModel):
    extra_step: Step | None = Field(None, description="one more data step to dig deeper, or null")
    reason: str = ""


class Claim(BaseModel):
    result_id: str
    field: str
    value: float


class Sentence(BaseModel):
    text: str = Field(min_length=3, max_length=400)
    claims: list[Claim] = Field(default_factory=list)


class WriterOut(BaseModel):
    sentences: list[Sentence] = Field(min_length=1, max_length=5)
    follow_ups: list[str] = Field(default_factory=list, max_length=3)


class AskIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    session_id: str | None = Field(None, alias="sessionId")

    model_config = {"populate_by_name": True}


class FeedbackIn(BaseModel):
    rating: Literal[-1, 1]
    comment: str | None = Field(None, max_length=1000)
