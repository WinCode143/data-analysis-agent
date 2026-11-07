"""Generates demo data: sales (CSV), products (Excel) and customers/support tickets (SQL for Postgres).

It includes real patterns the agent should discover: Christmas seasonality, growth of the online
channel and a sales drop in the North region in March 2026 (an Electronics stockout).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

rng = np.random.default_rng(42)
OUT = Path(__file__).resolve().parents[1] / "demo"
OUT.mkdir(exist_ok=True)

# ---------------------------------------------------------------- products (Excel)
categories = {
    "Electronics": ["Pro Headphones", "Tablet 10", "Smartwatch", "BT Speaker", "Fast Charger", "Laptop 14"],
    "Home": ["Coffee Maker", "LED Lamp", "Pan Set", "Vacuum Cleaner", "Air Fryer"],
    "Sports": ["Yoga Mat", "5kg Dumbbells", "Running Shoes", "Exercise Bike"],
    "Fashion": ["Jacket", "Jeans", "Basic T-shirt", "Backpack", "Sunglasses"],
}
price_ranges = {"Electronics": (25, 900), "Home": (20, 250), "Sports": (15, 400), "Fashion": (12, 120)}
rows = []
pid = 1
for cat, names in categories.items():
    lo, hi = price_ranges[cat]
    for n in names:
        price = float(np.round(rng.uniform(lo, hi), 2))
        rows.append({"product_id": pid, "name": n, "category": cat, "list_price": price,
                     "unit_cost": float(np.round(price * rng.uniform(0.45, 0.7), 2))})
        pid += 1
products = pd.DataFrame(rows)
products.to_excel(OUT / "products.xlsx", index=False, sheet_name="catalog")

# ---------------------------------------------------------------- customers (Postgres)
n_customers = 1500
segments = rng.choice(["Consumer", "Small business", "Enterprise"], n_customers, p=[0.7, 0.22, 0.08])
countries = rng.choice(["United States", "Mexico", "Canada", "United Kingdom", "Australia"], n_customers,
                       p=[0.45, 0.25, 0.12, 0.1, 0.08])
signup = pd.Timestamp("2023-01-01") + pd.to_timedelta(rng.integers(0, 1000, n_customers), unit="D")
customers = pd.DataFrame({
    "customer_id": np.arange(1, n_customers + 1),
    "name": [f"Customer {i:04d}" for i in range(1, n_customers + 1)],
    "segment": segments, "country": countries, "signup_date": signup.date,
})

# ---------------------------------------------------------------- sales (CSV)
days = pd.date_range("2024-10-01", "2026-08-31", freq="D")
regions = ["North", "South", "Central", "East", "West"]
sales = []
order = 100000
for d in days:
    seasonal = 1 + 0.9 * np.exp(-((d.dayofyear - 350) % 365) ** 2 / 300) + 0.25 * (d.month in (7, 8))
    trend = 1 + (d - days[0]).days / 900
    n = rng.poisson(28 * seasonal * trend * (1.15 if d.dayofweek >= 5 else 1))
    for _ in range(n):
        order += 1
        prod = products.sample(1, random_state=int(rng.integers(0, 1e9))).iloc[0]
        region = rng.choice(regions, p=[0.24, 0.2, 0.26, 0.17, 0.13])
        # Stockout: Electronics almost disappears in the North in March 2026.
        if region == "North" and prod.category == "Electronics" and d.year == 2026 and d.month == 3 and rng.random() < 0.85:
            continue
        p_online = min(0.25 + (d - days[0]).days / 1400, 0.65)
        channel = rng.choice(["Online", "Store", "Marketplace"], p=[p_online, (1 - p_online) * 0.7, (1 - p_online) * 0.3])
        quantity = int(rng.choice([1, 1, 1, 2, 2, 3, 4]))
        discount = float(rng.choice([0, 0, 0, 0.05, 0.1, 0.15, 0.2]))
        price = round(prod.list_price * rng.uniform(0.97, 1.03), 2)
        sales.append((order, d.date(), int(rng.integers(1, n_customers + 1)), int(prod.product_id),
                      quantity, price, discount, channel, region))
sales = pd.DataFrame(sales, columns=["order_id", "order_date", "customer_id", "product_id", "quantity",
                                     "unit_price", "discount", "channel", "region"])
sales.to_csv(OUT / "sales.csv", index=False)

# ---------------------------------------------------------------- support (Postgres)
n_t = 4000
tickets = pd.DataFrame({
    "ticket_id": np.arange(1, n_t + 1),
    "customer_id": rng.integers(1, n_customers + 1, n_t),
    "opened_on": (pd.Timestamp("2025-01-01") + pd.to_timedelta(rng.integers(0, 600, n_t), unit="D")).date,
    "reason": rng.choice(["Late delivery", "Defective product", "Return", "Billing", "Other"], n_t,
                         p=[0.35, 0.2, 0.25, 0.12, 0.08]),
    "satisfaction": rng.choice([1, 2, 3, 4, 5], n_t, p=[0.08, 0.12, 0.2, 0.32, 0.28]),
    "resolution_hours": np.round(rng.gamma(2, 12, n_t), 1),
})


def sql_values(df: pd.DataFrame) -> str:
    def lit(v):
        if isinstance(v, (int, np.integer, float, np.floating)):
            return str(v)
        return "'" + str(v).replace("'", "''") + "'"
    return ",\n".join("(" + ", ".join(lit(v) for v in r) + ")" for r in df.itertuples(index=False))


(OUT / "postgres").mkdir(exist_ok=True)
(OUT / "postgres" / "init.sql").write_text(f"""
CREATE TABLE customers (customer_id INT PRIMARY KEY, name TEXT, segment TEXT, country TEXT, signup_date DATE);
INSERT INTO customers VALUES
{sql_values(customers)};

