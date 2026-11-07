"""Official metrics: named aggregate definitions (e.g. "conversion rate = sum(conv)/sum(clicks)") and a
query compiler with relative periods and comparisons (previous period, previous year).

A metric is defined once and everyone (the AI, dashboards, alerts) computes it the same way. They're saved in
workspace/semantic/metrics.json, apart from Wren's MDL, and compiled to SQL over the semantic models.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import config
from .charts import ValueFormat
from .filters import time_column
from .semantic import SemanticLayer
from .workspace import QueryError, QueryResult, quote_ident, sql_literal

METRICS_FILE = config.SEMANTIC_FILE.parent / "metrics.json"
Grain = Literal["day", "week", "month", "quarter", "year"]
Compare = Literal["previous_period", "previous_year"]
PERIODS = ["all", "last_7_days", "last_30_days", "last_90_days", "last_12_months", "mtd", "qtd", "ytd",
           "last_month", "last_quarter", "last_year"]
PERIOD_LABELS = {
    "all": "all time", "last_7_days": "last 7 days", "last_30_days": "last 30 days",
    "last_90_days": "last 90 days", "last_12_months": "last 12 months", "mtd": "month to date",
    "qtd": "quarter to date", "ytd": "year to date", "last_month": "last full month",
    "last_quarter": "last full quarter", "last_year": "last full year",
}


class Metric(BaseModel):
    name: str = Field(description="Identifier in snake_case")
    label: str = Field(description="Readable name")
    model: str
    expression: str = Field(description="AGGREGATE SQL expression over the model's columns, e.g. sum(net_revenue)")
    description: str = ""
    format: ValueFormat = "number"
    currency: str | None = None
    time_column: str | None = Field(default=None, description="Time column; the model's by default")
    higher_is_better: bool = True
    certified: bool = False
    updated_at: str = Field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))

    @field_validator("name")
    @classmethod
    def _slug(cls, v: str) -> str:
        v = v.strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,59}", v):
            raise ValueError("name must be snake_case (letters, digits and _), starting with a letter")
        return v


class DimFilter(BaseModel):
    """Filter by the values of a column. E.g. {"column": "channel", "values": ["Meta Ads"]}."""
    model_config = ConfigDict(extra="forbid")
    column: str
    values: list[str]


class MetricQuery(BaseModel):
    metrics: list[str] = Field(min_length=1)
    group_by: list[str] = Field(default_factory=list)
    grain: Grain | None = None
    period: str = "all"
    date_from: str | None = None
    date_to: str | None = None
    compare: Compare | None = None
    filters: list[DimFilter] = Field(default_factory=list)
    order_by: str | None = None
    limit: int | None = Field(default=None, ge=1, le=5000)


# ---------------------------------------------------------------------- periods
def _month_start(d: dt.date) -> dt.date:
    return d.replace(day=1)


def _add_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    last = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return dt.date(y, m, min(d.day, last))


def _quarter_start(d: dt.date) -> dt.date:
    return dt.date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def resolve_period(period: str, anchor: dt.date) -> tuple[dt.date, dt.date] | None:
    """Range [start, end] (inclusive) relative to `anchor`, the latest available data."""
    if period == "all":
        return None
    days = {"last_7_days": 7, "last_30_days": 30, "last_90_days": 90}
    if period in days:
        return anchor - dt.timedelta(days=days[period] - 1), anchor
    if period == "last_12_months":
        return _add_months(anchor, -12) + dt.timedelta(days=1), anchor
    if period == "mtd":
        return _month_start(anchor), anchor
    if period == "qtd":
        return _quarter_start(anchor), anchor
    if period == "ytd":
        return dt.date(anchor.year, 1, 1), anchor
    if period == "last_month":
        end = _month_start(anchor) - dt.timedelta(days=1)
        return _month_start(end), end
    if period == "last_quarter":
        end = _quarter_start(anchor) - dt.timedelta(days=1)
        return _quarter_start(end), end
    if period == "last_year":
        return dt.date(anchor.year - 1, 1, 1), dt.date(anchor.year - 1, 12, 31)
    raise QueryError(f"Unknown period: {period}. Options: {', '.join(PERIODS)} or date_from/date_to")


CALENDAR_SHIFT = {"last_month": 1, "mtd": 1, "last_quarter": 3, "qtd": 3, "last_year": 12, "ytd": 12, "last_12_months": 12}


def previous_range(rng: tuple[dt.date, dt.date], compare: str, period: str | None = None) -> tuple[dt.date, dt.date]:
    """Comparison range. Calendar periods are compared with their calendar equivalent
    (last month → the full month before; month to date → the same days of the previous month); the rest, with a
    range of the same length right before."""
    start, end = rng
    if compare == "previous_year":
        return _add_months(start, -12), _add_months(end, -12)
    months = CALENDAR_SHIFT.get(period or "")
    if months:
        prev_start = _add_months(start, -months)
        if period in {"last_month", "last_quarter", "last_year"}:
            return prev_start, start - dt.timedelta(days=1)
        return prev_start, _add_months(end, -months)
    length = (end - start).days + 1
    return start - dt.timedelta(days=length), start - dt.timedelta(days=1)


# ---------------------------------------------------------------------- registry
class MetricStore:
    def __init__(self, sl: SemanticLayer) -> None:
        self.sl = sl
        self.metrics: dict[str, Metric] = {}
        if METRICS_FILE.exists():
            for m in json.loads(METRICS_FILE.read_text()):
                self.metrics[m["name"]] = Metric(**m)

    def _save(self) -> None:
        METRICS_FILE.write_text(json.dumps([m.model_dump() for m in self.metrics.values()], indent=2, ensure_ascii=False))

    def list(self) -> list[Metric]:
        return sorted(self.metrics.values(), key=lambda m: (not m.certified, m.model, m.label))

    def get(self, name: str) -> Metric:
        if name not in self.metrics:
            raise QueryError(f"There's no metric “{name}”. Available: {', '.join(self.metrics) or 'none'}")
        return self.metrics[name]

    def upsert(self, metric: Metric) -> Metric:
        self.sl.model(metric.model)
        tcol = metric.time_column or time_column(self.sl.model(metric.model))
        if metric.time_column and metric.time_column not in {c["name"] for c in self.sl.model(metric.model)["columns"]}:
            raise QueryError(f"The time column “{metric.time_column}” doesn't exist in {metric.model}")
        # Real validation: the expression must aggregate to a single row.
        probe = f"SELECT {metric.expression} AS v FROM {quote_ident(metric.model)}"
        try:
            result, _ = self.sl.query(probe, limit=2)
        except QueryError as e:
            raise QueryError(f"The expression isn't valid over {metric.model}: {e}") from e
        if result.row_count != 1:
            raise QueryError("The expression must be an aggregate (sum, count, avg, a ratio of sums…), not per row.")
        metric.time_column = metric.time_column or tcol
        metric.updated_at = dt.datetime.now().isoformat(timespec="seconds")
        self.metrics[metric.name] = metric
        self._save()
        return metric

    def delete(self, name: str) -> None:
        self.metrics.pop(name, None)
        self._save()

    def describe(self) -> str:
        if not self.metrics:
            return ""
        lines = ["\n### Official metrics (use them with `query_metrics`; they're the agreed definition)"]
        for m in self.list():
            badge = " ✔certified" if m.certified else ""
            lines.append(f"- {m.name} ({m.label}{badge}) = {m.expression} over {m.model} [{m.format}"
                         + (f" {m.currency}" if m.currency else "") + f"; time: {m.time_column or '—'}"
                         + ("; higher is better" if m.higher_is_better else "; lower is better") + "]"
                         + (f" — {m.description}" if m.description else ""))
        return "\n".join(lines)

    # ------------------------------------------------------------------ compilation
    def anchor(self, model: str, tcol: str) -> dt.date:
        result, _ = self.sl.query(f"SELECT CAST(max({quote_ident(tcol)}) AS DATE) AS a FROM {quote_ident(model)}", limit=1)
        value = result.rows[0][0] if result.rows else None
        return dt.date.fromisoformat(value[:10]) if value else dt.date.today()

    def compile(self, q: MetricQuery) -> dict[str, Any]:
        metrics = [self.get(n) for n in q.metrics]
        models = {m.model for m in metrics}
        if len(models) != 1:
            raise QueryError("All metrics in a query must come from the same model; run separate queries.")
        model = metrics[0].model
        cols = {c["name"] for c in self.sl.model(model)["columns"] if not c.get("relationship")}
        for d in q.group_by + [f.column for f in q.filters]:
            if d not in cols:
                raise QueryError(f"“{d}” isn't a column of {model}. Columns: {', '.join(sorted(cols))}")
        tcol = metrics[0].time_column or time_column(self.sl.model(model))
        needs_time = q.grain or q.period != "all" or q.date_from or q.date_to or q.compare
        if needs_time and not tcol:
            raise QueryError(f"{model} has no time column: it doesn't support periods or comparisons.")

        anchor = self.anchor(model, tcol) if needs_time else None
        if q.date_from or q.date_to:
            rng = (dt.date.fromisoformat(q.date_from[:10]) if q.date_from else dt.date(1900, 1, 1),
                   dt.date.fromisoformat(q.date_to[:10]) if q.date_to else anchor)
        else:
            rng = resolve_period(q.period, anchor) if anchor else None
        if q.compare and not q.grain and rng is None:
            raise QueryError("To compare without a grain, pick a period (e.g. last_30_days or ytd).")

        t = quote_ident(tcol) if tcol else None
        dims = [quote_ident(d) for d in q.group_by]
        where_dims = [f"CAST({quote_ident(f.column)} AS VARCHAR) IN ({', '.join(sql_literal(v) for v in f.values)})"
                      for f in q.filters if f.values]

        def base(r: tuple[dt.date, dt.date] | None, shift_year: bool = False) -> str:
            sel = []
            if q.grain:
                expr = f"{t} + INTERVAL 1 YEAR" if shift_year else t
                sel.append(f"CAST(date_trunc('{q.grain}', {expr}) AS DATE) AS period")
            sel += dims
            sel += [f"{m.expression} AS {quote_ident(m.name)}" for m in metrics]
            where = list(where_dims)
            if r:
                where.append(f"CAST({t} AS DATE) BETWEEN DATE '{r[0]}' AND DATE '{r[1]}'")
            group = (["period"] if q.grain else []) + dims
            return (f"SELECT {', '.join(sel)} FROM {quote_ident(model)}"
                    + (f" WHERE {' AND '.join(where)}" if where else "")
                    + (f" GROUP BY {', '.join(group)}" if group else ""))

        keys = (["period"] if q.grain else []) + dims
        prev_rng = None
        if not q.compare:
            sql = base(rng)
        elif q.grain and q.compare == "previous_period":
            # Period over period (e.g. month over month) with LAG per dimension.
            part = f"PARTITION BY {', '.join(dims)} " if dims else ""
            extra = ", ".join(
                f"lag({quote_ident(m.name)}) OVER ({part}ORDER BY period) AS {quote_ident(m.name + '_previous')}" for m in metrics)
            sql = f"WITH b AS ({base(rng)}) SELECT *, {extra} FROM b"
        else:
            if q.grain:  # year over year per period: the previous year is shifted +1 year to line up
                prev_rng = (_add_months(rng[0], -12), _add_months(rng[1], -12)) if rng else None
                prev_sql = base(prev_rng, shift_year=True)
            else:
                prev_rng = previous_range(rng, q.compare, None if (q.date_from or q.date_to) else q.period)
                prev_sql = base(prev_rng)
            cur_cols = ", ".join([f"cur.{k}" for k in keys] + [f"cur.{quote_ident(m.name)}" for m in metrics])
            prev_cols = ", ".join(f"prev.{quote_ident(m.name)} AS {quote_ident(m.name + '_previous')}" for m in metrics)
            join = f"LEFT JOIN prev USING ({', '.join(keys)})" if keys else "CROSS JOIN prev"
            sql = f"WITH cur AS ({base(rng)}), prev AS ({prev_sql}) SELECT {cur_cols}, {prev_cols} FROM cur {join}"
        if q.compare:
            var = ", ".join(
                f"({quote_ident(m.name)} - {quote_ident(m.name + '_previous')}) / nullif(abs({quote_ident(m.name + '_previous')}), 0) "
                f"AS {quote_ident(m.name + '_change')}" for m in metrics)
            sql = f"SELECT *, {var} FROM ({sql}) AS q"
        if q.order_by and not re.fullmatch(r'"?[A-Za-z_][\w ]*"?( (ASC|DESC))?', q.order_by.strip(), re.I):
            raise QueryError("order_by must be a column name, optionally with ASC/DESC")
        order = q.order_by or ("period" if q.grain else (quote_ident(metrics[0].name) + " DESC" if dims else None))
        if order:
            sql += f" ORDER BY {order}"
        if q.limit:
            sql += f" LIMIT {q.limit}"
        return {
            "sql": sql, "model": model, "anchor": str(anchor) if anchor else None,
            "range": [str(rng[0]), str(rng[1])] if rng else None,
            "previous_range": [str(prev_rng[0]), str(prev_rng[1])] if prev_rng else None,
            "period_label": PERIOD_LABELS.get(q.period, q.period) if not (q.date_from or q.date_to) else "custom range",
            "formats": {m.name: {"format": m.format, "currency": m.currency, "higher_is_better": m.higher_is_better} for m in metrics},
        }

    def run(self, q: MetricQuery) -> tuple[QueryResult, dict[str, Any]]:
        info = self.compile(q)
        result, _ = self.sl.query(info["sql"])
        return result, info
