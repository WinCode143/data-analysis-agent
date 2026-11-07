"""API HTTP de Insight Suite."""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config
from .agent import Agent, Conversation
from .automation import Automations, Notification
from .mcp_server import build_mcp
from .metrics import Metric, MetricQuery, MetricStore
from .providers import PRESETS, ProviderError, Settings, list_models, make_adapter
from .charts import ChartSpec, Dashboard, charts, dashboards
from .filters import DashboardFilters, DrillError, apply_filters, filter_options, resolve_click
from .suggest import suggestions
from . import dataprep, sqlwork
from .sqlwork import AssistIn, SavedQuery
from .semantic import SemanticError, SemanticLayer
from .tools import Toolbox
from .workspace import QueryError, Workspace, build_dsn

workspace = Workspace()
semantic = SemanticLayer(workspace)
metric_store = MetricStore(semantic)
semantic.extra_describers.append(metric_store.describe)
toolbox = Toolbox(semantic, metric_store)
settings = Settings()
agent = Agent(semantic, toolbox, settings)
automations = Automations(agent, toolbox, metric_store, semantic, settings)
prep = dataprep.Prep(workspace, semantic)
dataprep.register_tools(toolbox, prep, semantic, workspace)
mcp = build_mcp(semantic, toolbox)
mcp_app = mcp.streamable_http_app(streamable_http_path="/")


async def refresh_loop() -> None:
    """Periodic reload of URL / Google Sheets sources."""
    while True:
        await asyncio.sleep(60)
        for table in workspace.due_url_sources():
            try:
                await asyncio.to_thread(workspace.refresh_url_source, table)
                semantic.sync()
            except Exception as e:  # the source may be unavailable: it's retried on the next round
                for src in workspace.sources:
                    if src.get("table") == table:
                        src["error"] = str(e)[:300]


@asynccontextmanager
async def lifespan(_: FastAPI):
    scheduler = asyncio.create_task(automations.loop())
    refresher = asyncio.create_task(refresh_loop())
    async with mcp.session_manager.run():
        yield
    scheduler.cancel()
    refresher.cancel()


app = FastAPI(title="Insight Suite", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])
app.mount("/mcp", mcp_app)

