"""Coupon milestones and the admin report."""
import os
import secrets

from app.db import COUPON_LOCK, tx
from app.errors import DomainError

COUPON_EVERY_N = int(os.environ.get("COUPON_EVERY_N", "5"))
COUPON_PERCENT = int(os.environ.get("COUPON_PERCENT", "10"))


def validate_config():
    if not (COUPON_EVERY_N >= 1 and 1 <= COUPON_PERCENT <= 100):
        raise ValueError("COUPON_EVERY_N must be >= 1 and COUPON_PERCENT in 1..100")


def generate_coupon():
    """Issue one coupon for the oldest milestone that has been reached but not yet rewarded."""
    with tx() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (COUPON_LOCK,))
        placed = conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
        last = conn.execute("SELECT COALESCE(MAX(milestone_order), 0) AS m FROM coupons").fetchone()["m"]
        milestone = last + COUPON_EVERY_N
        if placed < milestone:
            raise DomainError(409, "NO_ELIGIBLE_MILESTONE", "No unrewarded order milestone has been reached",
                              {"orders_placed": placed, "next_milestone_order": milestone})
        code = "SAVE-" + secrets.token_hex(4).upper()
        coupon = conn.execute(
            """INSERT INTO coupons (code, milestone_order, percent) VALUES (%s, %s, %s)
               RETURNING code, percent, milestone_order, created_at""", (code, milestone, COUPON_PERCENT)).fetchone()
        return {**coupon, "redeemed_order_id": None, "status": "available"}


def list_coupons():
    with tx() as conn:
        rows = conn.execute(
            """SELECT c.code, c.percent, c.milestone_order, c.created_at, o.id AS redeemed_order_id
               FROM coupons c LEFT JOIN orders o ON o.coupon_code = c.code
               ORDER BY c.milestone_order""").fetchall()
        return [{**r, "status": "redeemed" if r["redeemed_order_id"] else "available"} for r in rows]


def report():
    with tx(snapshot=True) as conn:
        orders = conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
        revenue = conn.execute(
            """SELECT currency, SUM(subtotal_minor) AS gross_revenue_minor, SUM(discount_minor) AS discounts_minor,
                      SUM(total_minor) AS net_revenue_minor
               FROM orders GROUP BY currency ORDER BY currency""").fetchall()
        by_product = conn.execute(
            """SELECT p.id AS product_id, p.name, COALESCE(SUM(ol.quantity), 0) AS quantity_sold
               FROM products p LEFT JOIN order_lines ol ON ol.product_id = p.id
               GROUP BY p.id ORDER BY p.id""").fetchall()
        generated = conn.execute("SELECT COUNT(*) AS n FROM coupons").fetchone()["n"]
        redeemed = conn.execute("SELECT COUNT(*) AS n FROM orders WHERE coupon_code IS NOT NULL").fetchone()["n"]
        return {
            "orders_placed": orders,
            "quantity_by_product": by_product,
            "revenue": revenue,
            "coupons": {"generated": generated, "redeemed": redeemed, "available": generated - redeemed},
        }
