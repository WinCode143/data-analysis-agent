"""Agent tools. They're pure functions over the workspace, reused by the chat agent
(agent.py) and by the MCP server (mcp_server.py)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import ValidationError

from . import config
from .charts import ChartSpec, Dashboard, DashboardInsights, charts, dashboards
from .metrics import PERIODS, Metric, MetricQuery, MetricStore
from .semantic import SemanticError, SemanticLayer
from .workspace import QueryError


class ToolError(Exception):
    pass


@dataclass
class ToolOutput:
    content: Any  # what the model sees (serialized to JSON)
    events: list[dict[str, Any]] = field(default_factory=list)  # what the interface sees


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], ToolOutput]

    def definition(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


class Toolbox:
    def __init__(self, semantic: SemanticLayer, metrics: MetricStore) -> None:
        self.sl = semantic
        self.metrics = metrics
        self.extra_tools: list[Tool] = []  # tools added by other modules (automations)
        self.tools: dict[str, Tool] = {t.name: t for t in self._build()}

    def definitions(self) -> list[dict[str, Any]]:
        return [t.definition() for t in self.tools.values()]

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def run(self, name: str, args: dict[str, Any]) -> ToolOutput:
        tool = self.tools.get(name)
        if tool is None:
            raise ToolError(f"Unknown tool: {name}")
        try:
            return tool.handler(args)
        except (QueryError, SemanticError, ValueError, KeyError) as e:
            raise ToolError(str(e)) from e

    # ------------------------------------------------------------------ handlers
    def describe_model(self, a: dict[str, Any]) -> ToolOutput:
        m = self.sl.model(a["model"])
        ref = m["tableReference"]
        profile = self.sl.ws.profile(ref["catalog"], ref["schema"], ref["table"])
        derived = [c for c in m["columns"] if c.get("isCalculated") or c.get("relationship")]
        return ToolOutput({"model": m["name"], "description": m.get("properties", {}).get("description"),
                           "profile": profile, "derived_fields": derived})

    def run_query(self, a: dict[str, Any]) -> ToolOutput:
        result, planned = self.sl.query(a["sql"])
        preview = result.as_records(config.MAX_ROWS_TO_MODEL)
        return ToolOutput(
            {"columns": result.columns, "row_count": result.row_count, "truncated": result.truncated,
             "rows": preview, "rows_shown": len(preview)},
            [{"type": "query", "sql": a["sql"], "columns": result.columns,
              "rows": result.rows[:500], "row_count": result.row_count, "truncated": result.truncated}],
        )

    def create_chart(self, a: dict[str, Any]) -> ToolOutput:
        if a.get("type") == "text":
            raise ToolError("For text, use add_text_block.")
        try:
            spec = ChartSpec(**a)
        except ValidationError as e:
            raise ToolError(f"Invalid specification: {e.errors(include_url=False)}") from e
        result, _ = self.sl.query(spec.sql)
        spec.check_columns([c["name"] for c in result.columns])
        if result.row_count == 0:
            raise ToolError("The query returned no rows; check the filters before charting.")
        charts.put(spec)
        return ToolOutput(
            {"chart_id": spec.id, "row_count": result.row_count, "truncated": result.truncated,
             "preview": result.as_records(10)},
            [{"type": "chart", "spec": spec.model_dump(), "data": {"columns": result.columns, "rows": result.rows}}],
        )

    def create_dashboard(self, a: dict[str, Any]) -> ToolOutput:
        specs = [charts.get(cid) for cid in a.get("chart_ids", [])]
        d = Dashboard(title=a["title"], description=a.get("description"), charts=specs)
        dashboards.put(d)
        return ToolOutput({"dashboard_id": d.id, "charts": len(specs)},
                          [{"type": "dashboard", "id": d.id, "title": d.title}])

    def update_dashboard(self, a: dict[str, Any]) -> ToolOutput:
        d: Dashboard = dashboards.get(a["dashboard_id"])
        if a.get("title"):
            d.title = a["title"]
        if a.get("description") is not None:
            d.description = a["description"]
        remove = set(a.get("remove_chart_ids", []))
        d.charts = [c for c in d.charts if c.id not in remove]
        present = {c.id for c in d.charts}
        d.charts += [charts.get(cid) for cid in a.get("add_chart_ids", []) if cid not in present]
        if a.get("order"):
            pos = {cid: i for i, cid in enumerate(a["order"])}
            d.charts.sort(key=lambda c: pos.get(c.id, len(pos)))
        for cid, w in (a.get("widths") or {}).items():
            for c in d.charts:
                if c.id == cid:
                    c.width = max(3, min(12, int(w)))
        dashboards.put(d)
        return ToolOutput({"dashboard_id": d.id, "charts": [c.id for c in d.charts]},
                          [{"type": "dashboard", "id": d.id, "title": d.title}])

    def add_text_block(self, a: dict[str, Any]) -> ToolOutput:
        d: Dashboard = dashboards.get(a["dashboard_id"])
        try:
            block = ChartSpec(title="", type="text", text=a["text"], width=a.get("width", 12))
        except ValidationError as e:
            raise ToolError(str(e)) from e
        pos = a.get("position")
        d.charts.insert(pos if isinstance(pos, int) and 0 <= pos <= len(d.charts) else len(d.charts), block)
        dashboards.put(d)
        return ToolOutput({"ok": True, "block_id": block.id, "dashboard_id": d.id},
                          [{"type": "dashboard", "id": d.id, "title": d.title}])

    def update_dashboard_chart(self, a: dict[str, Any]) -> ToolOutput:
        d: Dashboard = dashboards.get(a["dashboard_id"])
        for i, c in enumerate(d.charts):
            if c.id == a["chart_id"]:
                patch = {k: v for k, v in a.items() if k not in {"dashboard_id", "chart_id"} and v is not None}
                try:
                    d.charts[i] = ChartSpec.model_validate({**c.model_dump(), **patch})
                except ValidationError as e:
                    raise ToolError(f"Invalid change: {e.errors(include_url=False)}") from e
                dashboards.put(d)
                return ToolOutput({"ok": True, "chart": d.charts[i].model_dump(exclude={"sql"})},
                                  [{"type": "dashboard", "id": d.id, "title": d.title}])
        raise ToolError("That chart_id isn't on the dashboard (use list_dashboards).")

    def list_dashboards(self, a: dict[str, Any]) -> ToolOutput:
        return ToolOutput([
            {"id": d.id, "title": d.title, "description": d.description, "has_insights": d.insights is not None,
             "charts": [{"id": c.id, "title": c.title, "type": c.type, "sql": c.sql} for c in d.charts]}
            for d in dashboards.list()
        ])

    def write_dashboard_insights(self, a: dict[str, Any]) -> ToolOutput:
        d: Dashboard = dashboards.get(a["dashboard_id"])
        try:
            insights = DashboardInsights(**{k: v for k, v in a.items() if k != "dashboard_id"})
        except ValidationError as e:
            raise ToolError(f"Invalid conclusions: {e.errors(include_url=False)}") from e
        ids = {c.id for c in d.charts}
        for f in insights.findings:
            if f.chart_id and f.chart_id not in ids:
                f.chart_id = None
        d.insights = insights
        dashboards.put(d)
        return ToolOutput({"ok": True, "dashboard_id": d.id, "findings": len(insights.findings)},
                          [{"type": "insights", "dashboard_id": d.id, "title": d.title, "insights": insights.model_dump()}])

    def get_chart(self, a: dict[str, Any]) -> ToolOutput:
        return ToolOutput(charts.get(a["chart_id"]).model_dump())

    def edit_semantic_layer(self, a: dict[str, Any]) -> ToolOutput:
        action = a["action"]
        if action == "set_description":
            self.sl.set_description(a["model"], a["description"], a.get("column"))
        elif action == "add_calculated_field":
            self.sl.add_calculated_field(a["model"], a["name"], a["expression"], a.get("type", "double"), a.get("description", ""))
        elif action == "add_relationship":
            self.sl.add_relationship(a["model"], a["to_model"], a["join_type"], a["condition"], a.get("name"))
        elif action == "remove_field":
            self.sl.remove_field(a["model"], a["name"])
        else:
            raise ToolError(f"Unknown action: {action}")
        return ToolOutput({"ok": True, "semantic_layer": self.sl.describe()}, [{"type": "semantic_updated"}])

    def define_metric(self, a: dict[str, Any]) -> ToolOutput:
        try:
            metric = Metric(**a)
        except ValidationError as e:
            raise ToolError(f"Invalid metric: {e.errors(include_url=False)}") from e
        saved = self.metrics.upsert(metric)
        return ToolOutput({"ok": True, "metric": saved.model_dump()}, [{"type": "semantic_updated"}])

    def query_metrics(self, a: dict[str, Any]) -> ToolOutput:
        try:
            q = MetricQuery(**a)
        except ValidationError as e:
            raise ToolError(f"Invalid query: {e.errors(include_url=False)}") from e
        result, info = self.metrics.run(q)
        preview = result.as_records(config.MAX_ROWS_TO_MODEL)
        return ToolOutput(
            {"columns": result.columns, "rows": preview, "row_count": result.row_count, "sql": info["sql"],
             "range": info["range"], "previous_range": info["previous_range"],
             "last_data_date": info["anchor"], "period": info["period_label"]},
            [{"type": "query", "sql": info["sql"], "columns": result.columns, "rows": result.rows[:500],
              "row_count": result.row_count, "truncated": result.truncated}],
        )

    def save_query(self, a: dict[str, Any]) -> ToolOutput:
        from .sqlwork import SavedQuery, format_sql, queries
        self.sl.query(a["sql"], limit=1)  # only queries that work get saved
        q = SavedQuery(title=a["title"], sql=format_sql(a["sql"]), description=a.get("description", ""),
                       tags=a.get("tags") or [])
        queries.put(q)
        return ToolOutput({"ok": True, "id": q.id}, [{"type": "saved_query", "id": q.id, "title": q.title}])

    def list_saved_queries(self, a: dict[str, Any]) -> ToolOutput:
        from .sqlwork import queries
        return ToolOutput([{"id": q.id, "title": q.title, "description": q.description, "sql": q.sql}
                           for q in queries.list()])

    def run_python(self, a: dict[str, Any]) -> ToolOutput:
        with tempfile.TemporaryDirectory(prefix="insight_py_") as tmp:
            tmpdir = Path(tmp)
            if a.get("sql"):
                result, _ = self.sl.query(a["sql"], limit=1_000_000)
                names = [c["name"] for c in result.columns]
                table = pa.table({n: [r[i] for r in result.rows] for i, n in enumerate(names)})
                pq.write_table(table, tmpdir / "df.parquet")
            script = (
                "import pandas as pd, numpy as np\n"
                "from pathlib import Path\n"
                "df = pd.read_parquet('df.parquet') if Path('df.parquet').exists() else None\n"
                + a["code"]
            )
            (tmpdir / "main.py").write_text(script)
            try:
                proc = subprocess.run([sys.executable, "main.py"], cwd=tmpdir, capture_output=True, text=True,
                                      timeout=config.PYTHON_TIMEOUT_S)
            except subprocess.TimeoutExpired as e:
                raise ToolError(f"The code took longer than {config.PYTHON_TIMEOUT_S}s") from e
        out = {"stdout": proc.stdout[-10000:], "stderr": proc.stderr[-4000:], "exit_code": proc.returncode}
        return ToolOutput(out, [{"type": "python", "code": a["code"], **out}])

    # ------------------------------------------------------------------ definitions
    def _build(self) -> list[Tool]:
        chart_props = {
            "title": {"type": "string", "description": "Title that says what the chart shows"},
            "subtitle": {"type": "string", "description": "Units, period or filter applied"},
            "type": {"type": "string", "enum": ["bar", "hbar", "stacked_bar", "line", "area", "scatter", "heatmap", "kpi", "table"]},
            "sql": {"type": "string", "description": "SQL (DuckDB dialect) over the semantic models"},
            "x": {"type": "string", "description": "Categorical or time column for the X axis"},
            "y": {"type": "array", "items": {"type": "string"}, "description": "Measure columns. kpi: [value] or [value, previous_value]"},
            "series": {"type": "string", "description": "Column whose values form the series (lines/segments). In a heatmap: the Y axis"},
            "value_format": {"type": "string", "enum": ["number", "integer", "currency", "percent"]},
            "currency": {"type": "string", "description": "ISO currency code if value_format=currency, e.g. USD, EUR, MXN"},
            "insight": {"type": "string", "description": "Main conclusion in one sentence, based on the data"},
            "width": {"type": "integer", "description": "Width on a 12-column grid (3-12). kpi=3, regular chart=6, long time series=12"},
            "targets": {"type": "array", "description": "Targets/budget: a horizontal line (in a kpi, the goal to reach)",
                        "items": _obj({"value": {"type": "number"}, "label": {"type": "string"}}, ["value"])},
            "annotations": {"type": "array", "description": "Events on the X axis that explain changes",
                            "items": _obj({"x": {"type": "string", "description": "X axis value exactly as it is in the data"},
                                           "label": {"type": "string"}}, ["x", "label"])},
        }
        tools = [
            Tool("describe_model",
                 "Profiles a semantic model: row count, types, min/max, distinct values, % nulls, sample rows and "
                 "derived fields. Use it before querying a model you don't know.",
                 _obj({"model": {"type": "string"}}, ["model"]), self.describe_model),
            Tool("run_query",
                 "Runs a read-only SQL query (DuckDB dialect) over the semantic layer models. "
                 f"Returns the columns and up to {config.MAX_ROWS_TO_MODEL} rows. Aggregate in SQL instead of fetching raw data.",
                 _obj({"sql": {"type": "string"}}, ["sql"]), self.run_query),
            Tool("create_chart",
                 "Creates a chart from a query and shows it to the user. The SQL must return exactly "
                 "the columns used in x / y / series, already aggregated and sorted. Returns chart_id.",
                 _obj(chart_props, ["title", "type", "sql"]), self.create_chart),
            Tool("create_dashboard",
                 "Creates a dashboard from existing charts (in the given order). Put the KPIs first.",
                 _obj({"title": {"type": "string"}, "description": {"type": "string"},
                       "chart_ids": {"type": "array", "items": {"type": "string"}}}, ["title", "chart_ids"]),
                 self.create_dashboard),
            Tool("update_dashboard",
                 "Changes a dashboard: title, description, adding/removing charts, order and widths.",
                 _obj({"dashboard_id": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"},
                       "add_chart_ids": {"type": "array", "items": {"type": "string"}},
                       "remove_chart_ids": {"type": "array", "items": {"type": "string"}},
                       "order": {"type": "array", "items": {"type": "string"}, "description": "chart_ids in their final order"},
                       "widths": {"type": "object", "additionalProperties": {"type": "integer"},
                                  "description": "chart_id → width (3-12)"}}, ["dashboard_id"]),
                 self.update_dashboard),
            Tool("add_text_block",
                 "Adds a text block (markdown) to a dashboard: section titles ('## Sales'), context, "
                 "notes on method. position = index to insert it at (0 = top); at the end by default.",
                 _obj({"dashboard_id": {"type": "string"}, "text": {"type": "string"},
                       "width": {"type": "integer", "description": "3-12 (12 = full width)"},
                       "position": {"type": "integer"}}, ["dashboard_id", "text"]),
                 self.add_text_block),
            Tool("update_dashboard_chart",
                 "Changes a chart inside a dashboard: title, subtitle, type, width, conclusion (insight), "
                 "targets or annotations. To annotate an event on a time series, use the X axis value "
                 "exactly as it appears in the data.",
                 _obj({"dashboard_id": {"type": "string"}, "chart_id": {"type": "string"},
                       "title": {"type": "string"}, "subtitle": {"type": "string"}, "insight": {"type": "string"},
                       "type": chart_props["type"], "width": {"type": "integer"},
                       "text": {"type": "string", "description": "Text blocks only"},
                       "targets": chart_props["targets"], "annotations": chart_props["annotations"]},
                      ["dashboard_id", "chart_id"]),
                 self.update_dashboard_chart),
            Tool("list_dashboards", "Lists the existing dashboards, their charts (with SQL) and whether they have conclusions yet.",
                 _obj({}, []), self.list_dashboards),
            Tool("write_dashboard_insights",
                 "Saves the conclusions of a dashboard: executive summary, key findings and recommendations. "
                 "Use it AFTER checking every figure with queries. Findings must be specific, "
                 "quantified and actionable; link each one to the chart_id that backs it.",
                 _obj({"dashboard_id": {"type": "string"},
                       "summary": {"type": "string", "description": "Executive summary in 2-4 sentences, with the most important figures"},
                       "findings": {"type": "array", "description": "3-6 findings, from most to least important", "items": _obj({
                           "title": {"type": "string", "description": "The finding in one sentence with the key figure"},
                           "detail": {"type": "string", "description": "Cause, size and context in 1-3 sentences"},
                           "kind": {"type": "string", "enum": ["positive", "negative", "warning", "neutral"]},
                           "chart_id": {"type": "string"}}, ["title", "detail", "kind"])},
                       "recommendations": {"type": "array", "items": {"type": "string"},
                                           "description": "2-4 concrete actions that follow from the findings"},
                       "scope": {"type": "string", "description": "Period and filters analyzed, e.g. 'Oct 2024 – Aug 2026, all regions'"}},
                      ["dashboard_id", "summary", "findings", "recommendations"]),
                 self.write_dashboard_insights),
            Tool("get_chart", "Returns the full specification of a chart (including its SQL).",
                 _obj({"chart_id": {"type": "string"}}, ["chart_id"]), self.get_chart),
            Tool("query_metrics",
                 "Queries OFFICIAL metrics by name, with relative periods and comparisons. Prefer it over free-form "
                 "SQL when the metric exists. Periods relative to the latest available data: " + ", ".join(PERIODS) +
                 ". compare=previous_period (with grain: against the previous period, e.g. month over month; without grain: "
                 "a range of the same length right before) or previous_year (year over year). Returns <metric>, "
                 "<metric>_previous and <metric>_change (relative change, 0.12 = +12 %) and the SQL, reusable in create_chart.",
                 _obj({"metrics": {"type": "array", "items": {"type": "string"}, "description": "Names of metrics from the same model"},
                       "group_by": {"type": "array", "items": {"type": "string"}, "description": "Dimension columns of the model"},
                       "grain": {"type": "string", "enum": ["day", "week", "month", "quarter", "year"]},
                       "period": {"type": "string", "enum": PERIODS},
                       "date_from": {"type": "string", "description": "YYYY-MM-DD (alternative to period)"},
                       "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                       "compare": {"type": "string", "enum": ["previous_period", "previous_year"]},
                       "filters": {"type": "array", "items": _obj({"column": {"type": "string"},
                                                                   "values": {"type": "array", "items": {"type": "string"}}},
                                                                  ["column", "values"])},
                       "order_by": {"type": "string", "description": "Result column, optionally with DESC"},
                       "limit": {"type": "integer"}}, ["metrics"]),
                 self.query_metrics),
            Tool("define_metric",
                 "Defines or updates a reusable official metric (an AGGREGATE expression over a model), e.g. "
                 "conversion_rate = sum(conversions) / nullif(sum(clicks), 0). Use it when a business metric is "
                 "going to be used more than once. Don't set certified: a person decides that.",
                 _obj({"name": {"type": "string", "description": "snake_case"}, "label": {"type": "string"},
                       "model": {"type": "string"}, "expression": {"type": "string"},
                       "description": {"type": "string"},
                       "format": {"type": "string", "enum": ["number", "integer", "currency", "percent"]},
                       "currency": {"type": "string"}, "time_column": {"type": "string"},
                       "higher_is_better": {"type": "boolean"}},
                      ["name", "label", "model", "expression"]),
                 self.define_metric),
            Tool("save_query",
                 "Saves a SQL query in the user's query library (SQL editor) for reuse. "
                 "Use it when asked to 'save this query' or when a query is clearly reusable.",
                 _obj({"title": {"type": "string"}, "sql": {"type": "string"}, "description": {"type": "string"},
                       "tags": {"type": "array", "items": {"type": "string"}}}, ["title", "sql"]),
                 self.save_query),
            Tool("list_saved_queries", "Lists the queries saved by the user (title, description and SQL).",
                 _obj({}, []), self.list_saved_queries),
            Tool("edit_semantic_layer",
                 "Improves the semantic layer so future questions are more precise: describe models/columns, "
                 "add calculated fields (an SQL expression over the model's columns or `relationship.column`), add "
                 "relationships between models or remove derived fields. Every change is validated before it's saved.",
                 _obj({"action": {"type": "string", "enum": ["set_description", "add_calculated_field", "add_relationship", "remove_field"]},
                       "model": {"type": "string"},
                       "column": {"type": "string", "description": "set_description: column (leave out to describe the model)"},
                       "description": {"type": "string"},
                       "name": {"type": "string", "description": "Name of the calculated field / relationship / field to remove"},
                       "expression": {"type": "string", "description": "add_calculated_field: SQL expression"},
                       "type": {"type": "string", "description": "add_calculated_field: type (double, bigint, varchar, date...)"},
                       "to_model": {"type": "string", "description": "add_relationship: target model"},
                       "join_type": {"type": "string", "enum": ["ONE_TO_ONE", "ONE_TO_MANY", "MANY_TO_ONE", "MANY_TO_MANY"]},
                       "condition": {"type": "string", "description": "add_relationship: e.g. orders.customer_id = customers.id"}},
                      ["action", "model"]),
                 self.edit_semantic_layer),
        ]
        if config.ENABLE_PYTHON:
            tools.append(Tool(
                "run_python",
                "Runs Python (pandas, numpy) for analysis SQL doesn't handle well: statistics, correlations, "
                "simple forecasts, anomaly detection. If you pass `sql`, its result is in `df`. "
                "Print what you need to see; only stdout/stderr come back.",
                _obj({"code": {"type": "string"}, "sql": {"type": "string"}}, ["code"]), self.run_python))
        return tools


def to_model_content(content: Any) -> str:
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
