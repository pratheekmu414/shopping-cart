"""Products and carts. Carts hold only product_id + quantity; prices are always read live."""
import os
import re
import uuid

from app.db import tx
from app.errors import DomainError

CURRENCY = os.environ.get("CURRENCY", "INR")


def validate_config():
    if not re.fullmatch(r"[A-Z]{3}", CURRENCY):
        raise ValueError("CURRENCY must be an ISO 4217 code such as INR, USD or JPY")


def list_products():
    with tx() as conn:
        return [{**p, "currency": CURRENCY} for p in conn.execute("SELECT * FROM products ORDER BY id")]


def get_product(conn, product_id):
    row = conn.execute("SELECT * FROM products WHERE id = %s", (product_id,)).fetchone()
    if not row:
        raise DomainError(404, "PRODUCT_NOT_FOUND", f"Product {product_id} does not exist")
    return row


def update_product(product_id, price_minor=None, inventory=None):
    with tx() as conn:
        row = conn.execute(
            """UPDATE products SET price_minor = COALESCE(%s, price_minor), inventory = COALESCE(%s, inventory)
               WHERE id = %s RETURNING *""", (price_minor, inventory, product_id)).fetchone()
        if not row:
            raise DomainError(404, "PRODUCT_NOT_FOUND", f"Product {product_id} does not exist")
        return {**row, "currency": CURRENCY}


def order_id_for_cart(conn, cart_id):
    row = conn.execute("SELECT id FROM orders WHERE cart_id = %s", (cart_id,)).fetchone()
    return row["id"] if row else None


def require_cart(conn, cart_id, lock=False):
    sql = "SELECT id FROM carts WHERE id = %s" + (" FOR UPDATE" if lock else "")
    if not conn.execute(sql, (cart_id,)).fetchone():
        raise DomainError(404, "CART_NOT_FOUND", f"Cart {cart_id} does not exist")


def require_open_cart(conn, cart_id):
    require_cart(conn, cart_id, lock=True)
    order_id = order_id_for_cart(conn, cart_id)
    if order_id:
        raise DomainError(409, "CART_ALREADY_CHECKED_OUT", "Cart has already been checked out",
                          {"order_id": order_id})


def _cart_view(conn, cart_id):
    rows = conn.execute(
        """SELECT ci.product_id, p.name, p.price_minor, p.inventory, ci.quantity
           FROM cart_items ci JOIN products p ON p.id = ci.product_id
           WHERE ci.cart_id = %s ORDER BY ci.product_id""", (cart_id,)).fetchall()
    items = [{
        "product_id": r["product_id"],
        "name": r["name"],
        "unit_price_minor": r["price_minor"],
        "quantity": r["quantity"],
        "line_total_minor": r["price_minor"] * r["quantity"],
        "available_inventory": r["inventory"],
        "in_stock": r["quantity"] <= r["inventory"],
    } for r in rows]
    order_id = order_id_for_cart(conn, cart_id)
    return {
        "id": cart_id,
        "status": "checked_out" if order_id else "open",
        "order_id": order_id,
        "items": items,
        "subtotal_minor": sum(i["line_total_minor"] for i in items),
        "currency": CURRENCY,
    }


def create_cart():
    cart_id = uuid.uuid4().hex
    with tx() as conn:
        conn.execute("INSERT INTO carts (id) VALUES (%s)", (cart_id,))
        return _cart_view(conn, cart_id)


def get_cart(cart_id):
    with tx() as conn:
        require_cart(conn, cart_id)
        return _cart_view(conn, cart_id)


def _check_stock(product, quantity):
    if quantity > product["inventory"]:
        raise DomainError(409, "INSUFFICIENT_INVENTORY", "Not enough inventory for requested quantity",
                          {"items": [{"product_id": product["id"], "requested": quantity,
                                      "available": product["inventory"]}]})


def add_item(cart_id, product_id, quantity):
    with tx() as conn:
        require_open_cart(conn, cart_id)
        product = get_product(conn, product_id)
        if conn.execute("SELECT 1 FROM cart_items WHERE cart_id = %s AND product_id = %s",
                        (cart_id, product_id)).fetchone():
            raise DomainError(409, "ITEM_ALREADY_IN_CART",
                              "Product already in cart; use PUT to change its quantity")
        _check_stock(product, quantity)
        conn.execute("INSERT INTO cart_items VALUES (%s, %s, %s)", (cart_id, product_id, quantity))
        return _cart_view(conn, cart_id)


def set_item(cart_id, product_id, quantity):
    with tx() as conn:
        require_open_cart(conn, cart_id)
        _check_stock(get_product(conn, product_id), quantity)
        conn.execute("""INSERT INTO cart_items VALUES (%s, %s, %s)
                        ON CONFLICT (cart_id, product_id) DO UPDATE SET quantity = excluded.quantity""",
                     (cart_id, product_id, quantity))
        return _cart_view(conn, cart_id)


def remove_item(cart_id, product_id):
    with tx() as conn:
        require_open_cart(conn, cart_id)
        conn.execute("DELETE FROM cart_items WHERE cart_id = %s AND product_id = %s", (cart_id, product_id))
        return _cart_view(conn, cart_id)
