"""Data workspace: DuckDB as the single engine.

- CSV / Excel / Parquet / JSON are imported as tables in the local catalog.
- Postgres is attached live and read-only (DuckDB's postgres extension),
  so a single query can join files and databases.
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
import math
import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

import duckdb
import pandas as pd
import sqlglot
from sqlglot import exp

from . import config, vault

SYSTEM_SCHEMAS = {"information_schema", "pg_catalog", "mysql", "performance_schema", "sys", "pg_toast"}
SYSTEM_CATALOGS = {"system", "temp"}


class QueryError(Exception):
    pass


@dataclass
class QueryResult:
    columns: list[dict[str, str]]
    rows: list[list[Any]]
    row_count: int
    truncated: bool

    def as_records(self, limit: int | None = None) -> list[dict[str, Any]]:
        names = [c["name"] for c in self.columns]
        rows = self.rows if limit is None else self.rows[:limit]
        return [dict(zip(names, r)) for r in rows]


def slugify(name: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z_]+", "_", name.strip().lower()).strip("_")
    if not s:
        s = "t"
    if s[0].isdigit():
        s = f"t_{s}"
    return s[:60]


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def to_jsonable(v: Any) -> Any:
    if v is None or isinstance(v, (str, bool, int)):
        return v
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return v.total_seconds()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray)):
        return f"<{len(v)} bytes>"
    if isinstance(v, (list, tuple)):
        return [to_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): to_jsonable(x) for k, x in v.items()}
    return str(v)


def ensure_read_only(sql: str) -> None:
    """Accepts a single read statement (SELECT / WITH / UNION ...)."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise QueryError(f"Invalid SQL: {e}") from e
    if len(statements) != 1:
        raise QueryError("Only one SQL statement per query is allowed.")
    stmt = statements[0]
    if not isinstance(stmt, exp.Query):
        raise QueryError("Only read queries (SELECT) are allowed.")
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter, exp.Command)
    if any(stmt.find_all(*forbidden)):
        raise QueryError("The query contains write operations.")


ENGINES = {
    # engine: (DuckDB extension, alias prefix, readable name)
    "postgres": ("postgres", "pg_", "PostgreSQL"),
    "mysql": ("mysql", "my_", "MySQL / MariaDB"),
    "sqlite": ("sqlite", "sq_", "SQLite"),
}
ALIAS_PREFIXES = tuple(v[1] for v in ENGINES.values())


def build_dsn(engine: str, f: dict[str, Any]) -> str:
    """Connection string from separate fields (host, port, user…)."""
    if engine == "sqlite":
        return str(f.get("path") or "")
    host, port = f.get("host") or "localhost", f.get("port")
    user, password, db = f.get("user") or "", f.get("password") or "", f.get("database") or ""
    if engine == "postgres":
        auth = quote(user, safe="") + (":" + quote(password, safe="") if password else "")
        q = "?sslmode=require" if f.get("ssl") else ""
        return f"postgresql://{auth}@{host}:{port or 5432}/{quote(db, safe='')}{q}"
    parts = {"host": host, "port": port or 3306, "user": user, "passwd": password, "db": db}
    if f.get("ssl"):
        parts["ssl_mode"] = "required"
    return " ".join(f"{k}={v}" for k, v in parts.items() if v not in ("", None))


def normalize_dsn(engine: str, dsn: str) -> str:
    """Accepts mysql:// URIs and turns them into the key=value format DuckDB's extension uses."""
    dsn = dsn.strip()
    if engine == "mysql" and dsn.startswith(("mysql://", "mariadb://")):
        u = urlparse(dsn)
        qs = parse_qs(u.query)
        return build_dsn("mysql", {"host": u.hostname, "port": u.port, "user": unquote(u.username or ""),
                                   "password": unquote(u.password or ""), "database": u.path.lstrip("/"),
                                   "ssl": any("require" in v.lower() for vals in qs.values() for v in vals)})
    return dsn


def mask_dsn(dsn: str) -> str:
    dsn = re.sub(r"://([^:/@]+):[^@]+@", r"://\1:****@", dsn)
    return re.sub(r"\b(password|passwd)=\S+", r"\1=****", dsn)


CONNECTION_HINTS = [
    (("access denied", "password authentication failed", "authentication failed"), "Wrong user or password."),
    (("unknown database", "does not exist"), "The database doesn't exist (check the name)."),
    (("connection refused", "can't connect", "could not connect", "timed out", "timeout", "no route to host",
      "name or service not known", "could not translate host"),
     "The server can't be reached: check the host and port, the firewall/allowed IPs or the VPN."),
    (("ssl", "tls"), "SSL problem: try turning “Require SSL” on or off."),
    (("unable to open database file", "no such file"), "The database file can't be found."),
]


