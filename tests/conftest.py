"""Shared fixtures and helpers. Every test starts from freshly seeded tables with n=2, x=10.

Needs the Postgres from docker-compose.yml (`docker compose up -d`); tests use the separate
shop_test database so they never touch dev data.
"""
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import carts, checkout, coupons, db
from app.errors import DomainError
from app.main import app

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://shop:shop@localhost:5433/shop_test")


@pytest.fixture(autouse=True)
def fresh_db(monkeypatch):
    monkeypatch.setattr(db, "DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setattr(coupons, "COUPON_EVERY_N", 2)
    monkeypatch.setattr(coupons, "COUPON_PERCENT", 10)
    db.init_db()
    with db.tx() as conn:
        conn.execute("TRUNCATE order_lines, orders, coupons, cart_items, carts, products")
    db.init_db()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def race(fn, args_list):
    """Run fn(*args) for each args concurrently, released together by a barrier.
    Each call opens its own Postgres connection, so this exercises real database locking.
    Returns a list of results or DomainError instances."""
    barrier = threading.Barrier(len(args_list))

    def run(args):
        barrier.wait()
        try:
            return fn(*args)
        except DomainError as e:
            return e

    with ThreadPoolExecutor(len(args_list)) as pool:
        return list(pool.map(run, args_list))


def cart_with(product_id, qty):
    cart_id = carts.create_cart()["id"]
    carts.add_item(cart_id, product_id, qty)
    return cart_id


def place_orders(n):
    for _ in range(n):
        checkout.checkout(cart_with("p_cable", 1))


def inventory(product_id):
    return next(p["inventory"] for p in carts.list_products() if p["id"] == product_id)
