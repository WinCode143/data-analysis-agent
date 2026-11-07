"""MCP server: exposes the suite to Claude Code, Claude Desktop or other agents.

It's mounted inside the API (same process, same DuckDB workspace) at /mcp:
    claude mcp add --transport http insight-suite http://localhost:8000/mcp/

"""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from . import config
from .schedule import Condition, Frequency
from .charts import Annotation, Target
from .dataprep import Step
from .metrics import DimFilter
from .semantic import SemanticLayer
from .tools import Toolbox


class FindingIn(BaseModel):
    title: str = Field(description="The finding in one sentence with the key figure")
    detail: str = Field(description="Cause, size and context in 1-3 sentences")
    kind: Literal["positive", "negative", "warning", "neutral"] = "neutral"
    chart_id: str | None = None


def build_mcp(semantic: SemanticLayer, toolbox: Toolbox) -> MCPServer:
    server = MCPServer(
        name="insight-suite",
        instructions=(
            "Data analysis suite. Check `semantic_layer` first; the SQL (DuckDB dialect) is written "
            "over the semantic models, not over physical tables. The charts and dashboards you create show up "
            "in the Insight Suite web interface."
        ),
    )
    desc = {name: t.description for name, t in toolbox.tools.items()}

    def call(name: str, args: dict[str, Any]) -> Any:
        return toolbox.run(name, {k: v for k, v in args.items() if v is not None}).content

    @server.tool(description="Describes the semantic layer: models, columns with their role, calculated fields and relationships.")
    def semantic_layer() -> str:
        return semantic.describe()

    @server.tool(description=desc["describe_model"])
    def describe_model(model: str) -> Any:
        return call("describe_model", {"model": model})

    @server.tool(description=desc["run_query"])
    def run_query(sql: str) -> Any:
        return call("run_query", {"sql": sql})

    @server.tool(description=desc["query_metrics"])
    def query_metrics(
        metrics: list[str], group_by: list[str] | None = None,
        grain: Literal["day", "week", "month", "quarter", "year"] | None = None,
        period: str | None = None, date_from: str | None = None, date_to: str | None = None,
        compare: Literal["previous_period", "previous_year"] | None = None,
        filters: list[DimFilter] | None = None, order_by: str | None = None, limit: int | None = None,
    ) -> Any:
        return call("query_metrics", dict(metrics=metrics, group_by=group_by, grain=grain, period=period,
                                          date_from=date_from, date_to=date_to, compare=compare,
                                          filters=[f.model_dump() for f in filters] if filters else None,
                                          order_by=order_by, limit=limit))

    @server.tool(description=desc["define_metric"])
    def define_metric(name: str, label: str, model: str, expression: str, description: str | None = None,
                      format: Literal["number", "integer", "currency", "percent"] | None = None,
                      currency: str | None = None, time_column: str | None = None,
                      higher_is_better: bool | None = None) -> Any:
        return call("define_metric", dict(name=name, label=label, model=model, expression=expression,
                                          description=description, format=format, currency=currency,
                                          time_column=time_column, higher_is_better=higher_is_better))

    @server.tool(description=desc["create_chart"] + " Types: bar, hbar, stacked_bar, line, area, scatter, heatmap, kpi, table.")
    def create_chart(
        title: str,
        type: Literal["bar", "hbar", "stacked_bar", "line", "area", "scatter", "heatmap", "kpi", "table"],
        sql: str,
        x: str | None = None,
        y: list[str] | None = None,
        series: str | None = None,
        value_format: Literal["number", "integer", "currency", "percent"] | None = None,
        currency: str | None = None,
        subtitle: str | None = None,
        insight: str | None = None,
        width: int | None = None,
        targets: list[Target] | None = None,
        annotations: list[Annotation] | None = None,
    ) -> Any:
        return call("create_chart", dict(title=title, type=type, sql=sql, x=x, y=y or [], series=series,
                                         value_format=value_format, currency=currency, subtitle=subtitle,
                                         insight=insight, width=width,
                                         targets=[t.model_dump() for t in targets] if targets else None,
                                         annotations=[a.model_dump() for a in annotations] if annotations else None))

    @server.tool(description=desc["add_text_block"])
    def add_text_block(dashboard_id: str, text: str, width: int | None = None, position: int | None = None) -> Any:
        return call("add_text_block", dict(dashboard_id=dashboard_id, text=text, width=width, position=position))

    @server.tool(description=desc["update_dashboard_chart"])
    def update_dashboard_chart(
        dashboard_id: str, chart_id: str, title: str | None = None, subtitle: str | None = None,
        insight: str | None = None,
        type: Literal["bar", "hbar", "stacked_bar", "line", "area", "scatter", "heatmap", "kpi", "table"] | None = None,
        width: int | None = None, text: str | None = None, targets: list[Target] | None = None,
        annotations: list[Annotation] | None = None,
    ) -> Any:
        return call("update_dashboard_chart", dict(
            dashboard_id=dashboard_id, chart_id=chart_id, title=title, subtitle=subtitle, insight=insight, type=type,
            width=width, text=text, targets=[t.model_dump() for t in targets] if targets is not None else None,
            annotations=[a.model_dump() for a in annotations] if annotations is not None else None))

    @server.tool(description=desc["create_dashboard"])
    def create_dashboard(title: str, chart_ids: list[str], description: str | None = None) -> Any:
        return call("create_dashboard", {"title": title, "chart_ids": chart_ids, "description": description})

    @server.tool(description=desc["update_dashboard"])
    def update_dashboard(dashboard_id: str, title: str | None = None, description: str | None = None,
                         add_chart_ids: list[str] | None = None, remove_chart_ids: list[str] | None = None,
                         order: list[str] | None = None, widths: dict[str, int] | None = None) -> Any:
        return call("update_dashboard", dict(dashboard_id=dashboard_id, title=title, description=description,
                                             add_chart_ids=add_chart_ids, remove_chart_ids=remove_chart_ids,
                                             order=order, widths=widths))

    @server.tool(description=desc["save_query"])
    def save_query(title: str, sql: str, description: str | None = None, tags: list[str] | None = None) -> Any:
        return call("save_query", {"title": title, "sql": sql, "description": description, "tags": tags})

    @server.tool(description=desc["list_saved_queries"])
    def list_saved_queries() -> Any:
        return call("list_saved_queries", {})

    @server.tool(description=desc["list_dashboards"])
    def list_dashboards() -> Any:
        return call("list_dashboards", {})

    @server.tool(description=desc["get_chart"])
    def get_chart(chart_id: str) -> Any:
        return call("get_chart", {"chart_id": chart_id})

    @server.tool(description=desc["write_dashboard_insights"])
    def write_dashboard_insights(dashboard_id: str, summary: str, findings: list[FindingIn],
                                 recommendations: list[str], scope: str | None = None) -> Any:
        return call("write_dashboard_insights", dict(
            dashboard_id=dashboard_id, summary=summary, findings=[f.model_dump() for f in findings],
            recommendations=recommendations, scope=scope))

    @server.tool(description=desc["edit_semantic_layer"])
    def edit_semantic_layer(
        action: Literal["set_description", "add_calculated_field", "add_relationship", "remove_field"],
        model: str, column: str | None = None, description: str | None = None, name: str | None = None,
        expression: str | None = None, type: str | None = None, to_model: str | None = None,
        join_type: Literal["ONE_TO_ONE", "ONE_TO_MANY", "MANY_TO_ONE", "MANY_TO_MANY"] | None = None,
        condition: str | None = None,
    ) -> Any:
        return call("edit_semantic_layer", dict(action=action, model=model, column=column, description=description,
                                                name=name, expression=expression, type=type, to_model=to_model,
                                                join_type=join_type, condition=condition))

    if "check_data_quality" in toolbox.tools:
        @server.tool(description=desc["check_data_quality"])
        def check_data_quality(model: str) -> Any:
            return call("check_data_quality", {"model": model})

        @server.tool(description=desc["preview_cleaning_step"])
        def preview_cleaning_step(model: str, step: Step) -> Any:
            return call("preview_cleaning_step", {"model": model, "step": step.model_dump(exclude={"id"}, exclude_none=True)})

        @server.tool(description=desc["add_cleaning_step"])
        def add_cleaning_step(model: str, step: Step) -> Any:
            return call("add_cleaning_step", {"model": model, "step": step.model_dump(exclude={"id"}, exclude_none=True)})

        @server.tool(description=desc["remove_cleaning_step"])
        def remove_cleaning_step(model: str, step_id: str) -> Any:
            return call("remove_cleaning_step", {"model": model, "step_id": step_id})

    if "schedule_report" in toolbox.tools:
        @server.tool(description=desc["schedule_report"])
        def schedule_report(name: str, dashboard_id: str, frequency: Frequency,
                            channels: list[Literal["app", "email", "slack", "webhook"]] | None = None,
                            recipients: list[str] | None = None, refresh_insights: bool | None = None) -> Any:
            return call("schedule_report", dict(name=name, dashboard_id=dashboard_id, frequency=frequency.model_dump(),
                                                channels=channels, recipients=recipients, refresh_insights=refresh_insights))

        @server.tool(description=desc["create_alert"])
        def create_alert(name: str, metric: str, period: str, condition: Condition, frequency: Frequency,
                         compare: Literal["previous_period", "previous_year"] | None = None,
                         filters: list[DimFilter] | None = None,
                         channels: list[Literal["app", "email", "slack", "webhook"]] | None = None,
                         recipients: list[str] | None = None, explain: bool | None = None) -> Any:
            return call("create_alert", dict(name=name, metric=metric, period=period, condition=condition.model_dump(),
                                             frequency=frequency.model_dump(), compare=compare,
                                             filters=[f.model_dump() for f in filters] if filters else None, channels=channels,
                                             recipients=recipients, explain=explain))

        @server.tool(description=desc["list_automations"])
        def list_automations() -> Any:
            return call("list_automations", {})

    if config.ENABLE_PYTHON:
        @server.tool(description=desc["run_python"])
        def run_python(code: str, sql: str | None = None) -> Any:
            return call("run_python", {"code": code, "sql": sql})

    return server