def connection_error(e: Exception, dsn: str) -> str:
    """A readable connection error WITHOUT credentials (DuckDB puts the whole string in the message)."""
    raw = clean_error(e)
    detail = mask_dsn(raw.replace(dsn, mask_dsn(dsn)))
    secret = re.search(r"(?:passwd|password)=(\S+)", dsn) or re.search(r"://[^:/@]+:([^@]+)@", dsn)
    if secret:
        detail = detail.replace(secret.group(1), "****").replace(unquote(secret.group(1)), "****")
    low = raw.lower()
    hint = next((h for keys, h in CONNECTION_HINTS if any(k in low for k in keys)), "")
    return f"Couldn't connect. {hint} Detail: {detail}".replace("  ", " ")


class Workspace:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.conn = duckdb.connect(str(config.DUCKDB_FILE))
        self.sources: list[dict[str, Any]] = self._load_sources()
        self._reattach()

    # ------------------------------------------------------------------ sources
    def _load_sources(self) -> list[dict[str, Any]]:
        if not config.SOURCES_FILE.exists():
            return []
        sources = json.loads(config.SOURCES_FILE.read_text())
        plaintext = False
        for s in sources:
            if s.get("dsn"):
                plaintext = plaintext or not s["dsn"].startswith(vault.PREFIX)
                s["dsn"] = vault.decrypt(s["dsn"])
        if plaintext:  # migration: old plaintext passwords → encrypted
            self.sources = sources
            self._save_sources()
        return sources

    def _save_sources(self) -> None:
        stored = [{**s, "dsn": vault.encrypt(s["dsn"])} if s.get("dsn") else s for s in self.sources]
        config.SOURCES_FILE.write_text(json.dumps(stored, indent=2, ensure_ascii=False))
        try:
            config.SOURCES_FILE.chmod(0o600)
        except OSError:
            pass

    def _reattach(self) -> None:
        for src in self.sources:
            if src["type"] in ENGINES:
                try:
                    self._attach(src["type"], src["alias"], src["dsn"])
                    src.pop("error", None)
                except Exception as e:  # the database may be unavailable at startup
                    src["error"] = connection_error(e, src["dsn"])

    def _attach(self, engine: str, alias: str, dsn: str) -> None:
        ext = ENGINES[engine][0]
        with self._lock:
            self.conn.execute(f"INSTALL {ext}; LOAD {ext};")
            if ext == "mysql":
                # Extension bug (DuckDB 1.5): aggregate pushdown breaks COUNT(*) over subqueries,
                # which is exactly what the semantic layer generates. Aggregation happens in DuckDB.
                self.conn.execute("SET GLOBAL mysql_aggregate_pushdown_enabled = false")
            attached = {r[0] for r in self.conn.execute("SELECT database_name FROM duckdb_databases()").fetchall()}
            if alias in attached:
                self.conn.execute(f"DETACH {quote_ident(alias)}")
            self.conn.execute(f"ATTACH {sql_literal(dsn)} AS {quote_ident(alias)} (TYPE {ext}, READ_ONLY)")

    def _schemas_of(self, alias: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT table_schema, count(*) FROM information_schema.tables WHERE table_catalog = ? GROUP BY 1 ORDER BY 1",
            [alias]).fetchall()
        return [{"schema": s, "tables": n} for s, n in rows if s not in SYSTEM_SCHEMAS]

    def inspect(self, engine: str, dsn: str) -> list[dict[str, Any]]:
        """Tests a connection and returns its schemas with their table count, without saving it."""
        if engine not in ENGINES:
            raise QueryError(f"Unsupported engine: {engine}")
        alias = f"__probe_{uuid.uuid4().hex[:8]}"
        with self._lock:
            try:
                self._attach(engine, alias, normalize_dsn(engine, dsn))
                return self._schemas_of(alias)
            except duckdb.Error as e:
                raise QueryError(connection_error(e, normalize_dsn(engine, dsn))) from e
            finally:
                self.conn.execute(f"DETACH DATABASE IF EXISTS {quote_ident(alias)}")

    def add_database(self, engine: str, name: str, dsn: str, schemas: list[str] | None = None) -> dict[str, Any]:
        if engine not in ENGINES:
            raise QueryError(f"Unsupported engine: {engine}")
        dsn = normalize_dsn(engine, dsn)
        alias = ENGINES[engine][1] + slugify(name)
        try:
            self._attach(engine, alias, dsn)
        except duckdb.Error as e:
            raise QueryError(connection_error(e, dsn)) from e
        self.sources = [s for s in self.sources if s.get("alias") != alias]
        src = {"type": engine, "name": name, "alias": alias, "dsn": dsn, "schemas": schemas or None}
        self.sources.append(src)
        self._save_sources()
        return self.public_source(src)

    def add_postgres(self, name: str, dsn: str) -> dict[str, Any]:
        return self.add_database("postgres", name, dsn)

    def set_schemas(self, alias: str, schemas: list[str] | None) -> None:
        src = next((s for s in self.sources if s.get("alias") == alias), None)
        if src is None:
            raise KeyError(alias)
        src["schemas"] = schemas or None
        self._save_sources()

    def remove_source(self, source_id: str) -> None:
        src = next((s for s in self.sources if s.get("alias") == source_id or s.get("table") == source_id), None)
        if src is None:
            raise KeyError(source_id)
        with self._lock:
            if src["type"] in ENGINES:
                self.conn.execute(f"DETACH DATABASE IF EXISTS {quote_ident(src['alias'])}")
            else:
                self.conn.execute(f"DROP TABLE IF EXISTS {config.LOCAL_CATALOG}.main.{quote_ident(src['table'])}")
        self.sources.remove(src)
        self._save_sources()

    def public_source(self, src: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in src.items() if k != "dsn"}
        if src.get("dsn"):
            out["dsn"] = mask_dsn(src["dsn"]) if src["type"] != "sqlite" else Path(src["dsn"]).name
        if src["type"] in ENGINES:
            out["engine_name"] = ENGINES[src["type"]][2]
            if "error" not in src:
                try:
                    with self._lock:
                        out["available_schemas"] = self._schemas_of(src["alias"])
                except duckdb.Error:
                    out["available_schemas"] = []
        return out

    def list_sources(self) -> list[dict[str, Any]]:
        return [self.public_source(s) for s in self.sources]

    # ------------------------------------------------------------------ ingest
    def ingest_file(self, path: Path, original_name: str) -> list[str]:
        """Imports a file as table(s). Returns the names of the tables created."""
        suffix = path.suffix.lower()
        base = slugify(Path(original_name).stem)
        created: list[str] = []
        with self._lock:
            if suffix in {".csv", ".tsv", ".txt"}:
                created.append(self._create_from(base, f"read_csv_auto({sql_literal(str(path))}, sample_size=-1)"))
            elif suffix == ".parquet":
                created.append(self._create_from(base, f"read_parquet({sql_literal(str(path))})"))
            elif suffix in {".json", ".jsonl", ".ndjson"}:
                created.append(self._create_from(base, f"read_json_auto({sql_literal(str(path))})"))
            elif suffix in {".xlsx", ".xlsm", ".xls"}:
                sheets = pd.read_excel(path, sheet_name=None)
                for sheet, df in sheets.items():
                    if df.empty:
                        continue
                    df.columns = [str(c) for c in df.columns]
                    name = base if len(sheets) == 1 else f"{base}_{slugify(str(sheet))}"
                    self.conn.register("__df_import", df)
                    try:
                        created.append(self._create_from(name, "__df_import"))
                    finally:
                        self.conn.unregister("__df_import")
            else:
                raise QueryError(f"Unsupported format: {suffix}")
        for table in created:
            self.sources = [s for s in self.sources if s.get("table") != table]
            self.sources.append({"type": "file", "name": original_name, "table": table, "file": path.name})
        self._save_sources()
        return created

    def _create_from(self, table: str, relation: str) -> str:
        fq = f"{config.LOCAL_CATALOG}.main.{quote_ident(table)}"
        self.conn.execute(f"CREATE OR REPLACE TABLE {fq} AS SELECT * FROM {relation}")
        return table

    def add_url_source(self, name: str, url: str, refresh_minutes: int | None = None) -> list[str]:
        from .dataprep import download, normalize_url
        path = download(url, config.UPLOADS_DIR)
        _, ext = normalize_url(url)
        tables = self.ingest_file(path, f"{name}{ext}")
        kind = "gsheet" if "docs.google.com/spreadsheets" in url else "url"
        now = dt.datetime.now().isoformat(timespec="seconds")
        for s in self.sources:
            if s.get("table") in tables and s["type"] == "file" and s.get("file") == path.name:
                s.update(type=kind, url=url, refresh_minutes=refresh_minutes, last_refreshed=now, name=name)
        self._save_sources()
        return tables

    def refresh_url_source(self, table: str) -> list[str]:
        src = next((s for s in self.sources if s.get("table") == table and s["type"] in {"url", "gsheet"}), None)
        if src is None:
            raise KeyError(table)
        old_file = config.UPLOADS_DIR / src.get("file", "")
        tables = self.add_url_source(src["name"], src["url"], src.get("refresh_minutes"))
        self.sources = [s for s in self.sources if not (s is src)]  # add_url_source already left the new entry
        if old_file.is_file() and old_file.name != next((s.get("file") for s in self.sources if s.get("table") == table), None):
            old_file.unlink(missing_ok=True)
        self._save_sources()
        return tables

    def due_url_sources(self) -> list[str]:
        now = dt.datetime.now()
        due = []
        for s in self.sources:
            if s["type"] in {"url", "gsheet"} and s.get("refresh_minutes"):
                last = dt.datetime.fromisoformat(s.get("last_refreshed") or "2000-01-01T00:00:00")
                if (now - last).total_seconds() >= s["refresh_minutes"] * 60:
                    due.append(s["table"])
        return due

    # ------------------------------------------------------------------ catalog
    def list_tables(self) -> list[dict[str, Any]]:
        sql = """
            SELECT table_catalog, table_schema, table_name
            FROM information_schema.tables
            WHERE table_type IN ('BASE TABLE', 'VIEW')
            ORDER BY 1, 2, 3
        """
        with self._lock:
            rows = self.conn.execute(sql).fetchall()
        allowed = {src["alias"]: set(src["schemas"]) for src in self.sources if src.get("schemas")}
        return [
            {"catalog": c, "schema": s, "table": t}
            for c, s, t in rows
            if c not in SYSTEM_CATALOGS and s not in SYSTEM_SCHEMAS and not t.startswith("__")
            and not c.startswith("__probe_") and (c not in allowed or s in allowed[c])
        ]

    def columns(self, catalog: str, schema: str, table: str) -> list[dict[str, str]]:
        sql = """
            SELECT column_name, data_type FROM information_schema.columns
            WHERE table_catalog = ? AND table_schema = ? AND table_name = ?
            ORDER BY ordinal_position
        """
        with self._lock:
            rows = self.conn.execute(sql, [catalog, schema, table]).fetchall()
        return [{"name": n, "type": t} for n, t in rows]

    def profile(self, catalog: str, schema: str, table: str, sample_rows: int = 5) -> dict[str, Any]:
        fq = f"{quote_ident(catalog)}.{quote_ident(schema)}.{quote_ident(table)}"
        is_remote = catalog != config.LOCAL_CATALOG
        # On remote sources we profile a sample so huge tables aren't scanned.
        rel = f"(SELECT * FROM {fq} LIMIT 200000)" if is_remote else fq
        with self._lock:
            cur = self.conn.cursor()
            row_count = cur.execute(f"SELECT count(*) FROM {fq}").fetchone()[0]
            summary = cur.execute(f"SUMMARIZE {rel}").fetchdf()
            sample = cur.execute(f"SELECT * FROM {fq} LIMIT {int(sample_rows)}")
            cols = [d[0] for d in sample.description]
            sample_rows_data = [[to_jsonable(v) for v in r] for r in sample.fetchall()]
        stats = []
        for rec in summary.to_dict(orient="records"):
            stats.append({
                "column": rec["column_name"],
                "type": rec["column_type"],
                "min": to_jsonable(rec.get("min")),
                "max": to_jsonable(rec.get("max")),
                "distinct_approx": to_jsonable(rec.get("approx_unique")),
                "avg": to_jsonable(rec.get("avg")),
                "null_pct": to_jsonable(rec.get("null_percentage")),
            })
        return {
            "table": f"{catalog}.{schema}.{table}",
            "row_count": row_count,
            "profiled_on_sample": is_remote and row_count > 200000,
            "columns": stats,
            "sample": {"columns": cols, "rows": sample_rows_data},
        }

    # ------------------------------------------------------------------ query
    def execute(self, sql: str, limit: int = config.MAX_ROWS_TO_UI) -> QueryResult:
        with self._lock:
            cur = self.conn.cursor()
            try:
                rel = cur.execute(sql)
            except duckdb.Error as e:
                raise QueryError(clean_error(e)) from e
            columns = [{"name": d[0], "type": str(d[1])} for d in rel.description]
            fetched = rel.fetchmany(limit + 1)
        truncated = len(fetched) > limit
        rows = [[to_jsonable(v) for v in r] for r in fetched[:limit]]
        return QueryResult(columns=columns, rows=rows, row_count=len(rows), truncated=truncated)


def clean_error(e: Exception) -> str:
    """DuckDB's message without the internal trace or the echoed SQL."""
    msg = str(e).split("\n\nLINE")[0].split("\n\nStack Trace")[0].split("\nStack Trace")[0]
    return msg.strip()[:600]


def sql_literal(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"
