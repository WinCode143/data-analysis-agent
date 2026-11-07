"""Loads the demo data into a running suite (http://127.0.0.1:8000).

Requires `python scripts/generate_demo.py` and `docker compose up -d` (the demo Postgres).
It uploads sales.csv and products.xlsx, connects the Postgres database and seeds the semantic layer
with relationships and business metrics so questions work well from the start.
"""

from __future__ import annotations

import json
import sys
import urllib.request
import uuid
from pathlib import Path

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
DEMO = Path(__file__).resolve().parents[1] / "demo"


def call(method: str, path: str, body: dict | None = None, raw: bytes | None = None, ctype: str = "application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(API + path, data=data, method=method, headers={"Content-Type": ctype})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def upload(path: Path):
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    return call("POST", "/api/sources/upload", raw=body, ctype=f"multipart/form-data; boundary={boundary}")


print(upload(DEMO / "sales.csv"))
print(upload(DEMO / "products.xlsx"))
print(call("POST", "/api/sources/postgres", {"name": "shop", "dsn": "postgresql://demo:demo@localhost:5442/shop"}))

# Semantic layer seed (the same thing the agent can do with edit_semantic_layer).
seed = [
    ("set_description", {"model": "sales", "description": "One row per order line (online shop, physical stores and marketplace)."}),
    ("add_relationship", {"model": "sales", "to_model": "products", "join_type": "MANY_TO_ONE",
                          "condition": "sales.product_id = products.product_id"}),
    ("add_relationship", {"model": "sales", "to_model": "shop_customers", "join_type": "MANY_TO_ONE",
                          "condition": "sales.customer_id = shop_customers.customer_id"}),
    ("add_calculated_field", {"model": "sales", "name": "net_revenue", "type": "double",
                              "expression": "quantity * unit_price * (1 - discount)",
                              "description": "Line revenue after discount"}),
    ("add_calculated_field", {"model": "sales", "name": "category", "type": "varchar",
                              "expression": "products.category", "description": "Product category"}),
    ("add_calculated_field", {"model": "sales", "name": "margin", "type": "double",
                              "expression": "quantity * (unit_price * (1 - discount) - products.unit_cost)",
                              "description": "Gross margin of the line"}),
    ("add_calculated_field", {"model": "sales", "name": "customer_segment", "type": "varchar",
                              "expression": "shop_customers.segment", "description": "Consumer / Small business / Enterprise"}),
    ("add_calculated_field", {"model": "sales", "name": "customer_country", "type": "varchar",
                              "expression": "shop_customers.country"}),
]
for action, args in seed:
    r = call("POST", "/api/semantic/edit", {"action": action, **args})
    print(action, args.get("name") or args.get("to_model") or "", "→ ok" if r.get("ok") else r)

# Example official metrics (they're validated against the data when saved).
METRICS = [
    {"name": "revenue", "label": "Net revenue", "model": "sales", "expression": "sum(net_revenue)",
     "format": "currency", "currency": "USD", "certified": True, "description": "Revenue after discounts"},
    {"name": "orders", "label": "Orders", "model": "sales", "expression": "count(DISTINCT order_id)",
     "format": "integer", "certified": True},
    {"name": "avg_order_value", "label": "Average order value", "model": "sales",
     "expression": "sum(net_revenue) / nullif(count(DISTINCT order_id), 0)", "format": "currency", "currency": "USD"},
    {"name": "gross_margin_pct", "label": "Gross margin %", "model": "sales",
     "expression": "sum(margin) / nullif(sum(net_revenue), 0)", "format": "percent", "certified": True},
    {"name": "avg_satisfaction", "label": "Average satisfaction", "model": "shop_support_tickets",
     "expression": "avg(satisfaction)", "format": "number"},
    {"name": "resolution_hours_p90", "label": "Resolution hours (p90)", "model": "shop_support_tickets",
     "expression": "quantile_cont(resolution_hours, 0.9)", "format": "number", "higher_is_better": False},
]
for m in METRICS:
    print("metric", m["name"], "→", call("PUT", f"/api/metrics/{m['name']}", m)["name"])
