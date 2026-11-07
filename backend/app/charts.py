"""Declarative chart and dashboard specifications, and their storage on disk.

The AI never writes chart code: it produces a small, validatable `ChartSpec`.
The frontend turns it into ECharts applying the design system. That way a chart can
be edited by hand, versioned in Git and re-run without calling the model again.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from . import config

ChartType = Literal["bar", "hbar", "stacked_bar", "line", "area", "scatter", "heatmap", "kpi", "table", "text"]
ValueFormat = Literal["number", "integer", "currency", "percent"]


class Target(BaseModel):
    """Target / budget line (in a kpi: the goal to reach)."""
    value: float
    label: str = "Target"


class Annotation(BaseModel):
    """Vertical mark on the X axis: an event that explains a change (campaign, stockout…)."""
    x: str = Field(description="X axis value, exactly as it is in the data (e.g. 2026-03-01)")
    label: str


class ChartSpec(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    title: str
    subtitle: str | None = None
    type: ChartType
    sql: str = Field(default="", description="SQL over the semantic layer models")
    text: str | None = Field(default=None, description="Text block (markdown) if type=text")
    x: str | None = Field(default=None, description="X axis / category column")
    y: list[str] = Field(default_factory=list, description="Numeric columns (measures)")
    series: str | None = Field(default=None, description="Column whose values become series")
    value_format: ValueFormat = "number"
    currency: str | None = None
    insight: str | None = Field(default=None, description="Conclusion in one sentence")
    width: int = Field(default=6, ge=3, le=12, description="Width in columns of a 12-column grid")
    targets: list[Target] = Field(default_factory=list)
    annotations: list[Annotation] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))

    @model_validator(mode="after")
    def _check_fields(self) -> "ChartSpec":
        t = self.type
        if t == "text":
            if not (self.text or "").strip():
                raise ValueError("A text block needs 'text'")
            return self
        if not self.sql.strip():
            raise ValueError("A chart needs 'sql'")
        if t in {"bar", "hbar", "stacked_bar", "line", "area"}:
            if not self.x or not self.y:
                raise ValueError(f"A '{t}' chart needs 'x' and at least one column in 'y'")
            if self.series and len(self.y) != 1:
                raise ValueError("With 'series', use exactly one column in 'y'")
        elif t == "scatter":
            if not self.x or len(self.y) != 1:
                raise ValueError("A scatter needs 'x' and exactly one column in 'y'")
        elif t == "heatmap":
            if not self.x or not self.series or len(self.y) != 1:
                raise ValueError("A heatmap needs 'x', 'series' (Y axis) and one column in 'y' (value)")
        elif t == "kpi":
            if len(self.y) not in (1, 2):
                raise ValueError("A kpi needs 'y' = [value] or [value, previous_value]")
        return self

    def check_columns(self, columns: list[str]) -> None:
        needed = [c for c in [self.x, self.series, *self.y] if c]
        missing = [c for c in needed if c not in columns]
        if missing:
            raise ValueError(f"Columns {missing} aren't in the SQL result (it has: {columns})")


FindingKind = Literal["positive", "negative", "warning", "neutral"]


class Finding(BaseModel):
    title: str = Field(description="Finding in one sentence, with the key figure")
    detail: str = Field(description="Short explanation: cause, size and context (1-3 sentences)")
    kind: FindingKind = "neutral"
    chart_id: str | None = Field(default=None, description="Dashboard chart that backs it")


class DashboardInsights(BaseModel):
    summary: str = Field(description="Executive summary in 2-4 sentences (markdown)")
    findings: list[Finding] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    generated_at: str = Field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))
    scope: str | None = Field(default=None, description="Period or filters it was computed on")


class Dashboard(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    title: str
    description: str | None = None
    charts: list[ChartSpec] = Field(default_factory=list)
    insights: DashboardInsights | None = None
    created_at: str = Field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))
    updated_at: str = Field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))


class JsonStore:
    """One JSON file per object: easy to read, edit and version."""

    def __init__(self, directory: Path, model: type[BaseModel]) -> None:
        self.dir = directory
        self.model = model

    def _path(self, obj_id: str) -> Path:
        if not obj_id.isalnum():
            raise KeyError(obj_id)
        return self.dir / f"{obj_id}.json"

    def get(self, obj_id: str) -> Any:
        p = self._path(obj_id)
        if not p.exists():
            raise KeyError(obj_id)
        return self.model.model_validate_json(p.read_text())

    def put(self, obj: BaseModel) -> None:
        if hasattr(obj, "updated_at"):
            obj.updated_at = dt.datetime.now().isoformat(timespec="seconds")
        self._path(obj.id).write_text(json.dumps(obj.model_dump(), indent=2, ensure_ascii=False))

    def delete(self, obj_id: str) -> None:
        self._path(obj_id).unlink(missing_ok=True)

    def list(self) -> list[Any]:
        items = [self.model.model_validate_json(p.read_text()) for p in self.dir.glob("*.json")]
        return sorted(items, key=lambda o: getattr(o, "created_at", ""), reverse=True)


charts = JsonStore(config.CHARTS_DIR, ChartSpec)
dashboards = JsonStore(config.DASHBOARDS_DIR, Dashboard)