CREATE TABLE support_tickets (ticket_id INT PRIMARY KEY, customer_id INT REFERENCES customers, opened_on DATE,
  reason TEXT, satisfaction INT, resolution_hours NUMERIC(6,1));
INSERT INTO support_tickets VALUES
{sql_values(tickets)};
""")

print(f"sales: {len(sales)} rows · products: {len(products)} · customers: {len(customers)} · tickets: {len(tickets)}")
print(f"Files in {OUT}")

# ---------------------------------------------------------------- supplier purchases ("dirty" CSV on purpose)
# Duplicates, spelling variants, amounts stored as text with a decimal comma (as exported by a European
# ERP), dd/mm/yyyy dates and negatives: it exercises the quality check and the assisted cleanup.
supplier_variants = {"Acme Inc.": ["Acme Inc.", "ACME INC.", " acme inc. ", "Acme Inc"],
                     "Northern Supply": ["Northern Supply", "northern supply", "Northern  Supply"],
                     "TechImport": ["TechImport", "Techimport", "TECHIMPORT "],
                     "Home & More": ["Home & More", "Home & More ", "HOME & MORE"]}
cities = {"Chicago": ["Chicago", "chicago", "CHICAGO "], "Boston": ["Boston", "boston"], "Denver": ["Denver", "Denver "]}
rows = []
for i in range(1, 1201):
    canon = rng.choice(list(supplier_variants))
    city = rng.choice(list(cities))
    date = pd.Timestamp("2025-01-01") + pd.Timedelta(days=int(rng.integers(0, 600)))
    amount = float(np.round(rng.gamma(2, 900), 2))
    units = int(rng.integers(1, 60)) * (-1 if rng.random() < 0.02 else 1)
    rows.append({"purchase_id": i, "date": date.strftime("%d/%m/%Y"), "supplier": rng.choice(supplier_variants[canon]),
                 "city": rng.choice(cities[city]), "amount": f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
                 "units": units, "status": rng.choice(["Received", "Pending", "Cancelled"], p=[0.8, 0.15, 0.05])})
purchases = pd.DataFrame(rows)
purchases = pd.concat([purchases, purchases.sample(25, random_state=3)])  # 25 exact duplicate rows
purchases.to_csv(OUT / "supplier_purchases.csv", index=False)
print(f"supplier_purchases: {len(purchases)} rows (with deliberate errors)")
