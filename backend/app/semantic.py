"""Semantic layer.

The rest of the suite only talks to `SemanticLayer` (models, relationships, calculated
fields, `query(sql)`). Today the implementation uses Wren's engine (wren-core,
Apache 2.0) to translate semantic SQL into DuckDB SQL; if it's swapped for Cube or a
custom engine some day, only this file changes.
"""

from __future__ import annotations

import base64
import copy
import json
import re
import threading
from typing import Any

from wren import DataSource, WrenEngine

from . import config
from .workspace import QueryError, QueryResult, Workspace, ensure_read_only, slugify

JOIN_TYPES = {"ONE_TO_ONE", "ONE_TO_MANY", "MANY_TO_ONE", "MANY_TO_MANY"}
NUMERIC_TYPES = ("double", "float", "decimal", "numeric", "bigint", "int", "integer", "smallint", "tinyint",
                 "real", "hugeint", "ubigint", "uinteger")
TIME_TYPES = ("date", "timestamp", "timestamptz", "datetime")
# Column names in English and Spanish
GEO_HINTS = ("pais", "país", "country", "region", "región", "ciudad", "city", "provincia", "state", "estado",
             "comuna", "municipio", "zona", "continent", "lat", "lon", "lng", "codigo_postal", "zip")


def wren_type(duck_type: str) -> str:
    """Normalizes a DuckDB type to a name wren-core understands."""
    t = duck_type.strip().lower()
    if t.endswith("[]") or t.startswith(("struct", "map", "union")):
        return "varchar"
    t = re.sub(r"\(.*\)", "", t).strip()
    return {"timestamp with time zone": "timestamptz", "timestamp_s": "timestamp", "timestamp_ms": "timestamp"}.get(t, t)


class SemanticError(Exception):
    pass


