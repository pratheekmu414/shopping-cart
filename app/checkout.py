"""Checkout and orders. An order is an immutable snapshot of what was bought and at what price."""
import uuid

from app import carts
from app.carts import require_cart
from app.db import tx
from app.errors import DomainError


def compute_discount(subtotal_minor, percent):
    """Percent of the whole subtotal, rounded half-up to the smallest currency unit, never above the subtotal."""
    return min(subtotal_minor, (subtotal_minor * percent + 50) // 100)


def _order_view(conn, order_id):
    order = conn.execute("SELECT * FROM orders WHERE id = %s", (order_id,)).fetchone()
    if not order:
        raise DomainError(404, "ORDER_NOT_FOUND", f"Order {order_id} does not exist")
    lines = conn.execute(
        """SELECT product_id, product_name, unit_price_minor, quantity, line_total_minor
           FROM order_lines WHERE order_id = %s ORDER BY product_id""", (order_id,)).fetchall()
    return {**order, "lines": lines}


def get_order(order_id):
    with tx() as conn:
        return _order_view(conn, order_id)


def checkout(cart_id, coupon_code=None, expected_total_minor=None):
    """Returns (order, created). created=False means this is a replay of an earlier success.

    Lock order is always cart -> products (sorted by id) -> coupon, so two checkouts can
    never deadlock. Every check below runs on locked rows, so it can't go stale before the write.
    """
    with tx() as conn:
        require_cart(conn, cart_id, lock=True)

        existing = conn.execute("SELECT id, coupon_code FROM orders WHERE cart_id = %s", (cart_id,)).fetchone()
        if existing:
            if existing["coupon_code"] == coupon_code:
                return _order_view(conn, existing["id"]), False
            raise DomainError(409, "CART_ALREADY_CHECKED_OUT",
                              "Cart was already checked out with a different coupon",
                              {"order_id": existing["id"]})

        quantities = {r["product_id"]: r["quantity"] for r in conn.execute(
            "SELECT product_id, quantity FROM cart_items WHERE cart_id = %s", (cart_id,))}
        if not quantities:
            raise DomainError(422, "CART_EMPTY", "Cannot check out an empty cart")

        products = conn.execute(
            "SELECT id, name, price_minor, inventory FROM products WHERE id = ANY(%s) ORDER BY id FOR UPDATE",
            (sorted(quantities),)).fetchall()
        short = [{"product_id": p["id"], "requested": quantities[p["id"]], "available": p["inventory"]}
                 for p in products if quantities[p["id"]] > p["inventory"]]
        if short:
            raise DomainError(409, "INSUFFICIENT_INVENTORY", "Not enough inventory for one or more items",
                              {"items": short})

        percent = 0
        if coupon_code is not None:
            coupon = conn.execute("SELECT * FROM coupons WHERE code = %s FOR UPDATE", (coupon_code,)).fetchone()
            if not coupon:
                raise DomainError(422, "COUPON_NOT_FOUND", "Coupon code is not valid")
            if conn.execute("SELECT 1 FROM orders WHERE coupon_code = %s", (coupon_code,)).fetchone():
                raise DomainError(409, "COUPON_ALREADY_REDEEMED", "Coupon has already been redeemed")
            percent = coupon["percent"]

        subtotal = sum(p["price_minor"] * quantities[p["id"]] for p in products)
        discount = compute_discount(subtotal, percent)
        total = subtotal - discount
        if expected_total_minor is not None and expected_total_minor != total:
            raise DomainError(409, "TOTAL_CHANGED", "Order total differs from what the client expected",
                              {"expected_total_minor": expected_total_minor, "subtotal_minor": subtotal,
                               "discount_minor": discount, "total_minor": total})

        order_id = uuid.uuid4().hex
        conn.execute(
            """INSERT INTO orders (id, cart_id, subtotal_minor, coupon_code, discount_percent, discount_minor,
                                   total_minor, currency) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (order_id, cart_id, subtotal, coupon_code, percent, discount, total, carts.CURRENCY))
        for p in products:
            qty = quantities[p["id"]]
            cur = conn.execute("UPDATE products SET inventory = inventory - %s WHERE id = %s AND inventory >= %s",
                               (qty, p["id"], qty))
            if cur.rowcount != 1:
                raise DomainError(409, "INSUFFICIENT_INVENTORY", "Inventory changed during checkout",
                                  {"items": [{"product_id": p["id"]}]})
            conn.execute("INSERT INTO order_lines VALUES (%s, %s, %s, %s, %s, %s)",
                         (order_id, p["id"], p["name"], p["price_minor"], qty, p["price_minor"] * qty))
        return _order_view(conn, order_id), True
