"""Scheduling models (frequency and condition), with no agent dependencies so MCP can use them."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class Frequency(BaseModel):
    """When it runs. E.g. Monday 09:00 → {"every": "week", "weekday": 0, "at": "09:00"}."""
    model_config = ConfigDict(extra="forbid")
    every: Literal["hour", "day", "week", "month"] = "day"
    at: str = Field(default="08:00", pattern=r"^\d{2}:\d{2}$")
    weekday: int = Field(default=0, ge=0, le=6, description="0 = Monday")
    day: int = Field(default=1, ge=1, le=28)

    def describe(self) -> str:
        if self.every == "hour":
            return f"every hour (at minute {self.at[3:]})"
        if self.every == "day":
            return f"every day at {self.at}"
        if self.every == "week":
            return f"every {WEEKDAYS[self.weekday]} at {self.at}"
        return f"on day {self.day} of every month at {self.at}"

    def next_after(self, after: dt.datetime) -> dt.datetime:
        h, m = (int(x) for x in self.at.split(":"))
        if self.every == "hour":
            cand = after.replace(minute=m, second=0, microsecond=0)
            return cand if cand > after else cand + dt.timedelta(hours=1)
        cand = after.replace(hour=h, minute=m, second=0, microsecond=0)
        if self.every == "day":
            return cand if cand > after else cand + dt.timedelta(days=1)
        if self.every == "week":
            cand += dt.timedelta(days=(self.weekday - cand.weekday()) % 7)
            return cand if cand > after else cand + dt.timedelta(days=7)
        cand = cand.replace(day=self.day)
        if cand <= after:
            y, mo = (cand.year + 1, 1) if cand.month == 12 else (cand.year, cand.month + 1)
            cand = cand.replace(year=y, month=mo)
        return cand


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["change_pct", "value"] = "change_pct"
    op: Literal["<", ">"] = "<"
    threshold: float = Field(description="change_pct: in % (e.g. -15 = falls more than 15 %); value: absolute value")

    def describe(self, label: str) -> str:
        if self.type == "change_pct":
            return f"{label} {'falls' if self.op == '<' else 'rises'} {'more than' if self.threshold else ''} {abs(self.threshold):g} % against the previous period".replace("  ", " ")
        return f"{label} {'below' if self.op == '<' else 'above'} {self.threshold:g}"
