"""Schema, seed data and the transaction helper. Every operation runs in exactly one transaction.

Transactions run at READ COMMITTED. Each operation takes row locks (SELECT ... FOR UPDATE) on
exactly the rows whose read-check-write must not interleave, always in the same order:
cart -> products (sorted by id) -> coupon. Schema constraints (CHECK / UNIQUE) are a second
line of defence if the locking ever regresses.
"""
import os
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://shop:shop@localhost:5433/shop")

INIT_LOCK = 1
COUPON_LOCK = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  price_minor INTEGER NOT NULL CHECK (price_minor >= 0),
  inventory   INTEGER NOT NULL CHECK (inventory >= 0)
);
CREATE TABLE IF NOT EXISTS carts (
  id         TEXT PRIMARY KEY,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cart_items (
  cart_id    TEXT NOT NULL REFERENCES carts(id),
  product_id TEXT NOT NULL REFERENCES products(id),
  quantity   INTEGER NOT NULL CHECK (quantity > 0),
  PRIMARY KEY (cart_id, product_id)
);
CREATE TABLE IF NOT EXISTS coupons (
  code            TEXT PRIMARY KEY,
  milestone_order INTEGER NOT NULL UNIQUE,  -- the order count that earned it, e.g. 5, 10, 15
  percent         INTEGER NOT NULL CHECK (percent BETWEEN 1 AND 100),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- A cart is checked out iff an order references it; a coupon is redeemed iff an order
-- references it. UNIQUE makes "at most once" a database fact, not just app logic.
CREATE TABLE IF NOT EXISTS orders (
  id               TEXT PRIMARY KEY,
  cart_id          TEXT NOT NULL UNIQUE REFERENCES carts(id),
  subtotal_minor   INTEGER NOT NULL CHECK (subtotal_minor >= 0),
  coupon_code      TEXT UNIQUE REFERENCES coupons(code),
  discount_percent INTEGER NOT NULL,
  discount_minor   INTEGER NOT NULL CHECK (discount_minor BETWEEN 0 AND subtotal_minor),
  total_minor      INTEGER NOT NULL CHECK (total_minor = subtotal_minor - discount_minor),
  currency         CHAR(3) NOT NULL,  -- ISO 4217 code the order was priced in, copied at checkout
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS order_lines (
  order_id         TEXT NOT NULL REFERENCES orders(id),
  product_id       TEXT NOT NULL REFERENCES products(id),
  product_name     TEXT NOT NULL,
  unit_price_minor INTEGER NOT NULL,
  quantity         INTEGER NOT NULL CHECK (quantity > 0),
  line_total_minor INTEGER NOT NULL CHECK (line_total_minor = unit_price_minor * quantity),
  PRIMARY KEY (order_id, product_id)
);
"""

SEED = [
    ("p_keyboard", "Mechanical Keyboard", 8999, 50),
    ("p_mouse", "Wireless Mouse", 2499, 100),
    ("p_monitor", "27in Monitor", 27999, 20),
    ("p_cable", "USB-C Cable", 999, 500),
    ("p_lamp", "Limited Edition Desk Lamp", 14999, 3),
]


@contextmanager
def tx(snapshot=False):
    """Commit on success, roll back on any exception.
    snapshot=True: read-only REPEATABLE READ, so every query sees the same point in time."""
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        if snapshot:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        yield conn


def init_db():
    with tx() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (INIT_LOCK,))
        conn.execute(SCHEMA)
        conn.cursor().executemany(
            "INSERT INTO products VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO NOTHING", SEED)
