"""Question suggestions from the semantic layer (without calling any model)."""

from __future__ import annotations

from .filters import time_column
from .semantic import SemanticLayer

NUMERIC = ("double", "float", "decimal", "numeric", "bigint", "int", "integer", "smallint", "real", "hugeint")


def _is_id(name: str) -> bool:
    n = name.lower()
    return n == "id" or n.endswith("_id") or n.startswith("id_")


def suggestions(sl: SemanticLayer, limit: int = 4) -> list[str]:
    out: list[str] = []
    models = sl.mdl["models"]
    if not models:
        return []
    # The "main" model: the one with the most derived columns and a time column.
    main = max(models, key=lambda m: (time_column(m) is not None,
                                      sum(1 for c in m["columns"] if c.get("isCalculated")), len(m["columns"])))
    cols = [c for c in main["columns"] if not c.get("relationship")]
    measures = [c["name"] for c in cols if str(c["type"]).startswith(NUMERIC) and not _is_id(c["name"])]
    calc_measures = [c["name"] for c in cols if c.get("isCalculated") and str(c["type"]).startswith(NUMERIC)]
    dims = [c["name"] for c in cols if str(c["type"]).startswith(("varchar", "text")) and not _is_id(c["name"])]
    measure = (calc_measures or measures or [None])[0]
    tcol = time_column(main)
    name = main["name"].replace("_", " ")

    if measure and tcol:
        out.append(f"Analyze how {measure.replace('_', ' ')} evolves in {name} and explain the most important changes")
    if measure and dims:
        out.append(f"Which {dims[0].replace('_', ' ')} brings in the most {measure.replace('_', ' ')}? Compare them")
    out.append(f"Create an executive dashboard of {name}")
    if len(dims) > 1 and measure:
        out.append(f"Look for anomalies in {measure.replace('_', ' ')} by {dims[1].replace('_', ' ')}")
    others = [m["name"] for m in models if m is not main]
    if others:
        out.append(f"How are {name} and {others[0].replace('_', ' ')} related?")
    out.append("Give me a summary of the available data")
    return out[:limit]
