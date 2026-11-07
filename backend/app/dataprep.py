"""Data preparation and quality.

- Quality: an automatic check per model (duplicates, nulls, inconsistent categories, numbers/dates stored as
  text, negative or extreme values, future dates, freshness) with a 0-100 score and suggested fixes.
- Cleanup: per-model "recipes" of reversible steps. The original data is never touched: the recipe is materialized
  as a VIEW in workspace.prep.<model> and the semantic model reads from it. It works the same with imported
  files and with read-only remote databases. Removing every step points the model back at its source.
- URL sources: public Google Sheets (or shared by link) and CSV/Excel/Parquet files published at a URL,
  with periodic reload.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Literal

import sqlglot
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp

from . import config
from .semantic import SemanticLayer, NUMERIC_TYPES, TIME_TYPES
from .workspace import QueryError, Workspace, quote_ident, sql_literal

PREP_FILE = config.WORKSPACE_DIR / "prep.json"
PREP_SCHEMA = "prep"
TEXT_TYPES = ("varchar", "text", "char", "string")


# ---------------------------------------------------------------------- cleanup steps
class Step(BaseModel):
    """One step of the recipe. `kind` decides which parameters it uses."""
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    kind: Literal["trim", "case", "map_values", "cast", "parse_number", "parse_date", "fill_nulls",
                  "drop_duplicates", "filter_rows"]
    column: str | None = None
    columns: list[str] | None = Field(default=None, description="drop_duplicates: key columns (empty = whole row)")
    mode: Literal["upper", "lower", "title"] | None = None
    mapping: dict[str, str] | None = None
    type: Literal["double", "bigint", "date", "timestamp", "varchar", "boolean"] | None = None
    decimal: Literal[",", "."] | None = None
    format: str | None = Field(default=None, description="parse_date: strptime format, e.g. %d/%m/%Y")
    value: str | None = None
    condition: str | None = Field(default=None, description="filter_rows: SQL condition for the rows to KEEP")
    note: str | None = None


STEP_LABELS = {
    "trim": "Trim spaces", "case": "Unify case", "map_values": "Unify values",
    "cast": "Change type", "parse_number": "Text to number", "parse_date": "Text to date",
    "fill_nulls": "Fill blanks", "drop_duplicates": "Remove duplicates", "filter_rows": "Filter rows",
}


def describe_step(s: Step) -> str:
    c = f"“{s.column}”" if s.column else ""
    if s.kind == "map_values":
        pairs = ", ".join(f"{k!r} → {v!r}" for k, v in list((s.mapping or {}).items())[:4])
        return f"Unify values of {c}: {pairs}" + ("…" if len(s.mapping or {}) > 4 else "")
    if s.kind == "cast":
        return f"Change {c} to {s.type}"
    if s.kind == "case":
        return f"{c} in {'UPPERCASE' if s.mode == 'upper' else 'lowercase' if s.mode == 'lower' else 'Title Case'}"
    if s.kind == "drop_duplicates":
        return "Remove duplicate rows" + (f" by {', '.join(s.columns)}" if s.columns else " (identical)")
    if s.kind == "filter_rows":
        return f"Keep only rows where {s.condition}"
    if s.kind == "fill_nulls":
        return f"Fill blanks in {c} with {s.value!r}"
    if s.kind == "parse_date":
        return f"Convert {c} to a date ({s.format})"
    if s.kind == "parse_number":
        return f"Convert {c} to a number (decimal “{s.decimal or '.'}”)"
    return f"{STEP_LABELS[s.kind]} in {c}"


def _safe_condition(cond: str) -> str:
    """The filter_rows condition must be an expression, not a statement."""
    try:
        node = sqlglot.parse_one(cond, read="duckdb", into=exp.Condition)
    except sqlglot.errors.ParseError as e:
        raise QueryError(f"Invalid condition: {e}") from e
    if any(node.find_all(exp.Select, exp.Command, exp.Insert, exp.Delete, exp.Update, exp.Drop)):
        raise QueryError("The condition can't contain subqueries or statements.")
    return node.sql(dialect="duckdb")


def step_sql(prev: str, s: Step, columns: list[str]) -> str:
    """Wraps the previous query applying one step."""
    col = quote_ident(s.column) if s.column else None
    if s.kind in {"trim", "case", "map_values", "cast", "parse_number", "parse_date", "fill_nulls"}:
        if not s.column or s.column not in columns:
            raise QueryError(f"The column “{s.column}” doesn't exist. Columns: {', '.join(columns)}")
        if s.kind == "trim":
            expr = f"NULLIF(trim(regexp_replace(CAST({col} AS VARCHAR), '\\s+', ' ', 'g')), '')"
        elif s.kind == "case":
            if s.mode in ("upper", "lower"):
                expr = f"{s.mode}({col})"
            else:  # DuckDB has no initcap: capitalize word by word
                expr = f"array_to_string(list_transform(string_split(lower({col}), ' '), w -> upper(w[1]) || w[2:]), ' ')"
        elif s.kind == "map_values":
            if not s.mapping:
                raise QueryError("map_values needs a mapping")
            # Also compares without extra spaces: the mapping still applies after a trim.
            norm_col = f"trim(regexp_replace(CAST({col} AS VARCHAR), '\\s+', ' ', 'g'))"
            cases = " ".join(
                f"WHEN {col} = {sql_literal(k)} OR {norm_col} = {sql_literal(re.sub(r'[ \t\n]+', ' ', k).strip())} THEN {sql_literal(v)}"
                for k, v in s.mapping.items())
            expr = f"CASE {cases} ELSE {col} END"
        elif s.kind == "cast":
            expr = f"TRY_CAST({col} AS {(s.type or 'varchar').upper()})"
        elif s.kind == "parse_number":
            txt = f"regexp_replace(CAST({col} AS VARCHAR), '[^0-9,.\\-]', '', 'g')"
            if (s.decimal or ".") == ",":
                txt = f"replace(replace({txt}, '.', ''), ',', '.')"
            else:
                txt = f"replace({txt}, ',', '')"
            expr = f"TRY_CAST({txt} AS DOUBLE)"
        elif s.kind == "parse_date":
            expr = f"CAST(try_strptime(CAST({col} AS VARCHAR), {sql_literal(s.format or '%Y-%m-%d')}) AS DATE)"
        else:  # fill_nulls (the literal is cast to the column type)
            expr = f"coalesce({col}, {sql_literal(s.value or '')})"
        return f"SELECT * REPLACE ({expr} AS {col}) FROM ({prev})"
    if s.kind == "drop_duplicates":
        if not s.columns:
            return f"SELECT DISTINCT * FROM ({prev})"
        missing = [c for c in s.columns if c not in columns]
        if missing:
            raise QueryError(f"Columns that don't exist: {missing}")
        keys = ", ".join(quote_ident(c) for c in s.columns)
        return f"SELECT * FROM ({prev}) QUALIFY row_number() OVER (PARTITION BY {keys}) = 1"
    if s.kind == "filter_rows":
        return f"SELECT * FROM ({prev}) WHERE {_safe_condition(s.condition or 'TRUE')}"
    raise QueryError(f"Unknown step: {s.kind}")


# ---------------------------------------------------------------------- recipes
class Recipe(BaseModel):
    model: str
    source: dict[str, str]  # original catalog, schema, table
    steps: list[Step] = Field(default_factory=list)
    updated_at: str = Field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))


class Prep:
    def __init__(self, ws: Workspace, sl: SemanticLayer) -> None:
        self.ws, self.sl = ws, sl
        self.recipes: dict[str, Recipe] = {}
        if PREP_FILE.exists():
            for r in json.loads(PREP_FILE.read_text()):
                self.recipes[r["model"]] = Recipe(**r)
        with ws._lock:
            ws.conn.execute(f"CREATE SCHEMA IF NOT EXISTS {config.LOCAL_CATALOG}.{PREP_SCHEMA}")
        self._sync_hooks()

    def _save(self) -> None:
        PREP_FILE.write_text(json.dumps([r.model_dump() for r in self.recipes.values()], indent=2, ensure_ascii=False))
        self._sync_hooks()

    def _sync_hooks(self) -> None:
        # Source tables that have a recipe must not create a new model when syncing.
        self.sl.shadowed_sources = {(r.source["catalog"], r.source["schema"], r.source["table"]) for r in self.recipes.values()}

    @staticmethod
    def view_ref(model: str) -> dict[str, str]:
        return {"catalog": config.LOCAL_CATALOG, "schema": PREP_SCHEMA, "table": model}

    def source_of(self, model: str) -> dict[str, str]:
        if model in self.recipes:
            return self.recipes[model].source
        return dict(self.sl.model(model)["tableReference"])

    def _fq(self, ref: dict[str, str]) -> str:
        return f"{quote_ident(ref['catalog'])}.{quote_ident(ref['schema'])}.{quote_ident(ref['table'])}"

    def build_sql(self, source: dict[str, str], steps: list[Step]) -> str:
        sql = f"SELECT * FROM {self._fq(source)}"
        for s in steps:
            cols = [c["name"] for c in self._columns_of(sql)]
            sql = step_sql(sql, s, cols)
        return sql

    def _columns_of(self, sql: str) -> list[dict[str, str]]:
        return self.ws.execute(f"SELECT * FROM ({sql}) LIMIT 0", limit=0).columns

    def preview(self, model: str, step: Step, sample: int = 8) -> dict[str, Any]:
        """What the step would change: affected rows and before/after examples."""
        source = self.source_of(model)
        before_steps = list(self.recipes[model].steps) if model in self.recipes else []
        before = self.build_sql(source, before_steps)
        after = self.build_sql(source, before_steps + [step])
        n_before = self.ws.execute(f"SELECT count(*) FROM ({before})", limit=1).rows[0][0]
        n_after = self.ws.execute(f"SELECT count(*) FROM ({after})", limit=1).rows[0][0]
        out: dict[str, Any] = {"rows_before": n_before, "rows_after": n_after, "description": describe_step(step)}
        if step.column and step.kind not in {"drop_duplicates", "filter_rows"}:
            c = quote_ident(step.column)
            diff = self.ws.execute(
                f"WITH a AS (SELECT {c} AS v, row_number() OVER () AS rn FROM ({before})), "
                f"b AS (SELECT {c} AS v, row_number() OVER () AS rn FROM ({after})) "
                f"SELECT CAST(a.v AS VARCHAR) AS before, CAST(b.v AS VARCHAR) AS after, count(*) AS rows "
                f"FROM a JOIN b USING (rn) WHERE CAST(a.v AS VARCHAR) IS DISTINCT FROM CAST(b.v AS VARCHAR) "
                f"GROUP BY 1, 2 ORDER BY 3 DESC LIMIT {sample}",
                limit=sample)
            changed = self.ws.execute(
                f"WITH a AS (SELECT {c} AS v, row_number() OVER () AS rn FROM ({before})), "
                f"b AS (SELECT {c} AS v, row_number() OVER () AS rn FROM ({after})) "
                f"SELECT count(*) FROM a JOIN b USING (rn) WHERE CAST(a.v AS VARCHAR) IS DISTINCT FROM CAST(b.v AS VARCHAR)", limit=1)
            out["rows_changed"] = changed.rows[0][0]
            out["examples"] = diff.as_records()
            nulls = self.ws.execute(f"SELECT count(*) FILTER (WHERE {c} IS NULL) FROM ({after})", limit=1).rows[0][0]
            nulls_before = self.ws.execute(f"SELECT count(*) FILTER (WHERE {c} IS NULL) FROM ({before})", limit=1).rows[0][0]
            out["new_nulls"] = max(0, nulls - nulls_before)
        else:
            out["rows_changed"] = n_before - n_after
        return out

    def _materialize(self, model: str) -> None:
        r = self.recipes[model]
        view = self._fq(self.view_ref(model))
        with self.ws._lock:
            if r.steps:
                self.ws.conn.execute(f"CREATE OR REPLACE VIEW {view} AS {self.build_sql(r.source, r.steps)}")
            else:
                self.ws.conn.execute(f"DROP VIEW IF EXISTS {view}")
        m = self.sl.model(model)
        m["tableReference"] = self.view_ref(model) if r.steps else dict(r.source)
        if not r.steps:
            del self.recipes[model]
        self._save()
        self.sl.sync()  # refreshes the model's columns/types against the view and saves it

    def add_step(self, model: str, step: Step) -> Recipe:
        self.sl.model(model)
        if model not in self.recipes:
            self.recipes[model] = Recipe(model=model, source=dict(self.sl.model(model)["tableReference"]))
        r = self.recipes[model]
        self.build_sql(r.source, r.steps + [step])  # validates before saving
        r.steps.append(step)
        r.updated_at = dt.datetime.now().isoformat(timespec="seconds")
        try:
            self._materialize(model)
        except Exception:
            r.steps.pop()
            if not r.steps:
                self.recipes.pop(model, None)
            raise
        return self.recipes.get(model) or Recipe(model=model, source=self.source_of(model))

    def remove_step(self, model: str, step_id: str) -> Recipe | None:
        r = self.recipes.get(model)
        if not r:
            raise KeyError(model)
        r.steps = [s for s in r.steps if s.id != step_id]
        self._materialize(model)
        return self.recipes.get(model)

    def recipe(self, model: str) -> dict[str, Any]:
        r = self.recipes.get(model)
        return {"model": model, "source": self.source_of(model),
                "steps": [{**s.model_dump(), "description": describe_step(s)} for s in (r.steps if r else [])]}


# ---------------------------------------------------------------------- quality
def _norm(v: str) -> str:
    v = unicodedata.normalize("NFD", v.lower().strip())
    v = "".join(ch for ch in v if unicodedata.category(ch) != "Mn")
    return re.sub(r"[\s\.\-_,]+", " ", v).strip()


def _well_formed(variant: tuple[str, int]) -> tuple:
    """Which spelling wins: no extra spaces, mixed case (not ALL CAPS or all lowercase), with accents
    and, when tied, the most frequent one."""
    v, count = variant
    clean = v == re.sub(r"\s+", " ", v).strip()
    mixed = not v.isupper() and not v.islower()
    accents = sum(1 for ch in unicodedata.normalize("NFD", v) if unicodedata.category(ch) == "Mn")
    return (clean, mixed, accents, count)


def _fmt(x: Any) -> str:
    """A number for messages: 6,917.37"""
    if not isinstance(x, (int, float)):
        return str(x)
    return f"{x:,.2f}".removesuffix(".00")


# Column names in English and Spanish
ID_RE = re.compile(r"(^id$|_id$|^id_|^codigo$|^code$|^sku$)")
NON_NEGATIVE = re.compile(r"(cantidad|unidades|units|qty|quantity|precio|price|importe|amount|coste|cost|stock|edad|age)")


def quality_report(model_name: str, sl: SemanticLayer, ws: Workspace) -> dict[str, Any]:
    m = sl.model(model_name)
    ref = m["tableReference"]
    fq = f"{quote_ident(ref['catalog'])}.{quote_ident(ref['schema'])}.{quote_ident(ref['table'])}"
    remote = ref["catalog"] != config.LOCAL_CATALOG
    rel = f"(SELECT * FROM {fq} LIMIT 200000)" if remote else fq
    cols = [c for c in m["columns"] if not c.get("isCalculated") and not c.get("relationship")]
    issues: list[dict[str, Any]] = []

    def add(severity: str, kind: str, title: str, detail: str, column: str | None = None, count: int | None = None,
            fix: dict[str, Any] | None = None, examples: list[Any] | None = None) -> None:
        issues.append({"id": uuid.uuid4().hex[:8], "severity": severity, "kind": kind, "column": column,
                       "title": title, "detail": detail, "count": count, "fix": fix, "examples": examples or []})

    n = ws.execute(f"SELECT count(*) FROM {rel}", limit=1).rows[0][0]
    if n == 0:
        add("high", "empty", "The table is empty", "There are no rows to analyze.")
        return {"model": model_name, "rows": 0, "score": 0, "issues": issues, "sampled": remote}

    # Exact duplicates
    distinct = ws.execute(f"SELECT count(*) FROM (SELECT DISTINCT * FROM {rel})", limit=1).rows[0][0]
    if distinct < n:
        add("high", "duplicates", f"{n - distinct:,} duplicate rows",
            "Rows identical in every column: they inflate sums and counts.", count=n - distinct,
            fix={"kind": "drop_duplicates"})

    stats_parts = []
    for c in cols:
        q = quote_ident(c["name"])
        stats_parts += [f"count(*) FILTER (WHERE {q} IS NULL)", f"approx_count_distinct({q})"]
    stats = ws.execute(f"SELECT {', '.join(stats_parts)} FROM {rel}", limit=1).rows[0] if stats_parts else []

    for i, c in enumerate(cols):
        name, typ = c["name"], str(c["type"]).lower()
        q = quote_ident(name)
        nulls, approx = stats[2 * i], stats[2 * i + 1]
        is_id = bool(ID_RE.search(name.lower()))
        # Nulls
        pct = nulls / n
        if nulls and (is_id or pct >= 0.05):
            sev = "high" if is_id or pct >= 0.3 else "medium" if pct >= 0.1 else "low"
            add(sev, "nulls", f"“{name}”: {pct:.0%} blank", f"{nulls:,} rows without a value.", name, nulls,
                fix=None if is_id else {"kind": "fill_nulls", "column": name, "value": "Unknown" if typ.startswith(TEXT_TYPES) else "0"})
        # Duplicate key (an almost unique id that repeats)
        if is_id and approx >= 0.9 * n:
            dup = ws.execute(f"SELECT count(*) - count(DISTINCT {q}) FROM {rel} WHERE {q} IS NOT NULL", limit=1).rows[0][0]
            if dup:
                add("high", "duplicate_key", f"“{name}” repeated in {dup:,} rows",
                    "It looks like a unique identifier but has repeats.", name, dup,
                    fix={"kind": "drop_duplicates", "columns": [name]})
        if typ.startswith(TEXT_TYPES):
            # Extra spaces
            ws_count = ws.execute(f"SELECT count(*) FROM {rel} WHERE {q} <> trim(regexp_replace({q}, '\\s+', ' ', 'g'))", limit=1).rows[0][0]
            if ws_count:
                add("medium", "whitespace", f"“{name}”: extra spaces in {ws_count:,} rows",
                    "Leading/trailing or double spaces: they create different categories for the same value.", name, ws_count,
                    fix={"kind": "trim", "column": name})
            # Numbers / dates stored as text
            nonnull = n - nulls
            if nonnull:
                conv = ws.execute(
                    f"SELECT count(TRY_CAST(replace({q}, ',', '.') AS DOUBLE)), count(TRY_CAST({q} AS DATE)), "
                    f"count(try_strptime({q}, '%d/%m/%Y')), count(TRY_CAST(replace(replace({q}, '.', ''), ',', '.') AS DOUBLE)) "
                    f"FROM {rel} WHERE {q} IS NOT NULL", limit=1).rows[0]
                if conv[1] >= 0.95 * nonnull:
                    add("medium", "date_as_text", f"“{name}” holds dates stored as text",
                        "Stored as text they can't be grouped by month or filtered by period.", name,
                        fix={"kind": "cast", "column": name, "type": "date"})
                elif conv[2] >= 0.95 * nonnull:
                    add("medium", "date_as_text", f"“{name}” holds dd/mm/yyyy dates stored as text",
                        "Convert them to dates to analyze them by period.", name,
                        fix={"kind": "parse_date", "column": name, "format": "%d/%m/%Y"})
                elif max(conv[0], conv[3]) >= 0.95 * nonnull and not is_id:
                    decimal = "," if conv[3] >= conv[0] and ws.execute(
                        f"SELECT count(*) FROM {rel} WHERE {q} LIKE '%,%'", limit=1).rows[0][0] else "."
                    add("medium", "number_as_text", f"“{name}” holds numbers stored as text",
                        "As text they can't be summed or averaged.", name,
                        fix={"kind": "parse_number", "column": name, "decimal": decimal})
            # Categories spelled several ways
            if approx and approx <= 500:
                vals = ws.execute(f"SELECT {q}, count(*) FROM {rel} WHERE {q} IS NOT NULL GROUP BY 1", limit=1000).rows
                groups: dict[str, list[tuple[str, int]]] = {}
                for v, cnt in vals:
                    groups.setdefault(_norm(str(v)), []).append((str(v), cnt))
                variants = {k: g for k, g in groups.items() if len(g) > 1}
                if variants:
                    mapping: dict[str, str] = {}
                    for g in variants.values():
                        canonical = re.sub(r"\s+", " ", max(g, key=_well_formed)[0]).strip()
                        for v, _ in g:
                            if v != canonical:
                                mapping[v] = canonical
                    affected = sum(cnt for g in variants.values() for v, cnt in g if v in mapping)
                    ex = [f"{' / '.join(repr(v) for v, _ in g[:4])}" for g in list(variants.values())[:3]]
                    add("medium", "categories", f"“{name}”: {len(variants)} values spelled several ways",
                        "Different case, accents or spaces for the same thing: they count as different categories.",
                        name, affected, fix={"kind": "map_values", "column": name, "mapping": mapping}, examples=ex)
        elif typ.startswith(NUMERIC_TYPES) and not is_id:
            neg, q1, q3 = ws.execute(
                f"SELECT count(*) FILTER (WHERE {q} < 0), quantile_cont({q}, 0.25), quantile_cont({q}, 0.75) FROM {rel}", limit=1).rows[0]
            if neg and NON_NEGATIVE.search(name.lower()):
                add("medium", "negatives", f"“{name}”: {neg:,} negative values",
                    "Judging by its name this column shouldn't be negative (returns or loading errors?).", name, neg,
                    fix={"kind": "filter_rows", "condition": f"{q} >= 0 OR {q} IS NULL"})
            if q1 is not None and q3 is not None and q3 > q1:
                iqr = q3 - q1
                lo, hi = q1 - 3 * iqr, q3 + 3 * iqr
                out, ex = ws.execute(f"SELECT count(*), (list(DISTINCT {q}))[1:3] FROM {rel} "
                                     f"WHERE {q} < {lo} OR {q} > {hi}", limit=1).rows[0]
                if out:
                    if neg == 0:
                        lo = max(lo, 0.0)  # columns without negatives: the lower bound can't be negative
                    add("low", "outliers", f"“{name}”: {out:,} extreme values",
                        f"Far outside the usual range ({_fmt(lo)} – {_fmt(hi)}). Check whether they're real or errors.", name, out,
                        examples=[_fmt(x) for x in (ex or [])])
        elif typ.startswith(TIME_TYPES):
            fut, mx = ws.execute(f"SELECT count(*) FILTER (WHERE CAST({q} AS DATE) > current_date), CAST(max({q}) AS DATE) FROM {rel}", limit=1).rows[0]
            if fut:
                add("medium", "future_dates", f"“{name}”: {fut:,} dates in the future",
                    "It usually means loading errors or a swapped date format.", name, fut,
                    fix={"kind": "filter_rows", "condition": f"CAST({q} AS DATE) <= current_date OR {q} IS NULL"})
            if mx:
                age = (dt.date.today() - dt.date.fromisoformat(str(mx)[:10])).days
                if age > 45:
                    add("low", "freshness", f"Stale data: the latest record is {age} days old",
                        f"Latest date in “{name}”: {str(mx)[:10]}. Is the source still being updated?", name)
    weights = {"high": 18, "medium": 8, "low": 3}
    score = max(0, 100 - sum(weights[i["severity"]] for i in issues))
    order = {"high": 0, "medium": 1, "low": 2}
    issues.sort(key=lambda i: order[i["severity"]])
    return {"model": model_name, "rows": n, "score": score, "issues": issues, "sampled": remote and n >= 200000}


# ---------------------------------------------------------------------- URL sources
GSHEET_RE = re.compile(r"docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]+)")


def normalize_url(url: str) -> tuple[str, str]:
    """Returns (download url, extension). Google Sheets → CSV export of the given tab (gid)."""
    url = url.strip()
    m = GSHEET_RE.search(url)
    if m:
        gid = re.search(r"[#&?]gid=(\d+)", url)
        return (f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
                + (f"&gid={gid.group(1)}" if gid else "")), ".csv"
    if not url.startswith(("http://", "https://")):
        raise QueryError("The URL must start with http:// or https://")
    path = url.split("?")[0].lower()
    for ext in (".csv", ".tsv", ".xlsx", ".xls", ".parquet", ".json", ".jsonl"):
        if path.endswith(ext):
            return url, ext
    return url, ".csv"


def download(url: str, dest_dir: Path, max_mb: int = 200) -> Path:
    real, ext = normalize_url(url)
    req = urllib.request.Request(real, headers={"User-Agent": "InsightSuite/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            ctype = r.headers.get("content-type", "")
            data = r.read(max_mb * 1024 * 1024 + 1)
    except Exception as e:
        raise QueryError(f"Couldn't download it: {e}") from e
    if len(data) > max_mb * 1024 * 1024:
        raise QueryError(f"The file is larger than {max_mb} MB")
    if "text/html" in ctype and GSHEET_RE.search(url):
        raise QueryError("Google returned a sign-in page: share the sheet as “Anyone with the link (viewer)”.")
    dest = dest_dir / f"{uuid.uuid4().hex[:8]}{ext}"
    dest.write_bytes(data)
    return dest


# ---------------------------------------------------------------------- AI tools
def register_tools(toolbox, prep: "Prep", sl: SemanticLayer, ws: Workspace) -> None:
    from pydantic import ValidationError

    from .tools import Tool, ToolError, ToolOutput, _obj

    step_schema = _obj({
        "kind": {"type": "string", "enum": list(STEP_LABELS)},
        "column": {"type": "string"},
        "columns": {"type": "array", "items": {"type": "string"}, "description": "drop_duplicates: key columns; empty = identical rows"},
        "mode": {"type": "string", "enum": ["upper", "lower", "title"], "description": "case"},
        "mapping": {"type": "object", "additionalProperties": {"type": "string"}, "description": "map_values: original value → unified value"},
        "type": {"type": "string", "enum": ["double", "bigint", "date", "timestamp", "varchar", "boolean"], "description": "cast"},
        "decimal": {"type": "string", "enum": [",", "."], "description": "parse_number: decimal separator"},
        "format": {"type": "string", "description": "parse_date: strptime format, e.g. %d/%m/%Y"},
        "value": {"type": "string", "description": "fill_nulls: fill value"},
        "condition": {"type": "string", "description": "filter_rows: SQL condition for the rows to KEEP"},
        "note": {"type": "string", "description": "Why it's applied (kept in the recipe)"},
    }, ["kind"])

    def parse_step(a: dict[str, Any]) -> Step:
        try:
            return Step(**{k: v for k, v in a["step"].items() if v is not None})
        except ValidationError as e:
            raise ToolError(f"Invalid step: {e.errors(include_url=False)}") from e

    def check(a: dict[str, Any]) -> ToolOutput:
        rep = quality_report(a["model"], sl, ws)
        return ToolOutput(rep, [{"type": "quality", "model": a["model"], "score": rep["score"], "issues": len(rep["issues"])}])

    def preview(a: dict[str, Any]) -> ToolOutput:
        return ToolOutput(prep.preview(a["model"], parse_step(a)))

    def add(a: dict[str, Any]) -> ToolOutput:
        step = parse_step(a)
        pv = prep.preview(a["model"], step)
        prep.add_step(a["model"], step)
        return ToolOutput({"ok": True, "applied": describe_step(step), "effect": pv, "recipe": prep.recipe(a["model"])},
                          [{"type": "semantic_updated"}, {"type": "cleaning", "model": a["model"], "description": describe_step(step),
                                                          "rows_changed": pv.get("rows_changed")}])

    def remove(a: dict[str, Any]) -> ToolOutput:
        prep.remove_step(a["model"], a["step_id"])
        return ToolOutput({"ok": True, "recipe": prep.recipe(a["model"])}, [{"type": "semantic_updated"}])

    toolbox.register(Tool("check_data_quality",
        "Checks the quality of a model: duplicates, nulls, categories spelled several ways, numbers/dates "
        "stored as text, negatives, outliers, future dates and freshness. Returns a 0-100 score and issues with a "
        "suggested 'fix' (a cleanup step ready for add_cleaning_step).",
        _obj({"model": {"type": "string"}}, ["model"]), check))
    toolbox.register(Tool("preview_cleaning_step",
        "Shows the effect of a cleanup step without applying it: rows before/after, changed rows and examples.",
        _obj({"model": {"type": "string"}, "step": step_schema}, ["model", "step"]), preview))
    toolbox.register(Tool("add_cleaning_step",
        "Applies a cleanup step to a model (reversible: it doesn't touch the original data, it creates a clean view). "
        "Apply the safe ones directly (trim, unify spelling variants, convert types, remove identical "
        "rows); to filter or delete rows on business criteria, preview first and ask for confirmation.",
        _obj({"model": {"type": "string"}, "step": step_schema}, ["model", "step"]), add))
    toolbox.register(Tool("remove_cleaning_step", "Undoes a cleanup step from a model's recipe.",
        _obj({"model": {"type": "string"}, "step_id": {"type": "string"}}, ["model", "step_id"]), remove))
