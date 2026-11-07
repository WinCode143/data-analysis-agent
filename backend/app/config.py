"""Central configuration, read from environment variables (and an optional .env)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

WORKSPACE_DIR = Path(os.getenv("INSIGHT_WORKSPACE_DIR", ROOT_DIR / "workspace")).resolve()
UPLOADS_DIR = WORKSPACE_DIR / "uploads"
CHARTS_DIR = WORKSPACE_DIR / "charts"
DASHBOARDS_DIR = WORKSPACE_DIR / "dashboards"
CONVERSATIONS_DIR = WORKSPACE_DIR / "conversations"
SEMANTIC_FILE = WORKSPACE_DIR / "semantic" / "mdl.json"
SOURCES_FILE = WORKSPACE_DIR / "sources.json"
DUCKDB_FILE = WORKSPACE_DIR / "workspace.duckdb"

# Name of the DuckDB catalog for imported tables (CSV/Excel).
LOCAL_CATALOG = "workspace"

CLAUDE_MODEL = os.getenv("INSIGHT_MODEL", "claude-opus-5")

# Base URL of this backend, used for the links in notifications (alerts and reports).
PUBLIC_URL = os.getenv("INSIGHT_PUBLIC_URL", "http://127.0.0.1:8000")
CLAUDE_EFFORT = os.getenv("INSIGHT_EFFORT", "high")

# Maximum rows returned to the model and to the frontend per query.
MAX_ROWS_TO_MODEL = int(os.getenv("INSIGHT_MAX_ROWS_TO_MODEL", "100"))
MAX_ROWS_TO_UI = int(os.getenv("INSIGHT_MAX_ROWS_TO_UI", "5000"))

# Running AI-generated Python. It runs in a local subprocess WITHOUT real isolation,
# which is why it's off by default.
ENABLE_PYTHON = os.getenv("INSIGHT_ENABLE_PYTHON", "false").lower() in {"1", "true", "yes"}
PYTHON_TIMEOUT_S = int(os.getenv("INSIGHT_PYTHON_TIMEOUT_S", "60"))

for d in (UPLOADS_DIR, CHARTS_DIR, DASHBOARDS_DIR, CONVERSATIONS_DIR, SEMANTIC_FILE.parent):
    d.mkdir(parents=True, exist_ok=True)
