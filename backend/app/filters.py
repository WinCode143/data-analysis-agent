"""Global dashboard filters: they're injected into the WHERE of every SELECT that reads a semantic model.

A date filter applies to each model's time column; a dimension filter applies to every model that has
that column. Values go in as literals built with sqlglot (no string concatenation).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import sqlglot
from pydantic import BaseModel
from sqlglot import exp

from .semantic import SemanticLayer

DATE_TYPES = ("date", "timestamp", "timestamptz", "datetime")


class DimensionFilter(BaseModel):
    column: str
    values: list[str]


class DashboardFilters(BaseModel):
    date_from: str | None = None
    date_to: str | None = None
    dimensions: list[DimensionFilter] = []

    def is_empty(self) -> bool:
        return not (self.date_from or self.date_to or any(d.values for d in self.dimensions))


def time_column(model: dict[str, Any]) -> str | None:
    for c in model["columns"]:
        if not c.get("relationship") and not c.get("isCalculated") and str(c.get("type", "")).startswith(DATE_TYPES):
            return c["name"]
    return None


def models_in_sql(sql: str, sl: SemanticLayer) -> list[str]:
    names = set(sl.model_names())
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except sqlglot.errors.ParseError:
        return []
    return sorted({t.name for t in tree.find_all(exp.Table) if t.name in names})


def apply_filters(sql: str, filters: DashboardFilters, sl: SemanticLayer) -> str:
    if filters.is_empty():
        return sql
    tree = sqlglot.parse_one(sql, read="duckdb")
    names = set(sl.model_names())
    for select in list(tree.find_all(exp.Select)):
        conditions: list[exp.Expression] = []
        # Only tables whose closest SELECT is this one (subquery tables are handled in their own SELECT).
        for table in list(select.find_all(exp.Table)):
            conditions += _conditions_for(table, select, filters, sl, names)
        for cond in conditions:
            select.where(cond, copy=False)
    return tree.sql(dialect="duckdb")


def _conditions_for(table: exp.Table, select: exp.Select, filters: DashboardFilters, sl: SemanticLayer,
                    names: set[str]) -> list[exp.Expression]:
    if table.name not in names or table.find_ancestor(exp.Select) is not select:
        return []
    model = sl.model(table.name)
    qualifier = table.alias_or_name
    cols = {c["name"] for c in model["columns"] if not c.get("relationship")}
    out: list[exp.Expression] = []
    tcol = time_column(model)
    if tcol:
        col = exp.cast(exp.column(tcol, table=qualifier, quoted=True), "DATE")
        if filters.date_from:
            out.append(exp.GTE(this=col, expression=exp.cast(exp.Literal.string(filters.date_from[:10]), "DATE")))
        if filters.date_to:
            out.append(exp.LTE(this=col.copy(), expression=exp.cast(exp.Literal.string(filters.date_to[:10]), "DATE")))
    for dim in filters.dimensions:
        if dim.values and dim.column in cols:
            out.append(exp.In(
                this=exp.cast(exp.column(dim.column, table=qualifier, quoted=True), "VARCHAR"),
                expressions=[exp.Literal.string(v) for v in dim.values],
            ))
    return out


def filter_options(sqls: list[str], sl: SemanticLayer) -> dict[str, Any]:
    """Which filters make sense for a set of queries: dates and text dimensions."""
    models = sorted({m for s in sqls for m in models_in_sql(s, sl)})
    has_time = False
    dims: dict[str, list[str]] = {}
    for name in models:
        m = sl.model(name)
        has_time = has_time or time_column(m) is not None
        for c in m["columns"]:
            if c.get("relationship") or not str(c.get("type", "")).startswith(("varchar", "text", "char", "string")):
                continue
            dims.setdefault(c["name"], []).append(name)
    return {"models": models, "has_time": has_time,
            "dimensions": [{"column": k, "models": v} for k, v in sorted(dims.items())]}


class DrillError(Exception):
    pass


def _add_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    return dt.date(d.year + y, m + 1, 1)


def _resolve(expr: exp.Expression) -> exp.Expression:
    while isinstance(expr, (exp.Cast, exp.Paren, exp.Alias)):
        expr = expr.this
    return expr


def resolve_click(sql: str, column: str, value: Any, sl: SemanticLayer) -> dict[str, Any]:
    """Turns a click on a chart (result column + value) into a dashboard filter.

    - `date_trunc('month', order_date) AS month` + '2026-03-01' → the date range of March 2026.
    - `customer_country AS country` + 'Mexico' → dimension filter customer_country = Mexico.
    It looks for the definition in every SELECT (subqueries and CTEs included) to see through aliases.
    """
    if value is None:
        raise DrillError("There's no value at that point.")
    tree = sqlglot.parse_one(sql, read="duckdb")
    model_cols = {c["name"] for m in models_in_sql(sql, sl) for c in sl.model(m)["columns"] if not c.get("relationship")}
    dimension = None
    for select in tree.find_all(exp.Select):
        for proj in select.expressions:
            if proj.alias_or_name != column:
                continue
            e = _resolve(proj)
            if isinstance(e, (exp.TimestampTrunc, exp.DateTrunc)):
                unit = (e.args.get("unit").name if e.args.get("unit") is not None else "DAY").upper()
                start = dt.date.fromisoformat(str(value)[:10])
                end = {"DAY": start, "WEEK": start + dt.timedelta(days=6),
                       "MONTH": _add_months(start, 1) - dt.timedelta(days=1),
                       "QUARTER": _add_months(start, 3) - dt.timedelta(days=1),
                       "YEAR": dt.date(start.year, 12, 31)}.get(unit, start)
                return {"kind": "date", "date_from": str(start), "date_to": str(end)}
            if isinstance(e, exp.Column) and e.name in model_cols and dimension is None:
                dimension = e.name
    if dimension is None:
        raise DrillError(f"The dashboard can't be filtered by “{column}”: it isn't a column of the models.")
    s = str(value)
    if len(s) >= 10 and s[4:5] == "-" and s[7:8] == "-":  # a date column that isn't truncated → that day
        return {"kind": "date", "date_from": s[:10], "date_to": s[:10]}
    return {"kind": "dimension", "column": dimension, "value": s}