class SemanticLayer:
    def __init__(self, workspace: Workspace) -> None:
        self.ws = workspace
        self._lock = threading.RLock()
        self._engine: WrenEngine | None = None
        self._stats: dict[str, dict[str, Any]] = {}
        self.extra_describers: list = []  # e.g. the metrics catalog
        # Tables replaced by a cleanup view (dataprep recipes). They're read here already because sync() runs
        # in the constructor, before the preparation module exists.
        prep_file = config.WORKSPACE_DIR / "prep.json"
        self.shadowed_sources: set[tuple[str, str, str]] = {
            (r["source"]["catalog"], r["source"]["schema"], r["source"]["table"])
            for r in (json.loads(prep_file.read_text()) if prep_file.exists() else [])}
        self.mdl: dict[str, Any] = self._load()
        self.sync()

    # ------------------------------------------------------------------ persistence
    def _load(self) -> dict[str, Any]:
        if config.SEMANTIC_FILE.exists():
            return json.loads(config.SEMANTIC_FILE.read_text())
        return {"catalog": "wren", "schema": "public", "models": [], "relationships": [], "views": []}

    def _save(self) -> None:
        config.SEMANTIC_FILE.write_text(json.dumps(self.mdl, indent=2, ensure_ascii=False))
        self._engine = None

    def _engine_for(self, mdl: dict[str, Any]) -> WrenEngine:
        manifest = base64.b64encode(json.dumps(mdl).encode()).decode()
        return WrenEngine(manifest, DataSource.duckdb, {}, fallback=False)

    @property
    def engine(self) -> WrenEngine:
        if self._engine is None:
            self._engine = self._engine_for(self.mdl)
        return self._engine

    # ------------------------------------------------------------------ lookup
    def model(self, name: str) -> dict[str, Any]:
        for m in self.mdl["models"]:
            if m["name"] == name:
                return m
        raise SemanticError(f"There's no model '{name}'. Models: {', '.join(self.model_names())}")

    def model_names(self) -> list[str]:
        return [m["name"] for m in self.mdl["models"]]

    def model_for_table(self, catalog: str, schema: str, table: str) -> dict[str, Any] | None:
        for m in self.mdl["models"]:
            ref = m["tableReference"]
            if (ref.get("catalog"), ref.get("schema"), ref.get("table")) == (catalog, schema, table):
                return m
        return None

    # ------------------------------------------------------------------ sync with workspace
    def sync(self) -> dict[str, list[str]]:
        """Creates models for new tables, updates columns and removes orphan models."""
        with self._lock:
            tables = self.ws.list_tables()
            present = {(t["catalog"], t["schema"], t["table"]) for t in tables}
            added, removed = [], []

            kept = []
            for m in self.mdl["models"]:
                ref = m["tableReference"]
                if (ref["catalog"], ref["schema"], ref["table"]) in present:
                    kept.append(m)
                else:
                    removed.append(m["name"])
            self.mdl["models"] = kept
            if removed:
                gone = set(removed)
                self.mdl["relationships"] = [r for r in self.mdl["relationships"] if not gone & set(r["models"])]
                for m in self.mdl["models"]:
                    m["columns"] = [c for c in m["columns"] if c.get("type") not in gone]

            schemas_by_catalog: dict[str, set[str]] = {}
            for t in tables:
                schemas_by_catalog.setdefault(t["catalog"], set()).add(t["schema"])
            for t in tables:
                cols = self.ws.columns(t["catalog"], t["schema"], t["table"])
                existing = self.model_for_table(t["catalog"], t["schema"], t["table"])
                key = (t["catalog"], t["schema"], t["table"])
                if existing is None and (key in self.shadowed_sources
                                         or (t["catalog"] == config.LOCAL_CATALOG and t["schema"] == "prep")):
                    continue  # its model reads from the clean view (or it's an orphan cleanup view)
                if existing is None:
                    name = self._unique_name(self._default_name(t, len(schemas_by_catalog[t["catalog"]]) == 1))
                    self.mdl["models"].append({
                        "name": name,
                        "tableReference": {"catalog": t["catalog"], "schema": t["schema"], "table": t["table"]},
                        "columns": [{"name": c["name"], "type": wren_type(c["type"])} for c in cols],
                        "properties": {"description": ""},
                    })
                    added.append(name)
                else:
                    self._refresh_columns(existing, cols)
            self._stats.clear()
            self._save()
            return {"added": added, "removed": removed}

    def _default_name(self, t: dict[str, str], single_schema: bool = False) -> str:
        if t["catalog"] == config.LOCAL_CATALOG:
            return slugify(t["table"])
        prefix = t["catalog"]
        for p in ("pg_", "my_", "sq_"):
            prefix = prefix.removeprefix(p)
        schema = "" if single_schema or t["schema"] in ("public", "main") else f"{t['schema']}_"
        return slugify(f"{prefix}_{schema}{t['table']}")

    def _unique_name(self, base: str) -> str:
        names = set(self.model_names())
        name, i = base, 2
        while name in names:
            name, i = f"{base}_{i}", i + 1
        return name

    @staticmethod
    def _refresh_columns(model: dict[str, Any], cols: list[dict[str, str]]) -> None:
        physical = {c["name"]: c for c in model["columns"] if not c.get("isCalculated") and not c.get("relationship")}
        derived = [c for c in model["columns"] if c.get("isCalculated") or c.get("relationship")]
        new_cols = []
        for c in cols:
            prev = physical.get(c["name"], {})
            col = {"name": c["name"], "type": wren_type(c["type"])}
            if prev.get("properties"):
                col["properties"] = prev["properties"]
            new_cols.append(col)
        model["columns"] = new_cols + derived

    # ------------------------------------------------------------------ edits (validadas)
    def _apply(self, mutate) -> None:
        """Applies a change to a copy, validates it with the engine and only then saves it."""
        with self._lock:
            candidate = copy.deepcopy(self.mdl)
            mutate(candidate)
            engine = self._engine_for(candidate)
            for m in candidate["models"]:
                try:
                    engine.dry_plan(f'SELECT * FROM "{m["name"]}" LIMIT 1')
                except Exception as e:
                    raise SemanticError(f"The change breaks the model '{m['name']}': {e}") from e
            self.mdl = candidate
            self._save()

    def set_description(self, model: str, description: str, column: str | None = None) -> None:
        def mutate(mdl):
            m = next((x for x in mdl["models"] if x["name"] == model), None)
            if m is None:
                raise SemanticError(f"There's no model '{model}'")
            if column is None:
                m.setdefault("properties", {})["description"] = description
                return
            c = next((x for x in m["columns"] if x["name"] == column), None)
            if c is None:
                raise SemanticError(f"There's no column '{column}' in '{model}'")
            c.setdefault("properties", {})["description"] = description

        self._apply(mutate)

    def add_calculated_field(self, model: str, name: str, expression: str, type_: str, description: str = "") -> None:
        def mutate(mdl):
            m = next((x for x in mdl["models"] if x["name"] == model), None)
            if m is None:
                raise SemanticError(f"There's no model '{model}'")
            m["columns"] = [c for c in m["columns"] if c["name"] != name]
            m["columns"].append({
                "name": name, "type": wren_type(type_), "isCalculated": True,
                "expression": expression, "properties": {"description": description},
            })

        self._apply(mutate)

    def add_relationship(self, from_model: str, to_model: str, join_type: str, condition: str, name: str | None = None) -> str:
        join_type = join_type.upper()
        if join_type not in JOIN_TYPES:
            raise SemanticError(f"join_type must be one of {sorted(JOIN_TYPES)}")
        rel_name = name or slugify(f"{from_model}_{to_model}")

        def mutate(mdl):
            names = {x["name"] for x in mdl["models"]}
            for n in (from_model, to_model):
                if n not in names:
                    raise SemanticError(f"There's no model '{n}'")
            mdl["relationships"] = [r for r in mdl["relationships"] if r["name"] != rel_name]
            mdl["relationships"].append({"name": rel_name, "models": [from_model, to_model], "joinType": join_type, "condition": condition})
            # Relationship column on the source model: it allows calculated fields such as `customer.country`.
            src = next(x for x in mdl["models"] if x["name"] == from_model)
            col_name = to_model if to_model not in {c["name"] for c in src["columns"] if not c.get("relationship")} else f"{to_model}_rel"
            src["columns"] = [c for c in src["columns"] if c.get("relationship") != rel_name]
            src["columns"].append({"name": col_name, "type": to_model, "relationship": rel_name})

        self._apply(mutate)
        return rel_name

    def remove_field(self, model: str, name: str) -> None:
        def mutate(mdl):
            m = next((x for x in mdl["models"] if x["name"] == model), None)
            if m is None:
                raise SemanticError(f"There's no model '{model}'")
            target = next((c for c in m["columns"] if c["name"] == name), None)
            if target is None or not (target.get("isCalculated") or target.get("relationship")):
                raise SemanticError("Only calculated or relationship fields can be removed")
            m["columns"].remove(target)
            if target.get("relationship"):
                mdl["relationships"] = [r for r in mdl["relationships"] if r["name"] != target["relationship"]]

        self._apply(mutate)

    # ------------------------------------------------------------------ query
    def plan(self, sql: str) -> str:
        ensure_read_only(sql)
        try:
            return self.engine.dry_plan(sql)
        except Exception as e:
            msg = str(e)
            if "No model or view references" in msg:
                msg += f". Use the model names of the semantic layer: {', '.join(self.model_names())}"
            raise QueryError(msg) from e

    def query(self, sql: str, limit: int = config.MAX_ROWS_TO_UI) -> tuple[QueryResult, str]:
        planned = self.plan(sql)
        return self.ws.execute(planned, limit=limit), planned

    # ------------------------------------------------------------------ describe
    def stats(self, model: dict[str, Any]) -> dict[str, Any]:
        """Rows, cardinality and time range of the physical columns (cached; on remote sources, over a sample)."""
        name = model["name"]
        if name in self._stats:
            return self._stats[name]
        ref = model["tableReference"]
        fq = f'"{ref["catalog"]}"."{ref["schema"]}"."{ref["table"]}"'
        cols = [c for c in model["columns"] if not c.get("isCalculated") and not c.get("relationship")]
        parts = ["count(*)"]
        for c in cols:
            q = '"' + c["name"].replace('"', '""') + '"'
            parts.append(f"approx_count_distinct({q})")
            if str(c["type"]).startswith(TIME_TYPES):
                parts += [f"min({q})::VARCHAR", f"max({q})::VARCHAR"]
        sample = "" if ref["catalog"] == config.LOCAL_CATALOG else " LIMIT 200000"
        out: dict[str, Any] = {"rows": None, "columns": {}}
        try:
            row = self.ws.execute(f"SELECT {', '.join(parts)} FROM (SELECT * FROM {fq}{sample})", limit=1).rows[0]
            out["rows"] = row[0]
            i = 1
            for c in cols:
                info = {"distinct": row[i]}
                i += 1
                if str(c["type"]).startswith(TIME_TYPES):
                    info["min"], info["max"] = (row[i] or "")[:10], (row[i + 1] or "")[:10]
                    i += 2
                out["columns"][c["name"]] = info
        except Exception:
            pass  # no stats: the description is still useful
        self._stats[name] = out
        return out

    @staticmethod
    def column_role(col: dict[str, Any], info: dict[str, Any], rows: int | None) -> str:
        name, typ = col["name"].lower(), str(col["type"])
        distinct = info.get("distinct")
        if typ.startswith(TIME_TYPES):
            return f"time {info.get('min', '?')} → {info.get('max', '?')}" if info.get("min") else "time"
        if name == "id" or name.endswith("_id") or name.startswith("id_"):
            return "identifier"
        near_unique = bool(rows and distinct and distinct >= 0.95 * rows and rows > 12)
        if near_unique and typ.startswith(("varchar", "text", "char", "string")):
            return f"unique label per row ({distinct} values: useful for top-N and detail)"
        if near_unique and not typ.startswith(("double", "float", "decimal")):
            return "identifier"
        if typ.startswith(NUMERIC_TYPES):
            if distinct is not None and distinct <= 12 and not typ.startswith(("double", "float", "decimal")):
                return f"discrete measure ({distinct} values)"
            return "measure"
        if typ.startswith("bool"):
            return "yes/no flag"
        geo = any(h in name for h in GEO_HINTS)
        if distinct is None:
            return "geographic dimension" if geo else "dimension"
        size = "low" if distinct <= 12 else "medium" if distinct <= 60 else "high"
        return f"{'geographic ' if geo else ''}dimension, {distinct} values ({size} cardinality)"

    def describe(self) -> str:
        """Compact description of the semantic layer for the agent prompt, with each column's role."""
        if not self.mdl["models"]:
            return "(No data loaded yet.)"
        lines = []
        for m in self.mdl["models"]:
            ref = m["tableReference"]
            st = self.stats(m)
            desc = m.get("properties", {}).get("description") or ""
            rows = (", " + f"{st['rows']:,}" + " rows") if st.get("rows") is not None else ""
            lines.append(f"### {m['name']}  (source: {ref['catalog']}.{ref['schema']}.{ref['table']}{rows})" + (f" — {desc}" if desc else ""))
            for c in m["columns"]:
                cd = c.get("properties", {}).get("description") or ""
                if c.get("relationship"):
                    lines.append(f"- {c['name']}: relationship → {c['type']} ({c['relationship']})")
                    continue
                if c.get("isCalculated"):
                    role = "calculated measure" if str(c["type"]).startswith(NUMERIC_TYPES) else "calculated field"
                    lines.append(f"- {c['name']} {c['type']} = {c['expression']} [{role}]" + (f" — {cd}" if cd else ""))
                    continue
                role = self.column_role(c, st["columns"].get(c["name"], {}), st.get("rows"))
                lines.append(f"- {c['name']} {c['type']} [{role}]" + (f" — {cd}" if cd else ""))
        if self.mdl["relationships"]:
            lines.append("\n### Relationships")
            for r in self.mdl["relationships"]:
                lines.append(f"- {r['name']}: {r['models'][0]} → {r['models'][1]} {r['joinType']} ON {r['condition']}")
        for extra in self.extra_describers:
            text = extra()
            if text:
                lines.append(text)
        return "\n".join(lines)
