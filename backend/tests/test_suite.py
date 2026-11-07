import copy
import json
from pathlib import Path

import httpx2
import pytest
from anthropic.types.beta import BetaMessage
from fastapi.testclient import TestClient

from app.main import app, semantic, toolbox

DEMO = Path(__file__).resolve().parents[2] / "demo"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        for f in ["sales.csv", "products.xlsx"]:
            r = c.post("/api/sources/upload", files={"files": (f, (DEMO / f).read_bytes())})
            assert r.status_code == 200, r.text
        yield c


def test_semantic_models_created(client):
    assert {"sales", "products"} <= set(semantic.model_names())


def test_query_and_read_only(client):
    r = client.post("/api/query", json={"sql": "SELECT channel, count(*) n FROM sales GROUP BY 1"})
    assert r.status_code == 200 and len(r.json()["rows"]) == 3
    for bad in ["DELETE FROM sales", "SELECT 1; SELECT 2", "CREATE TABLE x AS SELECT 1"]:
        assert client.post("/api/query", json={"sql": bad}).status_code == 400


def test_relationship_and_calculated_field(client):
    toolbox.run("edit_semantic_layer", {"action": "add_relationship", "model": "sales", "to_model": "products",
                                        "join_type": "MANY_TO_ONE", "condition": "sales.product_id = products.product_id"})
    toolbox.run("edit_semantic_layer", {"action": "add_calculated_field", "model": "sales", "name": "category",
                                        "expression": "products.category", "type": "varchar"})
    rows = client.post("/api/query", json={"sql": "SELECT DISTINCT category FROM sales ORDER BY 1"}).json()["rows"]
    assert [r[0] for r in rows] == ["Electronics", "Fashion", "Home", "Sports"]


def test_chart_validation(client):
    from app.tools import ToolError
    with pytest.raises(ToolError):
        toolbox.run("create_chart", {"title": "x", "type": "line", "sql": "SELECT channel FROM sales"})
    with pytest.raises(ToolError):  # column missing from the result
        toolbox.run("create_chart", {"title": "x", "type": "bar", "sql": "SELECT channel, count(*) n FROM sales GROUP BY 1",
                                     "x": "channel", "y": ["total"]})


def _msg(content, stop_reason):
    return BetaMessage.model_validate({
        "id": "msg_test", "type": "message", "role": "assistant", "model": "test", "content": content,
        "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    })


class _FakeStream:
    def __init__(self, message):
        self.message = message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        async def gen():
            if False:
                yield None
        return gen()

    async def get_final_message(self):
        return self.message


class _FakeClient:
    """Simulates two Claude turns: it asks for a chart and then answers with text."""

    def __init__(self):
        self.calls = []
        self.beta = self
        self.messages = self

    def stream(self, **kwargs):
        self.calls.append({**kwargs, "messages": copy.deepcopy(kwargs["messages"])})
        if len(self.calls) == 1:
            return _FakeStream(_msg([{"type": "tool_use", "id": "toolu_1", "name": "create_chart", "input": {
                "title": "Orders by channel", "type": "bar", "x": "channel", "y": ["orders"],
                "sql": "SELECT channel, count(*) AS orders FROM sales GROUP BY 1 ORDER BY 2 DESC"}}], "tool_use"))
        return _FakeStream(_msg([{"type": "text", "text": "Online leads."}], "end_turn"))


def _chat(client, body):
    with client.stream("POST", "/api/chat", json=body) as r:
        return [json.loads(l[6:]) for l in r.iter_lines() if l.startswith("data: ")]


def test_agent_loop_anthropic(client, monkeypatch):
    fake = _FakeClient()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr("app.providers.anthropic.AsyncAnthropic", lambda **kw: fake)
    events = _chat(client, {"message": "orders by channel", "provider": "anthropic", "model": "claude-opus-5"})
    types = [e["type"] for e in events]
    assert types[0] == "conversation" and types[-1] == "done"
    assert "chart" in types and "error" not in types, events
    # The second request carries the tool result in a single user message.
    last_user = fake.calls[1]["messages"][-1]
    assert last_user["role"] == "user" and last_user["content"][0]["type"] == "tool_result"
    assert fake.calls[0]["fallbacks"] == "default"
    # The semantic layer travels in the first user message, not in the system prompt.
    assert "<semantic_layer>" in fake.calls[0]["messages"][0]["content"][0]["text"]
    saved = client.get(f"/api/conversations/{events[0]['id']}").json()
    assert any(u["type"] == "chart" for u in saved["ui"]) and saved["provider"] == "anthropic"


def _sse(chunks):
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())


def _chunk(delta, finish=None):
    return {"id": "c1", "object": "chat.completion.chunk", "created": 0, "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def test_agent_loop_openai_compatible(client, monkeypatch):
    """Ollama/Gemini/OpenRouter use the same adapter: we test with a simulated OpenAI server."""
    requests = []

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            args = json.dumps({"sql": "SELECT region, count(*) AS n FROM sales GROUP BY 1"})
            return _sse([
                _chunk({"role": "assistant", "reasoning_content": "Thinking…"}),
                _chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                                        "function": {"name": "run_query", "arguments": args[:20]}}]}),
                _chunk({"tool_calls": [{"index": 0, "function": {"arguments": args[20:]}}]}),
                _chunk({}, "tool_calls"),
            ])
        return _sse([_chunk({"content": "Central leads."}), _chunk({}, "stop")])

    from app.main import agent, settings
    settings.update({"providers": {"ollama": {"model": "qwen-test"}}})
    agent.http_client_override = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    try:
        events = _chat(client, {"message": "orders by region", "provider": "ollama"})
    finally:
        agent.http_client_override = None
    types = [e["type"] for e in events]
    assert "error" not in types, events
    assert {"thinking", "tool_start", "query", "tool_end", "text"} <= set(types)
    second = requests[1]["messages"]
    assert second[0]["role"] == "system"
    assert second[-2]["tool_calls"][0]["function"]["name"] == "run_query"
    assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "call_1"
    # Ollama doesn't accept additionalProperties in the schemas: they get removed.
    assert "additionalProperties" not in json.dumps(requests[0]["tools"])


