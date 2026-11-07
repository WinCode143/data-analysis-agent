"""Analyst agent: a provider-independent tool loop that emits events for the interface."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import uuid
from typing import Any, AsyncIterator

from . import config
from .providers import PRESETS, ProviderError, Settings, make_adapter
from .semantic import SemanticLayer
from .tools import ToolError, Toolbox, to_model_content

MAX_TOOL_ROUNDS = 30

SYSTEM_PROMPT = """You are the data analyst of Insight Suite. You help business people understand their data, \
build dashboards and draw useful conclusions. You answer in the user's language (English by default).

## How you work with the data
- ALWAYS query through the semantic layer: use the model and column names you're given (in double quotes if they \
have spaces or capitals). DuckDB dialect.
- Each column comes with its role: [time], [measure], [calculated measure], [dimension, N values], [identifier], \
[unique label]. Use them to decide what to aggregate (measures), what to group by (dimensions) and which time axis to use.
- Before querying a model you don't know, use `describe_model`. Don't make up columns or values: check them.
- Aggregate in SQL and bring back only what you need. Every figure you state must come from a query in this conversation.
- If you discover something that makes the semantic layer more precise (what a column means, a relationship), save it \
with `edit_semantic_layer`.

## Data quality and cleanup
- If the data looks odd (categories duplicated by case or spaces, totals that don't add up, text that should be \
numbers) or you're asked to "clean" or "check the quality", use `check_data_quality`. Apply the safe fixes it \
proposes (fix) with `add_cleaning_step` and explain each one in one line with its effect. To filter rows or fill \
blanks with a business value, use `preview_cleaning_step` and ask for confirmation first.
- Cleanup never deletes the original data: it's a reversible recipe.

## Official metrics
- If there's an official metric for what you're asked (they're listed under "Official metrics"), use it with \
`query_metrics` instead of writing your own formula: that way everyone gets the same figure. It also handles \
comparisons (year over year, month over month, period against the previous one) and relative periods \
(ytd, last_12_months…); periods are computed up to the latest available data, so say so when you present figures.
- The SQL that `query_metrics` returns can be passed as-is to `create_chart` (e.g. a kpi with \
y: [<metric>, <metric>_previous]).
- If a business metric gets computed repeatedly and doesn't exist yet (ratios, averages per order, rates), define \
it with `define_metric` (an aggregate expression) and mention it.

## Automations
- "Let me know if…", "alert me when…" → `create_alert` on an official metric (define it first if needed). \
Turn the sentence into a period, a comparison and a threshold, and confirm in one line what it watches and how often.
- "Send me this dashboard every…" → `schedule_report`. If a channel (email, Slack) isn't configured, say so and \
point to Settings → Notifications; the in-app inbox always works. They're managed in the "Automations" section of \
the menu. If a call fails because of the format, fix the arguments according to the error; don't create \
duplicates (use list_automations to check).

## How to design a dashboard
1. Identify the kind of data and the business question:
   - Transactional with a date (sales, orders, payments, events): KPI for the period compared with the previous \
period (y: [current, previous]), time trend, breakdown by the main dimension, composition/mix (stacked_bar), \
top-N (hbar) and a dimension × time heatmap to spot anomalies.
   - Entities/customers (one row per customer, product, employee): size and distribution by segment, \
concentration (what % the top 10/20 % brings in), top and bottom N, relationship between two measures (scatter), \
sign-ups over time.
   - Operations/service (tickets, incidents, deliveries): volume over time, averages and percentiles \
(p50/p90 with quantile_cont), on-time rate, breakdown by reason/status, satisfaction.
   - Financial (revenue, costs, margins, budget): totals and margin %, evolution, breakdown by line, \
deviation against a reference.
   - No time column (catalogs, surveys, inventories): distribution by category, rankings, scatter between \
measures and a detail table.
   If there are several related models, combine them: the main fact with its dimensions (product, customer, region).
2. Time granularity based on the range: ≤ 3 months → day or week; ≤ 3 years → month; longer → quarter or year. \
Leave out incomplete periods when comparing (e.g. the current month) or say so in the subtitle.
3. Layout: 2-4 KPIs on top (width 3), the main chart full width (12), then the supporting ones (6). \
Between 5 and 9 charts in total. Each chart answers one question; the title says what it shows and the subtitle the \
period or the unit. Sort bars from largest to smallest, at most ~8 series (the rest in "Other"), never two Y axes.
4. Forms: evolution → line/area; comparing categories → bar (hbar with long names or > 8 categories); \
composition → stacked_bar; relationship between measures → scatter; key figure → kpi; two dimensions crossed → heatmap; \
detail → table. Use a fitting value_format (currency with its currency, percent for 0-1 ratios, integer for counts).
5. Call `create_dashboard` with the chart_ids in order, then write the conclusions.
6. Enrich it when it helps: sections with `add_text_block` ("## Customers") if there are more than ~6 charts; targets \
when the user mentions goals or budgets; annotations on time series for events that explain a change (e.g. the drop \
you found), with `update_dashboard_chart`.

## Conclusions (required for every dashboard)
After creating or reviewing a dashboard, analyze it the way a good analyst would and save the result with \
`write_dashboard_insights`:
- Investigate before concluding: compare periods, break down by dimensions until you find the cause of each change, \
look for concentrations, outliers and trends. Check every figure with a query.
- `summary`: 2-4 sentences with the most important things management should know.
- `findings`: 3-6 findings ordered by impact. Each one with the key figure in the title ("The North fell 86 % \
in March"), the explanation/cause in the detail, its kind (positive, negative, warning, neutral) and the chart_id that \
backs it. Nothing generic ("sales vary") or obvious.
- `recommendations`: 2-4 concrete actions that follow from the findings.
- `scope`: the period and filters analyzed.
In the chat, sum up the main findings in a few lines.

## Style
- Concise: findings first, with concrete figures. No filler.
- When you finish, if it makes sense, add a last line that starts exactly with "Follow-up questions:" with \
2-3 short follow-up questions separated by " | "."""


class Conversation:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    @classmethod
    def new(cls, provider: str, model: str) -> "Conversation":
        now = dt.datetime.now().isoformat(timespec="seconds")
        return cls({"id": uuid.uuid4().hex[:12], "title": "New conversation", "created_at": now,
                    "updated_at": now, "provider": provider, "model": model,
                    "messages": [], "ui": [], "semantic_snapshot": None, "usage": {"input_tokens": 0, "output_tokens": 0}})

    @classmethod
    def load(cls, conv_id: str) -> "Conversation":
        if not conv_id.isalnum():
            raise KeyError(conv_id)
        path = config.CONVERSATIONS_DIR / f"{conv_id}.json"
        if not path.exists():
            raise KeyError(conv_id)
        data = json.loads(path.read_text())
        data.setdefault("provider", "anthropic")
        data.setdefault("model", config.CLAUDE_MODEL)
        data.setdefault("usage", {"input_tokens": 0, "output_tokens": 0})
        return cls(data)

    @property
    def id(self) -> str:
        return self.data["id"]

    def save(self) -> None:
        self.data["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        path = config.CONVERSATIONS_DIR / f"{self.id}.json"
        path.write_text(json.dumps(self.data, ensure_ascii=False, default=str))

    def record_ui(self, event: dict[str, Any]) -> None:
        """Stores the interface events (merging text deltas) so the conversation can be redrawn."""
        ui = self.data["ui"]
        if event["type"] in {"text", "thinking"} and ui and ui[-1]["type"] == event["type"]:
            ui[-1]["delta"] += event["delta"]
        elif event["type"] not in {"conversation"}:
            ui.append(dict(event))


class Agent:
    def __init__(self, semantic: SemanticLayer, toolbox: Toolbox, settings: Settings) -> None:
        self.sl = semantic
        self.toolbox = toolbox
        self.settings = settings
        self.http_client_override: Any = None  # for tests

    @staticmethod
    def switch_provider(conv: Conversation, provider: str, model: str) -> None:
        """Moves a conversation to another provider/model. The native history can't be carried across providers,
        so it's summed up as a text transcript and handed to the new model with the next message."""
        parts: list[str] = []
        for ev in conv.data["ui"]:
            if ev["type"] == "user":
                parts.append(f"User: {ev['text']}")
            elif ev["type"] == "text":
                parts.append(f"Analyst: {ev['delta']}")
            elif ev["type"] == "chart":
                parts.append(f"(Chart created: “{ev['spec']['title']}”, chart_id {ev['spec']['id']})")
            elif ev["type"] == "dashboard":
                parts.append(f"(Dashboard: “{ev['title']}”, id {ev['id']})")
        transcript = "\n".join(parts)[-12000:]
        conv.data.update(provider=provider, model=model, messages=[], semantic_snapshot=None,
                         carryover=transcript or None)

    def _user_texts(self, conv: Conversation, text: str) -> list[str]:
        """The semantic layer is only sent when it changes, as new user text: the history stays
        append-only and the cached prefix isn't invalidated."""
        texts = []
        if conv.data.get("carryover"):
            texts.append("<previous_conversation>\nContext of this conversation with another model (its figures and charts "
                         f"are still valid):\n{conv.data.pop('carryover')}\n</previous_conversation>")
        current = self.sl.describe()
        if current != conv.data.get("semantic_snapshot"):
            conv.data["semantic_snapshot"] = current
            header = "Available semantic layer" if not conv.data["messages"] else "The semantic layer changed; current version"
            texts.append(f"<semantic_layer>\n{header}:\n\n{current}\n</semantic_layer>")
        texts.append(text)
        return texts

    async def run(self, conv: Conversation, user_text: str) -> AsyncIterator[dict[str, Any]]:
        def emit(event: dict[str, Any]) -> dict[str, Any]:
            conv.record_ui(event)
            return event

        yield emit({"type": "conversation", "id": conv.id, "provider": conv.data["provider"], "model": conv.data["model"]})
        if not conv.data["messages"]:
            conv.data["title"] = user_text.strip().split("\n")[0][:80] or conv.data["title"]
        yield emit({"type": "user", "text": user_text})

        usage = {"input_tokens": 0, "output_tokens": 0}
        adapter = None
        try:
            adapter = make_adapter(self.settings, conv.data["provider"], conv.data["model"], self.http_client_override)
            conv.data["messages"].append(adapter.user_message(self._user_texts(conv, user_text)))
            for _ in range(MAX_TOOL_ROUNDS):
                result = None
                async for ev in adapter.turn(SYSTEM_PROMPT, conv.data["messages"], self.toolbox.definitions()):
                    if ev["type"] == "_turn":
                        result = ev["result"]
                    else:
                        yield emit(ev)
                assert result is not None
                for k in usage:
                    usage[k] += result.usage.get(k, 0)
                if result.assistant_message:
                    conv.data["messages"].append(result.assistant_message)

                if result.stop == "refusal":
                    yield emit({"type": "error", "message": "The model declined this request. Rephrase it and try again."})
                    break
                if result.stop == "pause":
                    continue
                if not result.tool_calls:
                    if result.stop == "max_tokens":
                        yield emit({"type": "error", "message": "The answer was cut off for length."})
                    break
                if result.stop == "max_tokens":
                    yield emit({"type": "error", "message": "The answer was cut off for length. Ask for something narrower."})
                    break

                tool_results = []
                for call in result.tool_calls:
                    yield emit({"type": "tool_start", "id": call.id, "name": call.name,
                                "input": call.input if isinstance(call.input, dict) else {"raw": call.input}})
                    if not isinstance(call.input, dict):
                        out, error = None, "Invalid arguments: a JSON object was expected"
                    else:
                        try:
                            out = await asyncio.to_thread(self.toolbox.run, call.name, call.input)
                            error = None
                        except ToolError as e:
                            out, error = None, str(e)
                    if error is None:
                        for ev in out.events:
                            yield emit({**ev, "tool_id": call.id})
                        tool_results.append((call.id, to_model_content(out.content), False))
                    else:
                        tool_results.append((call.id, error, True))
                    yield emit({"type": "tool_end", "id": call.id, "name": call.name, "ok": error is None, "error": error})
                conv.data["messages"].extend(adapter.tool_results(tool_results))
            else:
                yield emit({"type": "error", "message": "Reached the maximum number of tool steps for this turn."})
        except ProviderError as e:
            yield emit({"type": "error", "message": str(e)})
        finally:
            if adapter is not None:
                pending = adapter.pending_tool_ids(conv.data["messages"])
                if pending:  # interrupted turn: close the calls so the history stays valid
                    conv.data["messages"].extend(adapter.tool_results([(p, "Interrupted", True) for p in pending]))
            for k in usage:
                conv.data["usage"][k] = conv.data["usage"].get(k, 0) + usage[k]
            conv.save()
        yield emit({"type": "done", "usage": usage})

    async def complete(self, system: str, prompt: str, effort: str = "medium") -> str:
        """A single-turn answer, without tools, with the default provider (quick assistants)."""
        pid = self.settings.data["provider"]
        model = self.settings.provider_cfg(pid)["model"]
        adapter = make_adapter(self.settings, pid, model, self.http_client_override)
        adapter.effort = effort
        parts: list[str] = []
        async for ev in adapter.turn(system, [adapter.user_message([prompt])], []):
            if ev["type"] == "text":
                parts.append(ev["delta"])
        return "".join(parts)
