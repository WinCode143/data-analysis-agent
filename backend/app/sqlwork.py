"""SQL workbench: saved queries, formatting, AI assistant (generate / fix / explain) and
Excel export (results, charts and whole dashboards)."""

from __future__ import annotations

import datetime as dt
import io
import re
import uuid
from typing import Any, Literal

import sqlglot
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from pydantic import BaseModel, Field

from . import config
from .charts import ChartSpec, Dashboard, JsonStore
from .semantic import SemanticLayer
from .workspace import QueryError, QueryResult

QUERIES_DIR = config.WORKSPACE_DIR / "queries"
QUERIES_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_MAX_ROWS = 500_000


class SavedQuery(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:10])
    title: str
    sql: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))
    updated_at: str = Field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))


queries = JsonStore(QUERIES_DIR, SavedQuery)


def format_sql(sql: str) -> str:
    try:
        return ";\n\n".join(sqlglot.transpile(sql, read="duckdb", write="duckdb", pretty=True))
    except sqlglot.errors.ParseError as e:
        raise QueryError(f"Couldn't format it: {e}") from e


# ---------------------------------------------------------------------- Excel
HEADER_FILL = PatternFill("solid", fgColor="1C5CAB")
HEADER_FONT = Font(bold=True, color="FFFFFF")
FORMATS = {"currency": '#,##0.00 "{cur}"', "percent": "0.0%", "integer": "#,##0", "number": "#,##0.00"}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?$")


def _cell(v: Any) -> Any:
    if isinstance(v, str) and _DATE_RE.match(v):
        try:
            d = dt.datetime.fromisoformat(v)
            return d.date() if len(v) == 10 or (d.hour == d.minute == d.second == 0) else d
        except ValueError:
            return v
    if isinstance(v, (list, dict)):
        return str(v)
    return v


def _sheet_name(title: str, used: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", " ", title).strip()[:28] or "Sheet"
    name, i = base, 2
    while name.lower() in used:
        name, i = f"{base[:25]} ({i})", i + 1
    used.add(name.lower())
    return name


def write_sheet(wb: Workbook, title: str, result: QueryResult, used: set[str],
                col_formats: dict[str, str] | None = None, note: str | None = None) -> None:
    ws = wb.create_sheet(_sheet_name(title, used))
    start = 1
    if note:
        ws.cell(row=1, column=1, value=note).font = Font(italic=True, color="52514E")
        start = 3
    names = [c["name"] for c in result.columns]
    for j, name in enumerate(names, 1):
        c = ws.cell(row=start, column=j, value=name)
        c.fill, c.font, c.alignment = HEADER_FILL, HEADER_FONT, Alignment(vertical="center")
    widths = [len(n) + 2 for n in names]
    for i, row in enumerate(result.rows, start + 1):
        for j, v in enumerate(row, 1):
            cell = ws.cell(row=i, column=j, value=_cell(v))
            fmt = (col_formats or {}).get(names[j - 1])
            if fmt and isinstance(v, (int, float)):
                cell.number_format = fmt
            elif isinstance(cell.value, dt.datetime):
                cell.number_format = "yyyy-mm-dd hh:mm"
            elif isinstance(cell.value, dt.date):
                cell.number_format = "yyyy-mm-dd"
            if i < start + 200:
                widths[j - 1] = min(max(widths[j - 1], len(str(v)) + 2 if v is not None else 0), 60)
    for j, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(j)].width = max(w, 8)
    ws.freeze_panes = ws.cell(row=start + 1, column=1)
    if result.rows:
        ws.auto_filter.ref = f"A{start}:{get_column_letter(len(names))}{start + len(result.rows)}"


CURRENCY_SYMBOLS = {"EUR": "€", "USD": "US$", "GBP": "£", "MXN": "MX$", "ARS": "AR$", "CLP": "CLP$", "COP": "COL$",
                    "BRL": "R$", "PEN": "S/", "UYU": "$U"}


def chart_formats(spec: ChartSpec) -> dict[str, str]:
    cur = CURRENCY_SYMBOLS.get((spec.currency or "EUR").upper(), spec.currency or "€")
    fmt = FORMATS[spec.value_format].replace("{cur}", cur)
    return {y: fmt for y in spec.y}


def to_bytes(wb: Workbook) -> bytes:
    if "Sheet" in wb.sheetnames and len(wb.sheetnames) > 1:
        del wb["Sheet"]
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def query_workbook(sl: SemanticLayer, sql: str, title: str) -> bytes:
    result, _ = sl.query(sql, limit=EXPORT_MAX_ROWS)
    wb = Workbook()
    note = f"Exported on {dt.datetime.now():%Y-%m-%d %H:%M}" + (f" · truncated to {EXPORT_MAX_ROWS:,} rows" if result.truncated else "")
    write_sheet(wb, title, result, set(), note=note)
    sql_ws = wb.create_sheet("SQL")
    sql_ws["A1"] = sql
    sql_ws["A1"].alignment = Alignment(wrap_text=True, vertical="top")
    sql_ws.column_dimensions["A"].width = 120
    return to_bytes(wb)