def test_missing_key_is_reported(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    events = _chat(client, {"message": "hello", "provider": "openai"})
    assert any(e["type"] == "error" and "key" in e["message"].lower() for e in events)


def test_dashboard_filters(client):
    from app.filters import DashboardFilters, apply_filters
    sql = "SELECT channel, count(*) AS n FROM sales v GROUP BY 1"
    f = DashboardFilters(date_from="2026-01-01", date_to="2026-01-31", dimensions=[{"column": "region", "values": ["North"]}])
    filtered = apply_filters(sql, f, semantic)
    assert "2026-01-01" in filtered and "'North'" in filtered
    total = sum(r[1] for r in client.post("/api/query", json={"sql": sql}).json()["rows"])
    part = sum(r[1] for r in client.post("/api/query", json={"sql": filtered}).json()["rows"])
    assert 0 < part < total
    # Injection: the value stays a literal, not SQL.
    evil = apply_filters(sql, DashboardFilters(dimensions=[{"column": "region", "values": ["x') OR 1=1 --"]}]), semantic)
    assert client.post("/api/query", json={"sql": evil}).json()["rows"] == []
    d = client.post("/api/dashboards", json={"title": "F", "chart_ids": []}).json()
    r = client.post(f"/api/dashboards/{d['id']}/render", json={"date_from": "2026-01-01"})
    assert r.status_code == 200 and "filter_options" in r.json()


def test_suggestions(client):
    s = client.get("/api/suggestions").json()
    assert 1 <= len(s) <= 4 and all(isinstance(x, str) for x in s)


def test_dashboard_insights(client):
    chart = toolbox.run("create_chart", {"title": "Orders by channel", "type": "bar", "x": "channel", "y": ["n"],
                                         "sql": "SELECT channel, count(*) AS n FROM sales GROUP BY 1 ORDER BY 2 DESC"}).content
    d = toolbox.run("create_dashboard", {"title": "Channels", "chart_ids": [chart["chart_id"]]}).content
    out = toolbox.run("write_dashboard_insights", {
        "dashboard_id": d["dashboard_id"], "summary": "Online leads.",
        "findings": [{"title": "Online is 50 %", "detail": "By volume.", "kind": "positive", "chart_id": chart["chart_id"]},
                     {"title": "X", "detail": "Y", "kind": "warning", "chart_id": "missing"}],
        "recommendations": ["Invest in online"]})
    assert out.events[0]["type"] == "insights"
    saved = client.get(f"/api/dashboards/{d['dashboard_id']}").json()["insights"]
    assert saved["findings"][0]["chart_id"] == chart["chart_id"] and saved["findings"][1]["chart_id"] is None
    from app.tools import ToolError
    with pytest.raises(ToolError):
        toolbox.run("write_dashboard_insights", {"dashboard_id": d["dashboard_id"], "summary": "s",
                                                 "findings": [{"title": "t", "detail": "d", "kind": "good"}], "recommendations": []})


def test_semantic_roles():
    text = semantic.describe()
    assert "[time" in text and "[identifier]" in text and "low cardinality" in text


def test_unconfigured_conversation_switches_provider(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    first = _chat(client, {"message": "hello", "provider": "openai"})
    conv_id = first[0]["id"]
    from app.main import settings
    settings.update({"provider": "ollama", "providers": {"ollama": {"model": "qwen-test"}}})
    from app.main import agent

    def handler(request):
        body = json.loads(request.content)
        assert "<previous_conversation>" in body["messages"][1]["content"]
        return _sse([_chunk({"content": "Hello again."}), _chunk({}, "stop")])

    agent.http_client_override = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    try:
        events = _chat(client, {"message": "once more", "conversation_id": conv_id})
    finally:
        agent.http_client_override = None
    assert events[0]["type"] == "notice" and "error" not in [e["type"] for e in events]
    saved = client.get(f"/api/conversations/{conv_id}").json()
    assert saved["provider"] == "ollama"


def test_vault_and_dsn_helpers():
    from app import vault
    from app.workspace import build_dsn, mask_dsn, normalize_dsn
    enc = vault.encrypt("s3cr3t")
    assert enc.startswith(vault.PREFIX) and "s3cr3t" not in enc and vault.decrypt(enc) == "s3cr3t"
    assert vault.decrypt("plain-text") == "plain-text"
    pg = build_dsn("postgres", {"host": "h", "user": "u", "password": "p@ss word", "database": "db", "ssl": True})
    assert pg == "postgresql://u:p%40ss%20word@h:5432/db?sslmode=require"
    assert normalize_dsn("mysql", "mysql://u:pw@h:3307/shop") == "host=h port=3307 user=u passwd=pw db=shop"
    assert "pw" not in mask_dsn("host=h passwd=pw db=x") and "pw" not in mask_dsn("postgresql://u:pw@h/db")


def test_sqlite_upload_and_schema_filter(client, tmp_path):
    import sqlite3
    db = tmp_path / "inventory.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE stock (sku TEXT, warehouse TEXT, units INT)")
    con.executemany("INSERT INTO stock VALUES (?,?,?)", [("A", "North", 3), ("B", "South", 7)])
    con.commit()
    con.close()
    r = client.post("/api/sources/upload", files={"files": ("inventory.sqlite", db.read_bytes())})
    assert r.status_code == 200, r.text
    assert "inventory_stock" in semantic.model_names() or "inventory" in " ".join(semantic.model_names())
    name = next(n for n in semantic.model_names() if "stock" in n)
    rows = client.post("/api/query", json={"sql": f'SELECT sum(units) FROM "{name}"'}).json()["rows"]
    assert rows == [[10]]
    src = next(s for s in client.get("/api/sources").json() if s["type"] == "sqlite")
    assert src["available_schemas"][0]["tables"] == 1
    # Filtering to a schema that doesn't exist removes the model.
    client.put(f"/api/sources/{src['alias']}/schemas", json={"schemas": ["other"]})
    assert name not in semantic.model_names()
    client.put(f"/api/sources/{src['alias']}/schemas", json={"schemas": None})
    assert any("stock" in n for n in semantic.model_names())
    # Passwords/connection strings are never stored in plaintext on disk.
    from app import config
    assert all(not s.get("dsn") or s["dsn"].startswith("enc:v1:") for s in json.loads(config.SOURCES_FILE.read_text()))


def test_mysql_real_if_available(client):
    """Uses the demo MySQL (docker compose up -d demo-mysql); skipped if it isn't running."""
    import socket
    try:
        socket.create_connection(("127.0.0.1", 3316), timeout=1).close()
    except OSError:
        pytest.skip("demo MySQL not available")
    fields = {"host": "127.0.0.1", "port": 3316, "user": "demo", "password": "demo", "database": "marketing"}
    r = client.post("/api/sources/inspect", json={"engine": "mysql", "fields": fields})
    assert r.status_code == 200, r.text
    assert any(s["schema"] == "marketing" for s in r.json()["schemas"])
    r = client.post("/api/sources/database", json={"engine": "mysql", "name": "marketing", "fields": fields, "schemas": ["marketing"]})
    assert r.status_code == 200, r.text
    assert "marketing_campaigns" in semantic.model_names()
    rows = client.post("/api/query", json={"sql": "SELECT count(*) FROM marketing_campaigns"}).json()["rows"]
    assert rows == [[92]]
    bad = client.post("/api/sources/inspect", json={"engine": "mysql", "fields": {**fields, "password": "wrong"}})
    assert bad.status_code == 400 and "connect" in bad.json()["detail"]


def test_connection_errors_never_leak_password(client):
    for body in [{"engine": "postgres", "dsn": "postgresql://demo:TOPSECRET@127.0.0.1:1/nope"},
                 {"engine": "mysql", "fields": {"host": "127.0.0.1", "port": 1, "user": "u", "password": "TOPSECRET", "database": "x"}},
                 {"engine": "postgres", "fields": {"host": "127.0.0.1", "port": 1, "user": "u", "password": "p@ss TOPSECRET", "database": "x"}}]:
        r = client.post("/api/sources/inspect", json=body)
        assert r.status_code == 400 and "TOPSECRET" not in r.text, r.text
        assert "can't be reached" in r.json()["detail"]


def test_metrics_periods_and_comparisons(client):
    import datetime as dt
    from app.metrics import resolve_period
    a = dt.date(2026, 8, 31)
    assert resolve_period("ytd", a) == (dt.date(2026, 1, 1), a)
    assert resolve_period("last_month", a) == (dt.date(2026, 7, 1), dt.date(2026, 7, 31))
    assert resolve_period("last_12_months", a) == (dt.date(2025, 9, 1), a)
    assert resolve_period("last_quarter", a) == (dt.date(2026, 4, 1), dt.date(2026, 6, 30))

    r = client.put("/api/metrics/orders", json={"label": "Orders", "model": "sales", "expression": "count(DISTINCT order_id)", "format": "integer"})
    assert r.status_code == 200, r.text
    # A per-row expression isn't a metric.
    assert client.put("/api/metrics/bad", json={"label": "x", "model": "sales", "expression": "quantity"}).status_code == 400

    total = client.post("/api/metrics/query", json={"metrics": ["orders"]}).json()["rows"][0][0]
    ytd = client.post("/api/metrics/query", json={"metrics": ["orders"], "period": "ytd", "compare": "previous_year"}).json()
    names = [c["name"] for c in ytd["columns"]]
    assert names == ["orders", "orders_previous", "orders_change"] and ytd["range"][0].endswith("-01-01")
    cur, prev, var = ytd["rows"][0]
    assert 0 < cur < total and prev > 0 and abs(var - (cur - prev) / prev) < 1e-9

    mom = client.post("/api/metrics/query", json={"metrics": ["orders"], "grain": "month", "group_by": ["channel"],
                                                   "compare": "previous_period", "filters": [{"column": "channel", "values": ["Online"]}]}).json()
    assert {r[1] for r in mom["rows"]} == {"Online"} and mom["rows"][0][3] is None and mom["rows"][1][3] == mom["rows"][0][2]
    yoy = client.post("/api/metrics/query", json={"metrics": ["orders"], "grain": "month", "period": "last_12_months", "compare": "previous_year"}).json()
    # Sep 2025 has no previous year (the data starts in Oct 2024); the rest do.
    assert len(yoy["rows"]) == 12 and yoy["rows"][0][2] is None and all(r[2] for r in yoy["rows"][1:])
    assert "orders" in semantic.describe()  # the catalog reaches the agent prompt
    bad = client.post("/api/metrics/query", json={"metrics": ["orders"], "compare": "previous_period"})
    assert bad.status_code == 400


def test_frequency_next_run():
    import datetime as dt
    from app.schedule import Frequency
    now = dt.datetime(2026, 9, 27, 10, 30)  # Sunday
    assert Frequency(every="day", at="08:00").next_after(now) == dt.datetime(2026, 9, 28, 8, 0)
    assert Frequency(every="day", at="11:00").next_after(now) == dt.datetime(2026, 9, 27, 11, 0)
    assert Frequency(every="week", at="09:00", weekday=0).next_after(now) == dt.datetime(2026, 9, 28, 9, 0)
    assert Frequency(every="month", at="07:00", day=1).next_after(now) == dt.datetime(2026, 10, 1, 7, 0)
    assert Frequency(every="hour", at="00:15").next_after(now) == dt.datetime(2026, 9, 27, 11, 15)


def test_alerts_reports_and_delivery(client):
    import asyncio
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from app.main import automations

    received = []

    class Hook(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            received.append((self.path, json.loads(self.rfile.read(int(self.headers["content-length"])))))
            self.send_response(200)
            self.end_headers()

    srv = HTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    r = client.put("/api/channels", json={"slack_webhook": base + "/slack", "webhook_url": base + "/hook"})
    assert set(r.json()["available"]) == {"app", "slack", "webhook"} and r.json()["config"]["slack_webhook"].startswith("••••")
    assert client.post("/api/channels/test", json={"channel": "slack"}).json()["ok"]

    client.put("/api/metrics/orders", json={"label": "Orders", "model": "sales", "expression": "count(DISTINCT order_id)", "format": "integer"})
    # An alert that always fires (change > -1000 %) and one that never does (< -1000 %).
    base_alert = {"metric": "orders", "period": "last_30_days", "compare": "previous_period", "explain": False,
                  "frequency": {"every": "day", "at": "08:00"}, "channels": ["app", "webhook"]}
    fire = client.post("/api/automations", json={**base_alert, "kind": "alert", "name": "Always",
                                                  "condition": {"type": "change_pct", "op": ">", "threshold": -1000}}).json()
    quiet = client.post("/api/automations", json={**base_alert, "kind": "alert", "name": "Never",
                                                   "condition": {"type": "change_pct", "op": "<", "threshold": -1000}}).json()
    assert fire["next_run"] and "Alert if" in fire["description"]
    prev = client.post("/api/automations/preview-alert", json={**base_alert, "condition": {"type": "value", "op": ">", "threshold": 0}}).json()
    assert prev["triggered"] and prev["value"] > 0

    n = asyncio.run(automations.execute(fire["id"]))
    assert n and n.delivered == {"app": "ok", "webhook": "ok"} and "Orders" in n.body
    assert asyncio.run(automations.execute(quiet["id"])) is None
    assert automations.items[quiet["id"]].last_status == "ok" and automations.items[fire["id"]].last_status == "triggered"

    chart = toolbox.run("create_chart", {"title": "Total orders", "type": "kpi", "y": ["n"], "sql": "SELECT count(*) AS n FROM sales"}).content
    d = toolbox.run("create_dashboard", {"title": "Weekly", "chart_ids": [chart["chart_id"]]}).content
    rep = client.post("/api/automations", json={"kind": "report", "name": "Mondays", "dashboard_id": d["dashboard_id"],
                                                 "refresh_insights": False, "channels": ["slack"],
                                                 "frequency": {"every": "week", "weekday": 0, "at": "09:00"}}).json()
    assert rep["channels"][0] == "app"  # the in-app inbox is always included
    note = asyncio.run(automations.execute(rep["id"]))
    assert note.delivered["slack"] == "ok" and "Total orders" in note.body
    paths = [p for p, _ in received]
    assert "/hook" in paths and paths.count("/slack") == 2
    assert "*Weekly*" in received[-1][1]["text"]  # markdown converted to Slack's format
    inbox = client.get("/api/notifications").json()
    assert inbox["unread"] >= 2
    client.post("/api/notifications/read", json={})
    assert client.get("/api/notifications").json()["unread"] == 0
    # Validation
    assert client.post("/api/automations", json={**base_alert, "kind": "alert", "name": "x", "metric": "missing",
                                                 "condition": {"type": "value", "op": ">", "threshold": 1}}).status_code == 400
    assert client.post("/api/automations", json={"kind": "report", "name": "x", "dashboard_id": d["dashboard_id"],
                                                 "channels": ["email"]}).status_code == 400
    srv.shutdown()


def test_automation_rejects_malformed_frequency_and_filters(client):
    client.put("/api/metrics/orders", json={"label": "Orders", "model": "sales", "expression": "count(DISTINCT order_id)", "format": "integer"})
    base = {"kind": "alert", "name": "x", "metric": "orders", "period": "last_month", "compare": "previous_period",
            "condition": {"type": "change_pct", "op": "<", "threshold": -20}}
    # Formats a model might make up: they must be rejected, not ignored.
    assert client.post("/api/automations", json={**base, "frequency": {"type": "weekly", "day": "monday"}}).status_code == 400
    assert client.post("/api/automations", json={**base, "frequency": {"cron": "0 9 * * 1"}}).status_code == 400
    assert client.post("/api/automations", json={**base, "frequency": {"every": "week", "weekday": 0, "at": "09:00"},
                                                 "filters": [{"field": "channel", "op": "=", "value": "Online"}]}).status_code == 400
    assert client.post("/api/automations", json={**base, "frequency": {"every": "week", "weekday": 0, "at": "09:00"},
                                                 "filters": [{"column": "missing", "values": ["x"]}]}).status_code == 400
    ok = client.post("/api/automations", json={**base, "frequency": {"every": "week", "weekday": 0, "at": "09:00"},
                                               "filters": [{"column": "channel", "values": ["Online"]}]})
    assert ok.status_code == 200 and "every Monday at 09:00" in ok.json()["description"] and "only channel = Online" in ok.json()["description"]


def test_mcp_schemas_are_explicit():
    import asyncio
    from app.main import mcp
    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    schema = json.dumps(tools["create_alert"].input_schema)
    for key in ("weekday", "every", "threshold", "column", "values"):
        assert key in schema, key


def test_calendar_previous_ranges():
    import datetime as dt
    from app.metrics import previous_range, resolve_period
    a = dt.date(2026, 8, 31)
    assert previous_range(resolve_period("last_month", a), "previous_period", "last_month") == (dt.date(2026, 6, 1), dt.date(2026, 6, 30))
    assert previous_range(resolve_period("mtd", dt.date(2026, 3, 31)), "previous_period", "mtd") == (dt.date(2026, 2, 1), dt.date(2026, 2, 28))
    assert previous_range(resolve_period("last_quarter", a), "previous_period", "last_quarter") == (dt.date(2026, 1, 1), dt.date(2026, 3, 31))
    assert previous_range(resolve_period("last_7_days", a), "previous_period", "last_7_days") == (dt.date(2026, 8, 18), dt.date(2026, 8, 24))


def test_sql_workbench_and_excel(client):
    import io
    from openpyxl import load_workbook
    r = client.post("/api/sql/format", json={"sql": "select channel,count(*) n from sales group by 1"})
    assert r.status_code == 200 and "GROUP BY" in r.json()["sql"] and "\n" in r.json()["sql"]
    run = client.post("/api/query", json={"sql": "SELECT channel, count(*) AS n FROM sales GROUP BY 1"}).json()
    assert "elapsed_ms" in run and run["row_count"] == 3
    q = client.post("/api/queries", json={"title": "Orders by channel", "sql": "SELECT channel, count(*) AS n FROM sales GROUP BY 1"}).json()
    q2 = client.post("/api/queries", json={**q, "title": "Renamed"}).json()
    assert q2["id"] == q["id"] and q2["created_at"] == q["created_at"]
    assert any(x["title"] == "Renamed" for x in client.get("/api/queries").json())
    out = toolbox.run("save_query", {"title": "Revenue by region", "sql": "select region, sum(unit_price) s from sales group by 1"})
    assert out.events[0]["type"] == "saved_query"
    from app.tools import ToolError
    with pytest.raises(ToolError):
        toolbox.run("save_query", {"title": "broken", "sql": "SELECT column_that_does_not_exist FROM sales"})
    # Excel of a query
    x = client.post("/api/export/xlsx", json={"sql": "SELECT order_date, channel, unit_price FROM sales LIMIT 50", "title": "Detail"})
    assert x.status_code == 200 and "spreadsheetml" in x.headers["content-type"]
    wb = load_workbook(io.BytesIO(x.content))
    ws = wb["Detail"]
    assert ws["A3"].value == "order_date" and ws.max_row == 53 and "SQL" in wb.sheetnames
    import datetime as dt
    assert isinstance(ws["A4"].value, (dt.date, dt.datetime))  # dates arrive as Excel dates
    # Excel of a dashboard: a summary sheet + one per chart, with currency format
    chart = toolbox.run("create_chart", {"title": "Sales by channel", "type": "bar", "x": "channel", "y": ["total"], "value_format": "currency", "currency": "EUR",
                                         "sql": "SELECT channel, sum(unit_price) AS total FROM sales GROUP BY 1 ORDER BY 2 DESC"}).content
    d = toolbox.run("create_dashboard", {"title": "Export", "chart_ids": [chart["chart_id"]]}).content
    x = client.post(f"/api/dashboards/{d['dashboard_id']}/export.xlsx", json={"date_from": "2026-01-01"})
    wb = load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames == ["Summary", "Sales by channel"]
    assert "€" in wb["Sales by channel"]["B2"].number_format


def test_sql_assist_uses_one_shot_completion(client, monkeypatch):
    from app.main import agent
    calls = []

    async def fake_complete(system, prompt, effort="medium"):
        calls.append(prompt)
        if len(calls) == 1:  # first answer with a column that doesn't exist → it must retry with the error
            return "Here it is.\n```sql\nSELECT channel, count(*) AS n FROM sales WHERE nope = 1 GROUP BY 1\n```"
        return "Fixed.\n```sql\nSELECT channel, count(*) AS n FROM sales GROUP BY 1\n```"

    monkeypatch.setattr(agent, "complete", fake_complete)
    r = client.post("/api/sql/assist", json={"mode": "generate", "prompt": "orders by channel"}).json()
    assert r["sql"].startswith("SELECT") and "nope" not in r["sql"] and len(calls) == 2
    assert "<semantic_layer>" in calls[0]


def test_interactive_dashboards(client):
    sem = semantic
    from app.filters import DrillError, resolve_click
    # Click resolution: aliases, date_trunc, metric CTEs
    assert resolve_click("SELECT date_trunc('month', order_date) AS month, sum(quantity) FROM sales GROUP BY 1", "month", "2026-02-01T00:00:00", sem) == \
        {"kind": "date", "date_from": "2026-02-01", "date_to": "2026-02-28"}
    assert resolve_click("SELECT CAST(date_trunc('quarter', order_date) AS DATE) AS t FROM sales", "t", "2026-04-01", sem)["date_to"] == "2026-06-30"
    assert resolve_click("SELECT channel AS c, count(*) FROM sales GROUP BY 1", "c", "Online", sem) == {"kind": "dimension", "column": "channel", "value": "Online"}
    client.put("/api/metrics/orders", json={"label": "Orders", "model": "sales", "expression": "count(DISTINCT order_id)", "format": "integer"})
    msql = client.post("/api/metrics/query", json={"metrics": ["orders"], "grain": "month", "group_by": ["region"], "compare": "previous_year"}).json()["sql"]
    assert resolve_click(msql, "period", "2026-03-01", sem)["date_to"] == "2026-03-31"
    assert resolve_click(msql, "region", "North", sem)["column"] == "region"
    with pytest.raises(DrillError):
        resolve_click("SELECT count(*) AS n FROM sales", "n", 5, sem)

    chart = toolbox.run("create_chart", {"title": "Orders by channel", "type": "bar", "x": "channel", "y": ["n"],
                                         "sql": "SELECT channel, count(*) AS n FROM sales GROUP BY 1",
                                         "targets": [{"value": 10000, "label": "Goal"}]}).content
    d = toolbox.run("create_dashboard", {"title": "Interactive", "chart_ids": [chart["chart_id"]]}).content["dashboard_id"]
    r = client.post(f"/api/dashboards/{d}/drill", json={"chart_id": chart["chart_id"], "column": "channel", "value": "Store"}).json()
    assert r == {"kind": "dimension", "column": "channel", "value": "Store"}
    # Text and annotations
    client.post(f"/api/dashboards/{d}/text", json={"text": "## Channels\nContext", "position": 0})
    dash = client.get(f"/api/dashboards/{d}").json()
    assert dash["charts"][0]["spec"]["type"] == "text" and dash["charts"][0]["data"] is None
    assert dash["charts"][1]["spec"]["targets"][0]["value"] == 10000
    toolbox.run("update_dashboard_chart", {"dashboard_id": d, "chart_id": chart["chart_id"], "annotations": [{"x": "Online", "label": "Campaign"}]})
    assert client.get(f"/api/dashboards/{d}").json()["charts"][1]["spec"]["annotations"][0]["label"] == "Campaign"
    r = client.patch(f"/api/dashboards/{d}/charts/{dash['charts'][0]['spec']['id']}", json={"text": "## New"})
    assert r.status_code == 200 and r.json()["spec"]["text"] == "## New"
    from app.tools import ToolError
    with pytest.raises(ToolError):
        toolbox.run("add_text_block", {"dashboard_id": d, "text": "   "})
    # The dashboard Excel skips text blocks
    from openpyxl import load_workbook
    import io
    wb = load_workbook(io.BytesIO(client.post(f"/api/dashboards/{d}/export.xlsx", json={}).content))
    assert wb.sheetnames == ["Summary", "Orders by channel"]


def test_data_quality_and_cleaning(client):
    r = client.post("/api/sources/upload", files={"files": ("supplier_purchases.csv", (DEMO / "supplier_purchases.csv").read_bytes())})
    assert r.status_code == 200, r.text
    rep = client.get("/api/quality/supplier_purchases").json()
    kinds = {i["kind"]: i for i in rep["issues"]}
    assert {"duplicates", "categories", "number_as_text", "negatives"} <= set(kinds), list(kinds)
    assert rep["score"] < 70
    # 1) Exact duplicates
    assert client.post("/api/prep/supplier_purchases/preview", json=kinds["duplicates"]["fix"]).json()["rows_changed"] == 25
    client.post("/api/prep/supplier_purchases/steps", json=kinds["duplicates"]["fix"])
    # 2) Unify supplier variants (the suggested fix carries the mapping)
    prov = next(i for i in rep["issues"] if i["kind"] == "categories" and i["column"] == "supplier")
    client.post("/api/prep/supplier_purchases/steps", json={"kind": "trim", "column": "supplier"})
    client.post("/api/prep/supplier_purchases/steps", json=prov["fix"])
    vals = client.post("/api/query", json={"sql": "SELECT DISTINCT supplier FROM supplier_purchases ORDER BY 1"}).json()["rows"]
    assert len(vals) <= 5, vals  # 4 real suppliers
    # 3) Amount as text with a decimal comma → number
    pv = client.post("/api/prep/supplier_purchases/preview", json=kinds["number_as_text"]["fix"]).json()
    assert pv["new_nulls"] == 0 and pv["rows_changed"] > 1000
    client.post("/api/prep/supplier_purchases/steps", json=kinds["number_as_text"]["fix"])
    total = client.post("/api/query", json={"sql": "SELECT sum(amount) FROM supplier_purchases"}).json()["rows"][0][0]
    assert isinstance(total, float) and total > 100000
    # The model now reads from the clean view and keeps its name; the original table doesn't create another model
    recipe = client.get("/api/prep/supplier_purchases").json()
    assert len(recipe["steps"]) == 4 and recipe["source"]["table"] == "supplier_purchases"
    assert semantic.model("supplier_purchases")["tableReference"]["schema"] == "prep"
    assert sum(1 for n in semantic.model_names() if n.startswith("supplier_purchases")) == 1
    # After a "restart" (new semantic layer) it still isn't duplicated
    from app.semantic import SemanticLayer
    from app.main import workspace
    again = SemanticLayer(workspace)
    assert sum(1 for n in again.model_names() if n.startswith("supplier_purchases")) == 1
    semantic.sync()
    # Quality improves
    assert client.get("/api/quality/supplier_purchases").json()["score"] > rep["score"]
    # A filter with an unsafe condition → rejected
    bad = client.post("/api/prep/supplier_purchases/steps", json={"kind": "filter_rows", "condition": "1=1; DROP TABLE x"})
    assert bad.status_code == 400
    # Undoing everything points the model back at the original table
    for s in reversed(recipe["steps"]):
        client.delete(f"/api/prep/supplier_purchases/steps/{s['id']}")
    assert semantic.model("supplier_purchases")["tableReference"]["schema"] == "main"
    assert client.get("/api/prep/supplier_purchases").json()["steps"] == []


def test_ai_cleaning_tools(client):
    rep = toolbox.run("check_data_quality", {"model": "supplier_purchases"}).content
    fix = next(i["fix"] for i in rep["issues"] if i["kind"] == "whitespace")
    out = toolbox.run("add_cleaning_step", {"model": "supplier_purchases", "step": fix})
    assert out.content["ok"] and any(e["type"] == "cleaning" for e in out.events)
    from app.tools import ToolError
    with pytest.raises(ToolError):
        toolbox.run("add_cleaning_step", {"model": "supplier_purchases", "step": {"kind": "delete_everything"}})
    toolbox.run("remove_cleaning_step", {"model": "supplier_purchases", "step_id": out.content["recipe"]["steps"][0]["id"]})


def test_url_and_google_sheet_sources(client, tmp_path):
    import threading
    from http.server import HTTPServer, SimpleHTTPRequestHandler
    from functools import partial
    from app.dataprep import normalize_url
    assert normalize_url("https://docs.google.com/spreadsheets/d/AbC-123_x/edit#gid=456") == \
        ("https://docs.google.com/spreadsheets/d/AbC-123_x/export?format=csv&gid=456", ".csv")
    assert normalize_url("https://docs.google.com/spreadsheets/d/AbC/edit")[0].endswith("format=csv")
    (tmp_path / "targets.csv").write_text("region,target\nNorth,100\nSouth,80\n")
    srv = HTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(tmp_path)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/targets.csv"
    r = client.post("/api/sources/url", json={"name": "targets", "url": url, "refresh_minutes": 60})
    assert r.status_code == 200, r.text
    assert client.post("/api/query", json={"sql": "SELECT sum(target) FROM targets"}).json()["rows"] == [[180]]
    src = next(s for s in client.get("/api/sources").json() if s.get("table") == "targets")
    assert src["type"] == "url" and src["refresh_minutes"] == 60
    (tmp_path / "targets.csv").write_text("region,target\nNorth,100\nSouth,80\nEast,20\n")
    assert client.post("/api/sources/targets/refresh").status_code == 200
    assert client.post("/api/query", json={"sql": "SELECT sum(target) FROM targets"}).json()["rows"] == [[200]]
    assert len([s for s in client.get("/api/sources").json() if s.get("table") == "targets"]) == 1
    assert client.post("/api/sources/url", json={"name": "x", "url": "ftp://nope"}).status_code == 400
    srv.shutdown()
