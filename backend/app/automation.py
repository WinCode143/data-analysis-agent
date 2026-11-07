"""Automations: scheduled dashboard reports and alerts on metrics.

- Report: at the set time it (optionally) regenerates the conclusions with the AI and sends a summary of the
  dashboard (KPIs + conclusions + recommendations).
- Alert: evaluates an official metric over a period (and against the previous period if asked). If the
  condition holds, the AI investigates the cause and a notification goes out.

Every result lands in the app's notification inbox; it can also go out by email (SMTP), Slack
(incoming webhook) or a generic webhook (Teams, n8n, Zapier…). Secrets are stored encrypted.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
import smtplib
import ssl
import urllib.request
import uuid
from email.message import EmailMessage
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import config, vault
from .agent import Agent, Conversation
from .charts import ChartSpec, dashboards
from .metrics import PERIOD_LABELS, PERIODS, DimFilter, MetricQuery, MetricStore
from .schedule import Condition, Frequency
from .providers import Settings
from .semantic import SemanticLayer
from .tools import Tool, ToolError, ToolOutput, Toolbox, _obj

log = logging.getLogger("insight.automation")
AUTOMATIONS_FILE = config.WORKSPACE_DIR / "automations.json"
NOTIFICATIONS_FILE = config.WORKSPACE_DIR / "notifications.json"
CHANNELS_FILE = config.WORKSPACE_DIR / "channels.json"
Channel = Literal["app", "email", "slack", "webhook"]
SECRET_FIELDS = {"smtp_password", "slack_webhook", "webhook_url"}


class Automation(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    kind: Literal["report", "alert"]
    name: str
    enabled: bool = True
    frequency: Frequency = Field(default_factory=Frequency)
    channels: list[Channel] = Field(default_factory=lambda: ["app"])
    recipients: list[str] = Field(default_factory=list)
    # report
    dashboard_id: str | None = None
    refresh_insights: bool = True
    # alert
    metric: str | None = None
    period: str = "last_7_days"
    compare: Literal["previous_period", "previous_year"] | None = "previous_period"
    filters: list[DimFilter] = Field(default_factory=list)
    condition: Condition | None = None
    explain: bool = True
    # state
    created_at: str = Field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))
    next_run: str | None = None
    last_run: str | None = None
    last_status: str | None = None  # ok | triggered | sent | error
    last_message: str | None = None
    last_notified: str | None = None


class Notification(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    automation_id: str | None = None
    kind: str
    level: Literal["info", "warning", "error", "success"] = "info"
    title: str
    body: str  # markdown
    link: str | None = None
    created_at: str = Field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))
    read: bool = False
    delivered: dict[str, str] = Field(default_factory=dict)  # canal → "ok" | error


def _md_to_slack(md: str) -> str:
    md = re.sub(r"\*\*(.+?)\*\*", r"*\1*", md)
    md = re.sub(r"^#+\s*(.+)$", r"*\1*", md, flags=re.M)
    return re.sub(r"\[(.+?)\]\((.+?)\)", r"<\2|\1>", md)


def _md_to_html(md: str) -> str:
    import html
    out = []
    for line in md.splitlines():
        esc = html.escape(line)
        esc = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc)
        esc = re.sub(r"\[(.+?)\]\((.+?)\)", r'<a href="\2">\1</a>', esc)
        if esc.startswith("# "):
            out.append(f"<h2 style='margin:0 0 8px'>{esc[2:]}</h2>")
        elif esc.startswith("## "):
            out.append(f"<h3 style='margin:16px 0 6px'>{esc[3:]}</h3>")
        elif esc.startswith("- "):
            out.append(f"<div style='margin:4px 0 4px 12px'>• {esc[2:]}</div>")
        elif esc.strip():
            out.append(f"<p style='margin:6px 0'>{esc}</p>")
    return ("<div style='font-family:system-ui,Segoe UI,sans-serif;font-size:14px;color:#0b0b0b;max-width:680px'>"
            + "".join(out) + "</div>")


def _fmt(value: Any, fmt: str = "number", currency: str | None = None) -> str:
    if not isinstance(value, (int, float)):
        return "—" if value is None else str(value)
    if fmt == "percent":
        return f"{value * 100:,.1f} %"
    if abs(value) >= 1e6:
        s = f"{value / 1e6:,.2f} M"
    elif fmt == "integer" or abs(value) >= 1000:
        s = f"{value:,.0f}"
    else:
        s = f"{value:,.2f}"
    return f"{s} {currency or 'USD'}" if fmt == "currency" else s


class Automations:
    def __init__(self, agent: Agent, toolbox: Toolbox, metrics: MetricStore, sl: SemanticLayer, settings: Settings) -> None:
        self.agent, self.toolbox, self.metrics, self.sl, self.settings = agent, toolbox, metrics, sl, settings
        self.items: dict[str, Automation] = {}
        self.notifications: list[Notification] = []
        self.running: set[str] = set()
        if AUTOMATIONS_FILE.exists():
            for a in json.loads(AUTOMATIONS_FILE.read_text()):
                self.items[a["id"]] = Automation(**a)
        if NOTIFICATIONS_FILE.exists():
            self.notifications = [Notification(**n) for n in json.loads(NOTIFICATIONS_FILE.read_text())]
        self._register_tools()

    # ------------------------------------------------------------------ persistence
    def _save(self) -> None:
        AUTOMATIONS_FILE.write_text(json.dumps([a.model_dump() for a in self.items.values()], indent=2, ensure_ascii=False))

    def _save_notifications(self) -> None:
        self.notifications = self.notifications[-300:]
        NOTIFICATIONS_FILE.write_text(json.dumps([n.model_dump() for n in self.notifications], indent=2, ensure_ascii=False))

    def channels_config(self, reveal: bool = False) -> dict[str, Any]:
        cfg = json.loads(CHANNELS_FILE.read_text()) if CHANNELS_FILE.exists() else {}
        for k in SECRET_FIELDS:
            if cfg.get(k):
                cfg[k] = vault.decrypt(cfg[k]) if reveal else "••••" + vault.decrypt(cfg[k])[-4:]
        return cfg

    def save_channels(self, patch: dict[str, Any]) -> dict[str, Any]:
        cfg = self.channels_config(reveal=True)
        for k, v in patch.items():
            if k in SECRET_FIELDS and isinstance(v, str) and v.startswith("••••"):
                continue  # masked value: leave it as is
            cfg[k] = v
        stored = {k: (vault.encrypt(v) if k in SECRET_FIELDS and v else v) for k, v in cfg.items()}
        CHANNELS_FILE.write_text(json.dumps(stored, indent=2, ensure_ascii=False))
        CHANNELS_FILE.chmod(0o600)
        return self.channels_config()

    def available_channels(self) -> list[str]:
        cfg = self.channels_config(reveal=True)
        out = ["app"]
        if cfg.get("smtp_host") and cfg.get("smtp_from"):
            out.append("email")
        if cfg.get("slack_webhook"):
            out.append("slack")
        if cfg.get("webhook_url"):
            out.append("webhook")
        return out

    # ------------------------------------------------------------------ CRUD
    def create(self, data: dict[str, Any]) -> Automation:
        a = Automation(**data)
        self._validate(a)
        a.next_run = a.frequency.next_after(dt.datetime.now()).isoformat(timespec="minutes")
        self.items[a.id] = a
        self._save()
        return a

    def update(self, aid: str, patch: dict[str, Any]) -> Automation:
        cur = self.items[aid]
        a = Automation(**{**cur.model_dump(), **patch, "id": aid})
        self._validate(a)
        a.next_run = a.frequency.next_after(dt.datetime.now()).isoformat(timespec="minutes")
        self.items[aid] = a
        self._save()
        return a

    def delete(self, aid: str) -> None:
        self.items.pop(aid, None)
        self._save()

    def _validate(self, a: Automation) -> None:
        if a.kind == "report":
            if not a.dashboard_id:
                raise ValueError("A report needs dashboard_id")
            dashboards.get(a.dashboard_id)
        else:
            if not a.metric or not a.condition:
                raise ValueError("An alert needs metric and condition")
            self.metrics.get(a.metric)
            if a.period not in PERIODS or a.period == "all":
                raise ValueError(f"period must be one of {PERIODS[1:]}")
            if a.condition.type == "change_pct" and not a.compare:
                raise ValueError("A change condition needs compare")
            cols = {c["name"] for c in self.sl.model(self.metrics.get(a.metric).model)["columns"]}
            for f in a.filters:
                if f.column not in cols:
                    raise ValueError(f"The filter uses “{f.column}”, which isn't a column of {self.metrics.get(a.metric).model}")
        if "email" in a.channels and not a.recipients:
            raise ValueError("To send by email, add recipients")
        if "app" not in a.channels:
            a.channels.insert(0, "app")

    def describe(self, a: Automation) -> str:
        if a.kind == "report":
            try:
                title = dashboards.get(a.dashboard_id).title
            except KeyError:
                title = "(deleted dashboard)"
            return f"Report of “{title}” {a.frequency.describe()}"
        label = self.metrics.metrics[a.metric].label if a.metric in self.metrics.metrics else a.metric
        scope = "".join(f", only {f.column} = {' / '.join(f.values)}" for f in a.filters)
        return f"Alert if {a.condition.describe(label)}{scope} ({PERIOD_LABELS.get(a.period, a.period)}), {a.frequency.describe()}"

    # ------------------------------------------------------------------ loop
    async def loop(self) -> None:
        while True:
            try:
                now = dt.datetime.now()
                for a in list(self.items.values()):
                    if a.enabled and a.next_run and dt.datetime.fromisoformat(a.next_run) <= now and a.id not in self.running:
                        await self.execute(a.id)
            except Exception:  # the loop must never die
                log.exception("Error en el planificador")
            await asyncio.sleep(30)

    async def execute(self, aid: str) -> Notification | None:
        a = self.items[aid]
        self.running.add(aid)
        try:
            note = await (self._run_report(a) if a.kind == "report" else self._run_alert(a))
        except Exception as e:
            log.exception("Automation %s failed", aid)
            a.last_status = "error"
            note = Notification(automation_id=aid, kind=a.kind, level="error", title=f"“{a.name}” failed",
                                body=f"The automation couldn't run: {e}")
            await self._deliver(note, a)
        finally:
            self.running.discard(aid)
        a.last_run = dt.datetime.now().isoformat(timespec="seconds")
        a.last_message = note.title if note else "Nothing new: the condition doesn't hold."
        a.next_run = a.frequency.next_after(dt.datetime.now()).isoformat(timespec="minutes")
        self._save()
        return note

    # ------------------------------------------------------------------ headless AI
    async def ask_agent(self, prompt: str, title: str) -> str:
        pid = self.settings.data["provider"]
        conv = Conversation.new(pid, self.settings.provider_cfg(pid)["model"])
        conv.data["title"] = title
        conv.data["automated"] = True
        buf: list[str] = []
        errors: list[str] = []
        async for ev in self.agent.run(conv, prompt):
            if ev["type"] == "tool_start":
                buf = []  # only the final text matters, after the last tool
            elif ev["type"] == "text":
                buf.append(ev["delta"])
            elif ev["type"] == "error":
                errors.append(ev["message"])
        conv.data["title"] = title  # agent.run lo sobreescribe con el prompt
        conv.save()
        text = re.sub(r"\n*\**Follow-up questions:?.*$", "", "".join(buf).strip(), flags=re.S | re.I)
        if not text and errors:
            raise RuntimeError(errors[-1])
        return text

    # ------------------------------------------------------------------ reports
    def _kpis(self, charts: list[ChartSpec]) -> list[str]:
        lines = []
        for c in charts:
            if c.type != "kpi":
                continue
            try:
                result, _ = self.sl.query(c.sql, limit=1)
                row = dict(zip([col["name"] for col in result.columns], result.rows[0]))
            except Exception:
                continue
            value, prev = row.get(c.y[0]), row.get(c.y[1]) if len(c.y) > 1 else None
            line = f"- **{c.title}**: {_fmt(value, c.value_format, c.currency)}"
            if isinstance(value, (int, float)) and isinstance(prev, (int, float)) and prev:
                var = (value - prev) / abs(prev)
                line += f" ({'▲' if var >= 0 else '▼'} {var * 100:+.1f} % vs. {_fmt(prev, c.value_format, c.currency)})"
            lines.append(line)
        return lines

    async def _run_report(self, a: Automation) -> Notification:
        d = dashboards.get(a.dashboard_id)
        if a.refresh_insights:
            await self.ask_agent(
                f"Write the conclusions of the dashboard “{d.title}” (id {d.id}) with the current data. Read the SQL of "
                "its charts with list_dashboards, investigate (compare periods, break down until you find causes) and save "
                "the result with write_dashboard_insights. In the chat, answer only with a 2-line summary.",
                f"[Automatic] Report: {d.title}")
            d = dashboards.get(a.dashboard_id)
        parts = [f"# {d.title}", f"_{dt.date.today().strftime('%Y-%m-%d')} · {a.frequency.describe()}_", ""]
        kpis = self._kpis(d.charts)
        if kpis:
            parts += ["## Indicators", *kpis, ""]
        if d.insights:
            icons = {"positive": "✅", "negative": "🔻", "warning": "⚠️", "neutral": "ℹ️"}
            parts += ["## Summary", d.insights.summary, "", "## Findings"]
            parts += [f"- {icons.get(f.kind, '•')} **{f.title}**. {f.detail}" for f in d.insights.findings]
            if d.insights.recommendations:
                parts += ["", "## Recommendations", *[f"- {r}" for r in d.insights.recommendations]]
        link = f"{config.PUBLIC_URL.rstrip('/')}/#/dashboard/{d.id}"
        parts += ["", f"[Open the dashboard]({link})"]
        note = Notification(automation_id=a.id, kind="report", level="info", title=f"Report: {d.title}",
                            body="\n".join(parts), link=f"#/dashboard/{d.id}")
        await self._deliver(note, a)
        a.last_status = "sent"
        return note

    # ------------------------------------------------------------------ alerts
    def evaluate(self, a: Automation) -> dict[str, Any]:
        m = self.metrics.get(a.metric)
        q = MetricQuery(metrics=[m.name], period=a.period, compare=a.compare, filters=[f.model_dump() for f in a.filters])
        result, info = self.metrics.run(q)
        row = dict(zip([c["name"] for c in result.columns], result.rows[0])) if result.rows else {}
        value, prev, var = row.get(m.name), row.get(f"{m.name}_previous"), row.get(f"{m.name}_change")
        c = a.condition
        observed = (var * 100 if var is not None else None) if c.type == "change_pct" else value
        triggered = observed is not None and (observed < c.threshold if c.op == "<" else observed > c.threshold)
        return {"metric": m, "value": value, "previous": prev, "var": var, "triggered": triggered, "info": info}

    async def _run_alert(self, a: Automation) -> Notification | None:
        ev = self.evaluate(a)
        m, info = ev["metric"], ev["info"]
        if not ev["triggered"]:
            a.last_status = "ok"
            return None
        today = dt.date.today().isoformat()
        if a.frequency.every == "hour" and a.last_notified == today and a.last_status == "triggered":
            return None  # hourly alerts: one notification a day while the condition stays active
        a.last_status = "triggered"
        scope = f"{PERIOD_LABELS.get(a.period, a.period)} ({info['range'][0]} → {info['range'][1]})"
        facts = f"**{m.label}**: {_fmt(ev['value'], m.format, m.currency)} in {scope}"
        if ev["previous"] is not None:
            facts += f", against {_fmt(ev['previous'], m.format, m.currency)} in {info['previous_range'][0]} → {info['previous_range'][1]}"
        if ev["var"] is not None:
            facts += f" ({ev['var'] * 100:+.1f} %)"
        body = [f"# ⚠️ {a.name}", "", f"Condition: {a.condition.describe(m.label)}.", "", facts + "."]
        if a.filters:
            body.append("Filters: " + "; ".join(f"{f.column} ∈ {{{', '.join(f.values)}}}" for f in a.filters))
        if a.explain:
            try:
                why = await self.ask_agent(
                    f"AUTOMATIC ALERT. {facts.replace('**', '')}. Condition: {a.condition.describe(m.label)}. "
                    f"Official metric `{m.name}` of the model `{m.model}`. Investigate the cause with queries: compare the "
                    f"current period with the previous one, breaking down by the model's dimensions until you find where the "
                    f"change is concentrated. Answer in 3-6 lines: the most likely cause with figures, and what to check.",
                    f"[Automatic] Alert: {a.name}")
                body += ["", "## AI analysis", why]
            except Exception as e:
                body += ["", f"_The automatic analysis couldn't be generated: {e}_"]
        a.last_notified = today
        note = Notification(automation_id=a.id, kind="alert", level="warning", title=a.name, body="\n".join(body))
        await self._deliver(note, a)
        return note

    # ------------------------------------------------------------------ delivery
    async def _deliver(self, note: Notification, a: Automation | None) -> None:
        channels = a.channels if a else ["app"]
        for ch in channels:
            if ch == "app":
                note.delivered["app"] = "ok"
                continue
            try:
                await asyncio.to_thread(self.send, ch, note, a.recipients if a else [])
                note.delivered[ch] = "ok"
            except Exception as e:
                note.delivered[ch] = f"error: {e}"
        self.notifications.append(note)
        self._save_notifications()

    def send(self, channel: str, note: Notification, recipients: list[str]) -> None:
        cfg = self.channels_config(reveal=True)
        text = note.body
        if channel == "email":
            if not (cfg.get("smtp_host") and cfg.get("smtp_from")):
                raise RuntimeError("Email isn't configured")
            msg = EmailMessage()
            msg["Subject"] = f"[Insight] {note.title}"
            msg["From"] = cfg["smtp_from"]
            msg["To"] = ", ".join(recipients)
            msg.set_content(text)
            msg.add_alternative(_md_to_html(text), subtype="html")
            port = int(cfg.get("smtp_port") or 587)
            security = cfg.get("smtp_security") or ("ssl" if port == 465 else "starttls")
            ctx = ssl.create_default_context()
            smtp = smtplib.SMTP_SSL(cfg["smtp_host"], port, context=ctx, timeout=20) if security == "ssl" \
                else smtplib.SMTP(cfg["smtp_host"], port, timeout=20)
            with smtp:
                if security == "starttls":
                    smtp.starttls(context=ctx)
                if cfg.get("smtp_user"):
                    smtp.login(cfg["smtp_user"], cfg.get("smtp_password") or "")
                smtp.send_message(msg)
        elif channel in {"slack", "webhook"}:
            url = cfg.get("slack_webhook" if channel == "slack" else "webhook_url")
            if not url:
                raise RuntimeError(f"{channel} isn't configured")
            payload = {"text": _md_to_slack(text)} if channel == "slack" else {
                "title": note.title, "text": text, "level": note.level, "kind": note.kind,
                "link": f"{config.PUBLIC_URL.rstrip('/')}/{note.link}" if note.link else None,
                "created_at": note.created_at}
            req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                if r.status >= 300:
                    raise RuntimeError(f"HTTP {r.status}")
        else:
            raise RuntimeError(f"Unknown channel: {channel}")

    # ------------------------------------------------------------------ tools for the AI
    def _register_tools(self) -> None:
        freq_schema = _obj({"every": {"type": "string", "enum": ["hour", "day", "week", "month"]},
                            "at": {"type": "string", "description": "HH:MM (local time)"},
                            "weekday": {"type": "integer", "description": "0 = Monday (if every=week)"},
                            "day": {"type": "integer", "description": "1-28 (if every=month)"}}, ["every"])
        channels = {"type": "array", "items": {"type": "string", "enum": ["app", "email", "slack", "webhook"]}}

        def create(kind: str):
            def handler(args: dict[str, Any]) -> ToolOutput:
                try:
                    a = self.create({**args, "kind": kind})
                except (ValidationError, ValueError, KeyError) as e:
                    raise ToolError(str(e)) from e
                missing = [c for c in a.channels if c not in self.available_channels()]
                return ToolOutput({"ok": True, "id": a.id, "description": self.describe(a), "next_run": a.next_run,
                                   "unconfigured_channels": missing},
                                  [{"type": "automation", "id": a.id, "name": a.name, "description": self.describe(a),
                                    "next_run": a.next_run}])
            return handler

        self.toolbox.register(Tool(
            "schedule_report",
            "Schedules sending a dashboard periodically (KPIs + conclusions regenerated by the AI) to the app's inbox "
            "and, if they're configured, by email/Slack/webhook. Use it when asked to 'send me this every Monday'.",
            _obj({"name": {"type": "string"}, "dashboard_id": {"type": "string"}, "frequency": freq_schema,
                  "channels": channels, "recipients": {"type": "array", "items": {"type": "string"}},
                  "refresh_insights": {"type": "boolean"}}, ["name", "dashboard_id", "frequency"]),
            create("report")))
        self.toolbox.register(Tool(
            "create_alert",
            "Creates an alert on an OFFICIAL METRIC (define_metric first if it doesn't exist): it's evaluated periodically and, "
            "if the condition holds, the AI investigates the cause and notifies. E.g. 'let me know if orders fall more than 15 % "
            "week over week' → period=last_7_days, compare=previous_period, condition={type:change_pct, op:'<', threshold:-15}.",
            _obj({"name": {"type": "string"}, "metric": {"type": "string"},
                  "period": {"type": "string", "enum": PERIODS[1:]},
                  "compare": {"type": "string", "enum": ["previous_period", "previous_year"]},
                  "condition": _obj({"type": {"type": "string", "enum": ["change_pct", "value"]},
                                     "op": {"type": "string", "enum": ["<", ">"]},
                                     "threshold": {"type": "number"}}, ["type", "op", "threshold"]),
                  "filters": {"type": "array", "items": _obj({"column": {"type": "string"},
                                                              "values": {"type": "array", "items": {"type": "string"}}},
                                                             ["column", "values"])},
                  "frequency": freq_schema, "channels": channels,
                  "recipients": {"type": "array", "items": {"type": "string"}},
                  "explain": {"type": "boolean"}}, ["name", "metric", "period", "condition", "frequency"]),
            create("alert")))
        self.toolbox.register(Tool(
            "list_automations", "Lists the scheduled reports and alerts with their status.", _obj({}, []),
            lambda a: ToolOutput([{**x.model_dump(include={"id", "kind", "name", "enabled", "next_run", "last_status"}),
                                   "description": self.describe(x)} for x in self.items.values()])))