def dashboard_workbook(sl: SemanticLayer, d: Dashboard, apply) -> bytes:
    """One sheet per chart (with number formats) + one sheet of conclusions."""
    wb = Workbook()
    used: set[str] = set()
    summary = wb.active
    summary.title = _sheet_name("Summary", used)
    summary["A1"] = d.title
    summary["A1"].font = Font(bold=True, size=16)
    summary["A2"] = f"Exported on {dt.datetime.now():%Y-%m-%d %H:%M}"
    row = 4
    if d.insights:
        ins = d.insights
        for label, text in [("Summary", ins.summary), ("Scope", ins.scope or "")]:
            if text:
                summary.cell(row=row, column=1, value=label).font = Font(bold=True)
                c = summary.cell(row=row, column=2, value=text)
                c.alignment = Alignment(wrap_text=True, vertical="top")
                row += 1
        row += 1
        summary.cell(row=row, column=1, value="Findings").font = Font(bold=True, size=13)
        row += 1
        for f in ins.findings:
            summary.cell(row=row, column=1, value=f.kind.capitalize())
            summary.cell(row=row, column=2, value=f"{f.title}. {f.detail}").alignment = Alignment(wrap_text=True)
            row += 1
        row += 1
        summary.cell(row=row, column=1, value="Recommendations").font = Font(bold=True, size=13)
        row += 1
        for r in ins.recommendations:
            summary.cell(row=row, column=2, value=f"• {r}").alignment = Alignment(wrap_text=True)
            row += 1
    summary.column_dimensions["A"].width = 18
    summary.column_dimensions["B"].width = 110
    for spec in d.charts:
        if spec.type == "text":
            continue
        try:
            result, _ = sl.query(apply(spec.sql), limit=EXPORT_MAX_ROWS)
        except QueryError as e:
            ws = wb.create_sheet(_sheet_name(spec.title, used))
            ws["A1"] = f"Error: {e}"
            continue
        write_sheet(wb, spec.title, result, used, chart_formats(spec), note=spec.subtitle or None)
    return to_bytes(wb)


# ---------------------------------------------------------------------- AI assistant
ASSIST_SYSTEM = """You are a SQL expert (DuckDB dialect) helping in a data analysis query editor.
Queries are written ONLY over the semantic layer models (not over physical tables); calculated and
relationship fields are used like normal columns. Names with capitals or spaces go in double quotes.
Answer in the user's language (English by default)."""

SQL_BLOCK = re.compile(r"```(?:sql)?\s*(.+?)```", re.S | re.I)


class AssistIn(BaseModel):
    mode: Literal["generate", "fix", "explain"]
    prompt: str | None = None
    sql: str | None = None
    error: str | None = None


def extract_sql(text: str) -> str | None:
    m = SQL_BLOCK.search(text)
    return m.group(1).strip().rstrip(";") if m else None


async def assist(body: AssistIn, sl: SemanticLayer, complete) -> dict[str, Any]:
    """`complete(system, prompt) -> str` is a single-turn call to the active provider."""
    context = f"<semantic_layer>\n{sl.describe()}\n</semantic_layer>"
    if body.mode == "explain":
        text = await complete(ASSIST_SYSTEM, f"{context}\n\nExplain in business language, in 3-6 bullets, what this query "
                                             f"computes and any risks (duplicates, filters, nulls):\n```sql\n{body.sql}\n```")
        return {"text": text}
    if body.mode == "generate":
        ask = (f"{context}\n\nWrite the SQL query for: {body.prompt}\n"
               + (f"Current query in the editor (change it if relevant):\n```sql\n{body.sql}\n```\n" if body.sql else "")
               + "Return one sentence of explanation and then ONLY the query in a ```sql``` block.")
    else:
        ask = (f"{context}\n\nThis query fails:\n```sql\n{body.sql}\n```\nError: {body.error}\n"
               "Fix it. Return one sentence with the cause and the fixed query in a ```sql``` block.")
    text = await complete(ASSIST_SYSTEM, ask)
    sql = extract_sql(text)
    for _ in range(2):  # validates against the semantic layer and retries with the error
        if not sql:
            break
        try:
            sl.query(sql, limit=1)  # really run it: the planner doesn't catch columns that don't exist
            break
        except QueryError as e:
            text = await complete(ASSIST_SYSTEM, f"{context}\n\nThe query\n```sql\n{sql}\n```\nfails with: {e}\n"
                                                 "Fix it. Return one sentence and the query in a ```sql``` block.")
            sql = extract_sql(text) or sql
    explanation = SQL_BLOCK.sub("", text).strip()
    return {"sql": format_sql(sql) if sql else None, "text": explanation}