ALLOWED_UPLOADS = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls", ".parquet", ".json", ".jsonl", ".ndjson"}
SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def bad_request(e: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------- status
@app.get("/api/status")
def status() -> dict[str, Any]:
    pid = settings.data["provider"]
    return {"provider": pid, "provider_name": PRESETS[pid].name, "model": settings.provider_cfg(pid)["model"],
            "configured": settings.is_configured(pid), "effort": settings.data["effort"],
            "python_enabled": config.ENABLE_PYTHON, "models": len(semantic.mdl["models"]),
            "dashboards": len(dashboards.list())}


# ---------------------------------------------------------------------- AI settings
@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return settings.public()


@app.patch("/api/settings")
def patch_settings(body: dict[str, Any]) -> dict[str, Any]:
    try:
        settings.update(body)
    except ValueError as e:
        raise bad_request(e) from e
    return settings.public()


@app.get("/api/providers/{pid}/models")
async def provider_models(pid: str) -> list[str]:
    if pid not in PRESETS:
        raise HTTPException(404, "Unknown provider")
    return await list_models(settings, pid)


class TestIn(BaseModel):
    model: str | None = None


@app.post("/api/providers/{pid}/test")
async def test_provider(pid: str, body: TestIn) -> dict[str, Any]:
    """Asks the model for a minimal answer using a tool, to check the key, the model and tool calling."""
    probe_tool = {"name": "ping", "description": "Answers the connection test.",
                  "input_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}}
    try:
        adapter = make_adapter(settings, pid, body.model)
        msgs = [adapter.user_message(["Connection test: call the ping tool with ok=true."])]
        result = None
        async for ev in adapter.turn("You are a connection checker.", msgs, [probe_tool]):
            if ev["type"] == "_turn":
                result = ev["result"]
    except ProviderError as e:
        return {"ok": False, "message": str(e)}
    uses_tools = bool(result and result.tool_calls)
    return {"ok": True, "tools": uses_tools,
            "message": "Connection OK" + ("" if uses_tools else " (but the model didn't use tools: it may fail as an analyst)")}


# ---------------------------------------------------------------------- data sources
@app.get("/api/sources")
def list_sources() -> list[dict[str, Any]]:
    return workspace.list_sources()


@app.post("/api/sources/upload")
async def upload(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    created: list[str] = []
    for f in files:
        suffix = Path(f.filename or "").suffix.lower()
        if suffix in SQLITE_SUFFIXES:  # a SQLite database is attached whole (read-only), not imported
            dest = config.UPLOADS_DIR / f"{uuid.uuid4().hex[:8]}{suffix}"
            with dest.open("wb") as out:
                shutil.copyfileobj(f.file, out)
            try:
                workspace.add_database("sqlite", Path(f.filename or "sqlite").stem, str(dest))
            except Exception as e:
                dest.unlink(missing_ok=True)
                raise bad_request(e) from e
            created.append(Path(f.filename or "").stem)
            continue
        if suffix not in ALLOWED_UPLOADS:
            raise HTTPException(400, f"Unsupported format: {f.filename}")
        dest = config.UPLOADS_DIR / f"{uuid.uuid4().hex[:8]}{suffix}"
        with dest.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        try:
            created += workspace.ingest_file(dest, f.filename or dest.name)
        except Exception as e:
            dest.unlink(missing_ok=True)
            raise bad_request(e) from e
    sync = semantic.sync()
    return {"tables": created, "models_added": sync["added"]}


class PostgresIn(BaseModel):
    name: str
    dsn: str


@app.post("/api/sources/postgres")
def add_postgres(body: PostgresIn) -> dict[str, Any]:
    try:
        src = workspace.add_postgres(body.name, body.dsn)
    except Exception as e:
        raise bad_request(e) from e
    sync = semantic.sync()
    return {"source": src, "models_added": sync["added"]}


class DatabaseIn(BaseModel):
    engine: str = "postgres"
    name: str | None = None
    dsn: str | None = None
    fields: dict[str, Any] | None = None
    schemas: list[str] | None = None

    def connection(self) -> str:
        dsn = self.dsn or (build_dsn(self.engine, self.fields) if self.fields else "")
        if not dsn:
            raise ValueError("Give the connection string or the connection details.")
        return dsn


@app.post("/api/sources/inspect")
def inspect_database(body: DatabaseIn) -> dict[str, Any]:
    """Tests the connection and lists the available schemas (saves nothing)."""
    try:
        return {"schemas": workspace.inspect(body.engine, body.connection())}
    except (QueryError, ValueError) as e:
        raise bad_request(e) from e


@app.post("/api/sources/database")
def add_database(body: DatabaseIn) -> dict[str, Any]:
    if not body.name:
        raise HTTPException(400, "Give the connection a name.")
    try:
        src = workspace.add_database(body.engine, body.name, body.connection(), body.schemas)
    except (QueryError, ValueError) as e:
        raise bad_request(e) from e
    sync = semantic.sync()
    return {"source": src, "models_added": sync["added"]}


class SchemasIn(BaseModel):
    schemas: list[str] | None = None


@app.put("/api/sources/{alias}/schemas")
def set_source_schemas(alias: str, body: SchemasIn) -> dict[str, Any]:
    try:
        workspace.set_schemas(alias, body.schemas)
    except KeyError as e:
        raise HTTPException(404, "Source not found") from e
    return semantic.sync()


@app.delete("/api/sources/{source_id}")
def remove_source(source_id: str) -> dict[str, Any]:
    try:
        workspace.remove_source(source_id)
    except KeyError as e:
        raise HTTPException(404, "Source not found") from e
    return semantic.sync()


# ---------------------------------------------------------------------- semantic layer
@app.get("/api/semantic")
def get_semantic() -> dict[str, Any]:
    return semantic.mdl


@app.post("/api/semantic/sync")
def sync_semantic() -> dict[str, Any]:
    return semantic.sync()


@app.get("/api/semantic/models/{name}/profile")
def profile_model(name: str) -> dict[str, Any]:
    try:
        m = semantic.model(name)
    except SemanticError as e:
        raise HTTPException(404, str(e)) from e
    ref = m["tableReference"]
    return workspace.profile(ref["catalog"], ref["schema"], ref["table"], sample_rows=20)


class DescriptionIn(BaseModel):
    description: str
    column: str | None = None


@app.put("/api/semantic/models/{name}/description")
def set_description(name: str, body: DescriptionIn) -> dict[str, Any]:
    try:
        semantic.set_description(name, body.description, body.column)
    except SemanticError as e:
        raise bad_request(e) from e
    return semantic.mdl


@app.post("/api/semantic/edit")
def edit_semantic(body: dict[str, Any]) -> dict[str, Any]:
    """Same operation as the agent's `edit_semantic_layer` tool."""
    try:
        return toolbox.run("edit_semantic_layer", body).content
    except Exception as e:
        raise bad_request(e) from e


# ---------------------------------------------------------------------- data quality & prep
@app.get("/api/quality/{model}")
def data_quality(model: str) -> dict[str, Any]:
    try:
        return dataprep.quality_report(model, semantic, workspace)
    except (SemanticError, QueryError) as e:
        raise bad_request(e) from e


@app.get("/api/prep/{model}")
def get_recipe(model: str) -> dict[str, Any]:
    try:
        return prep.recipe(model)
    except SemanticError as e:
        raise HTTPException(404, str(e)) from e


@app.post("/api/prep/{model}/preview")
def preview_step(model: str, step: dataprep.Step) -> dict[str, Any]:
    try:
        return prep.preview(model, step)
    except (QueryError, SemanticError) as e:
        raise bad_request(e) from e


@app.post("/api/prep/{model}/steps")
def add_step(model: str, step: dataprep.Step) -> dict[str, Any]:
    try:
        prep.add_step(model, step)
    except (QueryError, SemanticError) as e:
        raise bad_request(e) from e
    return prep.recipe(model)


@app.delete("/api/prep/{model}/steps/{step_id}")
def remove_step(model: str, step_id: str) -> dict[str, Any]:
    try:
        prep.remove_step(model, step_id)
    except KeyError as e:
        raise HTTPException(404, "Recipe not found") from e
    return prep.recipe(model)


class UrlSourceIn(BaseModel):
    name: str
    url: str
    refresh_minutes: int | None = None


@app.post("/api/sources/url")
def add_url_source(body: UrlSourceIn) -> dict[str, Any]:
    try:
        tables = workspace.add_url_source(body.name, body.url, body.refresh_minutes)
    except QueryError as e:
        raise bad_request(e) from e
    sync = semantic.sync()
    return {"tables": tables, "models_added": sync["added"]}


@app.post("/api/sources/{table}/refresh")
def refresh_source(table: str) -> dict[str, Any]:
    try:
        tables = workspace.refresh_url_source(table)
    except KeyError as e:
        raise HTTPException(404, "Source not found") from e
    except QueryError as e:
        raise bad_request(e) from e
    semantic.sync()
    return {"tables": tables}


# ---------------------------------------------------------------------- metrics
@app.get("/api/metrics")
def list_metrics() -> list[dict[str, Any]]:
    return [m.model_dump() for m in metric_store.list()]


@app.put("/api/metrics/{name}")
def upsert_metric(name: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return metric_store.upsert(Metric(**{**body, "name": name})).model_dump()
    except (QueryError, SemanticError, ValueError) as e:
        raise bad_request(e) from e


@app.delete("/api/metrics/{name}")
def delete_metric(name: str) -> dict[str, bool]:
    metric_store.delete(name)
    return {"ok": True}


@app.post("/api/metrics/query")
def query_metrics(body: MetricQuery) -> dict[str, Any]:
    try:
        result, info = metric_store.run(body)
    except (QueryError, SemanticError) as e:
        raise bad_request(e) from e
    return {"columns": result.columns, "rows": result.rows, "row_count": result.row_count, **info}


# ---------------------------------------------------------------------- automations & notifications
def _automation_out(a) -> dict[str, Any]:
    return {**a.model_dump(), "description": automations.describe(a), "running": a.id in automations.running}


@app.get("/api/automations")
def list_automations() -> dict[str, Any]:
    return {"items": [_automation_out(a) for a in automations.items.values()],
            "available_channels": automations.available_channels()}


@app.post("/api/automations")
def create_automation(body: dict[str, Any]) -> dict[str, Any]:
    try:
        return _automation_out(automations.create(body))
    except (ValueError, KeyError, QueryError) as e:
        raise bad_request(e) from e


@app.patch("/api/automations/{aid}")
def patch_automation(aid: str, body: dict[str, Any]) -> dict[str, Any]:
    if aid not in automations.items:
        raise HTTPException(404, "Automation not found")
    try:
        return _automation_out(automations.update(aid, body))
    except (ValueError, KeyError, QueryError) as e:
        raise bad_request(e) from e


@app.delete("/api/automations/{aid}")
def delete_automation(aid: str) -> dict[str, bool]:
    automations.delete(aid)
    return {"ok": True}


@app.post("/api/automations/{aid}/run")
async def run_automation(aid: str) -> dict[str, Any]:
    if aid not in automations.items:
        raise HTTPException(404, "Automation not found")
    if aid in automations.running:
        return {"started": False, "message": "It's already running"}
    asyncio.create_task(automations.execute(aid))
    return {"started": True}


@app.post("/api/automations/preview-alert")
def preview_alert(body: dict[str, Any]) -> dict[str, Any]:
    """Evaluates an alert without saving it: current value, previous value and whether it would fire today."""
    from .automation import Automation
    try:
        a = Automation(**{**body, "kind": "alert", "name": body.get("name") or "preview"})
        automations._validate(a)
        ev = automations.evaluate(a)
    except (ValueError, KeyError, QueryError) as e:
        raise bad_request(e) from e
    return {"value": ev["value"], "previous": ev["previous"], "var": ev["var"], "triggered": ev["triggered"],
            "range": ev["info"]["range"], "previous_range": ev["info"]["previous_range"]}


@app.get("/api/notifications")
def list_notifications() -> dict[str, Any]:
    items = [n.model_dump() for n in reversed(automations.notifications[-100:])]
    return {"items": items, "unread": sum(1 for n in automations.notifications if not n.read)}


@app.post("/api/notifications/read")
def read_notifications(body: dict[str, Any]) -> dict[str, bool]:
    ids = set(body.get("ids") or [])
    for n in automations.notifications:
        if not ids or n.id in ids:
            n.read = True
    automations._save_notifications()
    return {"ok": True}


@app.get("/api/channels")
def get_channels() -> dict[str, Any]:
    return {"config": automations.channels_config(), "available": automations.available_channels()}


@app.put("/api/channels")
def put_channels(body: dict[str, Any]) -> dict[str, Any]:
    return {"config": automations.save_channels(body), "available": automations.available_channels()}


@app.post("/api/channels/test")
async def test_channel(body: dict[str, Any]) -> dict[str, Any]:
    note = Notification(kind="test", title="Insight Suite test",
                        body="# Test\nIf you can read this, the channel is set up correctly. ✅")
    try:
        await asyncio.to_thread(automations.send, body.get("channel", ""), note, body.get("recipients") or [])
    except Exception as e:
        return {"ok": False, "message": f"Couldn't send it: {e}"}
    return {"ok": True, "message": "Sent. Check the channel."}


# ---------------------------------------------------------------------- query
class QueryIn(BaseModel):
    sql: str
    limit: int | None = None


@app.post("/api/query")
def run_query(body: QueryIn) -> dict[str, Any]:
    import time
    t0 = time.perf_counter()
    try:
        result, planned = semantic.query(body.sql, limit=min(body.limit or config.MAX_ROWS_TO_UI, 50_000))
    except QueryError as e:
        raise bad_request(e) from e
    return {"columns": result.columns, "rows": result.rows, "row_count": result.row_count,
            "truncated": result.truncated, "planned_sql": planned,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000)}


# ---------------------------------------------------------------------- SQL workbench
@app.post("/api/sql/format")
def sql_format(body: QueryIn) -> dict[str, str]:
    try:
        return {"sql": sqlwork.format_sql(body.sql)}
    except QueryError as e:
        raise bad_request(e) from e


@app.post("/api/sql/assist")
async def sql_assist(body: AssistIn) -> dict[str, Any]:
    try:
        return await sqlwork.assist(body, semantic, agent.complete)
    except (ProviderError, QueryError) as e:
        raise bad_request(e) from e


@app.get("/api/queries")
def list_queries() -> list[dict[str, Any]]:
    return [q.model_dump() for q in sqlwork.queries.list()]


@app.post("/api/queries")
def save_query(body: dict[str, Any]) -> dict[str, Any]:
    try:
        q = SavedQuery(**body)
        if body.get("id"):
            old = sqlwork.queries.get(body["id"])
            q.created_at = old.created_at
    except (KeyError, ValueError) as e:
        raise bad_request(e) from e
    sqlwork.queries.put(q)
    return q.model_dump()


@app.delete("/api/queries/{qid}")
def delete_query(qid: str) -> dict[str, bool]:
    sqlwork.queries.delete(qid)
    return {"ok": True}


def _xlsx(data: bytes, title: str) -> Response:
    from urllib.parse import quote
    name = quote(f"{title[:80] or 'export'}.xlsx")
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{name}"})


class ExportIn(BaseModel):
    sql: str
    title: str = "Query"
    chart_id: str | None = None


@app.post("/api/export/xlsx")
def export_xlsx(body: ExportIn) -> Response:
    try:
        if body.chart_id:  # chart: with the number formats from its specification
            from openpyxl import Workbook
            spec = charts.get(body.chart_id)
            result, _ = semantic.query(body.sql, limit=sqlwork.EXPORT_MAX_ROWS)
            wb = Workbook()
            sqlwork.write_sheet(wb, spec.title, result, set(), sqlwork.chart_formats(spec), note=spec.subtitle)
            return _xlsx(sqlwork.to_bytes(wb), spec.title)
        return _xlsx(sqlwork.query_workbook(semantic, body.sql, body.title), body.title)
    except (QueryError, KeyError) as e:
        raise bad_request(e) from e


@app.post("/api/dashboards/{dash_id}/export.xlsx")
def export_dashboard(dash_id: str, filters: DashboardFilters) -> Response:
    d = _load_dashboard(dash_id)
    data = sqlwork.dashboard_workbook(semantic, d, lambda sql: apply_filters(sql, filters, semantic))
    return _xlsx(data, d.title)


# ---------------------------------------------------------------------- charts
def chart_with_data(spec: ChartSpec, filters: DashboardFilters | None = None) -> dict[str, Any]:
    if spec.type == "text":
        return {"spec": spec.model_dump(), "data": None, "error": None}
    try:
        sql = apply_filters(spec.sql, filters, semantic) if filters else spec.sql
        result, _ = semantic.query(sql)
        data = {"columns": result.columns, "rows": result.rows}
        error = None
    except QueryError as e:
        data, error = None, str(e)
    except Exception as e:  # SQL the filter couldn't rewrite
        data, error = None, f"Couldn't apply the filter: {e}"
    return {"spec": spec.model_dump(), "data": data, "error": error}


@app.post("/api/charts")
def create_chart_endpoint(body: dict[str, Any]) -> dict[str, Any]:
    """Creates a chart from the SQL editor (same validation as the AI's tool)."""
    try:
        out = toolbox.run("create_chart", body)
    except Exception as e:
        raise bad_request(e) from e
    return {"chart_id": out.content["chart_id"], **out.events[0]}


@app.get("/api/charts/{chart_id}")
def get_chart(chart_id: str) -> dict[str, Any]:
    try:
        return chart_with_data(charts.get(chart_id))
    except KeyError as e:
        raise HTTPException(404, "Chart not found") from e


@app.get("/api/suggestions")
def get_suggestions() -> list[str]:
    return suggestions(semantic)


@app.get("/api/semantic/values")
def column_values(model: str, column: str) -> list[Any]:
    """Distinct values of a column (for the filters)."""
    try:
        semantic.model(model)
    except SemanticError as e:
        raise HTTPException(404, str(e)) from e
    col = '"' + column.replace('"', '""') + '"'
    try:
        result, _ = semantic.query(f'SELECT DISTINCT {col} AS v FROM "{model}" WHERE {col} IS NOT NULL ORDER BY 1 LIMIT 300')
    except QueryError as e:
        raise bad_request(e) from e
    return [r[0] for r in result.rows]


# ---------------------------------------------------------------------- dashboards
@app.get("/api/dashboards")
def list_dashboards() -> list[dict[str, Any]]:
    return [{"id": d.id, "title": d.title, "description": d.description, "charts": len(d.charts),
             "updated_at": d.updated_at, "types": [c.type for c in d.charts][:8]} for d in dashboards.list()]


def _load_dashboard(dash_id: str) -> Dashboard:
    try:
        return dashboards.get(dash_id)
    except KeyError as e:
        raise HTTPException(404, "Dashboard not found") from e


def render_dashboard(d: Dashboard, filters: DashboardFilters | None = None) -> dict[str, Any]:
    return {**d.model_dump(exclude={"charts"}), "charts": [chart_with_data(c, filters) for c in d.charts],
            "filter_options": filter_options([c.sql for c in d.charts if c.type != "text"], semantic)}


@app.get("/api/dashboards/{dash_id}")
def get_dashboard(dash_id: str) -> dict[str, Any]:
    return render_dashboard(_load_dashboard(dash_id))


@app.post("/api/dashboards/{dash_id}/render")
def render_with_filters(dash_id: str, filters: DashboardFilters) -> dict[str, Any]:
    return render_dashboard(_load_dashboard(dash_id), filters)


class ChartPatch(BaseModel):
    title: str | None = None
    subtitle: str | None = None
    type: str | None = None
    width: int | None = None
    text: str | None = None
    insight: str | None = None
    targets: list[dict[str, Any]] | None = None
    annotations: list[dict[str, Any]] | None = None


class DrillIn(BaseModel):
    chart_id: str
    column: str
    value: Any


@app.post("/api/dashboards/{dash_id}/drill")
def drill(dash_id: str, body: DrillIn) -> dict[str, Any]:
    """The filter that clicking a point of a dashboard chart stands for."""
    d = _load_dashboard(dash_id)
    spec = next((c for c in d.charts if c.id == body.chart_id), None)
    if spec is None:
        raise HTTPException(404, "Chart not found on the dashboard")
    try:
        return resolve_click(spec.sql, body.column, body.value, semantic)
    except (DrillError, ValueError) as e:
        raise bad_request(e) from e


class TextBlockIn(BaseModel):
    text: str
    width: int = 12
    position: int | None = None


@app.post("/api/dashboards/{dash_id}/text")
def add_text_block(dash_id: str, body: TextBlockIn) -> dict[str, Any]:
    try:
        out = toolbox.run("add_text_block", {"dashboard_id": dash_id, **body.model_dump(exclude_none=True)})
    except Exception as e:
        raise bad_request(e) from e
    return out.content


@app.patch("/api/dashboards/{dash_id}/charts/{chart_id}")
def patch_chart(dash_id: str, chart_id: str, body: ChartPatch) -> dict[str, Any]:
    d = _load_dashboard(dash_id)
    for i, c in enumerate(d.charts):
        if c.id == chart_id:
            try:
                d.charts[i] = ChartSpec.model_validate({**c.model_dump(), **body.model_dump(exclude_none=True)})
            except ValueError as e:
                raise bad_request(e) from e
            dashboards.put(d)
            return chart_with_data(d.charts[i])
    raise HTTPException(404, "Chart not found on the dashboard")


class DashboardIn(BaseModel):
    title: str
    description: str | None = None
    chart_ids: list[str] = []


@app.post("/api/dashboards")
def create_dashboard(body: DashboardIn) -> dict[str, Any]:
    try:
        d = Dashboard(title=body.title, description=body.description, charts=[charts.get(c) for c in body.chart_ids])
    except KeyError as e:
        raise HTTPException(404, f"Chart not found: {e}") from e
    dashboards.put(d)
    return d.model_dump()


class DashboardPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    add_chart_ids: list[str] = []
    remove_chart_ids: list[str] = []
    order: list[str] | None = None
    widths: dict[str, int] | None = None


@app.patch("/api/dashboards/{dash_id}")
def patch_dashboard(dash_id: str, body: DashboardPatch) -> dict[str, Any]:
    try:
        toolbox.run("update_dashboard", {"dashboard_id": dash_id, **body.model_dump(exclude_none=True)})
    except Exception as e:
        raise bad_request(e) from e
    return {"ok": True}


@app.delete("/api/dashboards/{dash_id}")
def delete_dashboard(dash_id: str) -> dict[str, bool]:
    dashboards.delete(dash_id)
    return {"ok": True}


# ---------------------------------------------------------------------- chat
class ChatIn(BaseModel):
    message: str
    conversation_id: str | None = None
    provider: str | None = None
    model: str | None = None


@app.post("/api/chat")
async def chat(body: ChatIn) -> StreamingResponse:
    notice = None
    if body.conversation_id:
        try:
            conv = Conversation.load(body.conversation_id)
        except KeyError as e:
            raise HTTPException(404, "Conversation not found") from e
        current = (conv.data["provider"], conv.data["model"])
        if body.provider in PRESETS and (body.provider, body.model or settings.provider_cfg(body.provider)["model"]) != current:
            target = (body.provider, body.model or settings.provider_cfg(body.provider)["model"])
            notice = f"Conversation switched to {PRESETS[target[0]].name} · {target[1]}."
        elif current[0] not in PRESETS or not settings.is_configured(current[0]):
            pid = settings.data["provider"]
            target = (pid, settings.provider_cfg(pid)["model"])
            old = PRESETS[current[0]].name if current[0] in PRESETS else current[0]
            notice = f"{old} isn't configured; this conversation continues with {PRESETS[pid].name} · {target[1]}."
        else:
            target = current
        if target != current:
            agent.switch_provider(conv, *target)
    else:
        pid = body.provider or settings.data["provider"]
        if pid not in PRESETS:
            raise HTTPException(400, "Unknown provider")
        conv = Conversation.new(pid, body.model or settings.provider_cfg(pid)["model"])

    async def events():
        if notice:
            conv.record_ui({"type": "notice", "message": notice})
            yield f"data: {json.dumps({'type': 'notice', 'message': notice}, ensure_ascii=False)}\n\n"
        async for ev in agent.run(conv, body.message):
            yield f"data: {json.dumps(ev, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/conversations")
def list_conversations() -> list[dict[str, Any]]:
    items = []
    for p in config.CONVERSATIONS_DIR.glob("*.json"):
        d = json.loads(p.read_text())
        items.append({"id": d["id"], "title": d["title"], "updated_at": d["updated_at"],
                      "provider": d.get("provider", "anthropic"), "model": d.get("model", "")})
    return sorted(items, key=lambda x: x["updated_at"], reverse=True)


@app.get("/api/conversations/{conv_id}")
def get_conversation(conv_id: str) -> dict[str, Any]:
    try:
        c = Conversation.load(conv_id)
    except KeyError as e:
        raise HTTPException(404, "Conversation not found") from e
    return {"id": c.id, "title": c.data["title"], "ui": c.data["ui"], "provider": c.data["provider"],
            "model": c.data["model"], "usage": c.data["usage"]}


@app.delete("/api/conversations/{conv_id}")
def delete_conversation(conv_id: str) -> dict[str, bool]:
    if conv_id.isalnum():
        (config.CONVERSATIONS_DIR / f"{conv_id}.json").unlink(missing_ok=True)
    return {"ok": True}


# ---------------------------------------------------------------------- built frontend (production)
_dist = config.ROOT_DIR / "frontend" / "dist"
if _dist.exists():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        return FileResponse(_dist / "index.html")
