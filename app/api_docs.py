"""OpenAPI text and example payloads for /docs. No behaviour lives here.

Examples are real responses captured from the running service, with IDs kept consistent
across endpoints (the cart below becomes the order below, the coupon is redeemed by it, ...).
"""
from app.schemas import ErrorResponse

DESCRIPTION = """
Backend for a small store: carts, idempotent checkout, and a rewards program that issues a
discount coupon after every *n*-th successfully placed order.

## Conventions
- **Money is an integer in the currency's minor unit** in every field named `*_minor`, with the ISO 4217
  code in a `currency` field next to it: `{"price_minor": 8999, "currency": "INR"}` is ₹89.99. The minor unit is
  paise for INR, cents for USD and yen for JPY (no decimals). There are no floats anywhere.
- **One currency per store**, set with the `CURRENCY` setting. Each order keeps the currency it was
  placed in, so changing the setting later never re-labels past orders.
- **Every error has the same shape.** Branch on `error.code`, not on `message` or the HTTP status alone:
  ```json
  {"error": {"code": "INSUFFICIENT_INVENTORY", "message": "...", "details": {...}}}
  ```
  - `404`: the resource in the path doesn't exist.
  - `409`: the request is valid but conflicts with current state. Retrying won't help until the state changes.
  - `422`: the request itself is wrong.
- **Request bodies reject unknown fields**, so `{"coupon": "..."}` (a typo for `coupon_code`)
  fails loudly instead of silently checking out at full price.

## Retries and concurrency
- **Checkout is idempotent per cart.** Retrying `POST /carts/{cart_id}/checkout` with the same body returns
  the original order (`200`) instead of creating a second one (`201` the first time). A cart becomes at most one order.
- `PUT` and `DELETE` on cart items are safe to retry. `POST /carts/{cart_id}/items` returns
  `409 ITEM_ALREADY_IN_CART` on a retry rather than doubling the quantity.
- Concurrent checkouts **never oversell** and a coupon is **never redeemed twice**. A checkout that fails
  changes nothing: no stock is taken and the coupon stays available.

## Prices
Carts always show **current** prices. The price is fixed at checkout, and the order keeps its own copy, so later
product changes never alter a placed order. Send `expected_total_minor` at checkout to refuse a total
the customer hasn't seen.

## Administration
Operations under `/admin` are administrative. Authentication is out of scope for this exercise;
in production they would require an admin role.
"""

TAGS = [
    {"name": "Products", "description": "The catalogue: current prices and stock."},
    {"name": "Carts", "description": "Build a cart. Carts hold product IDs and quantities only; they don't reserve stock."},
    {"name": "Checkout & orders", "description": "Turn a cart into an immutable order, optionally with a coupon."},
    {"name": "Admin", "description": "Administrative operations: coupons, reporting, and product changes."},
]

CART_ID = "b685d68c87cb40ffbb3c88af5c8cdfad"
ORDER_ID = "bb5df4bff6d1471cb976cb781c95a385"
CODE = "SAVE-7F3A9C21"
CREATED_AT = "2026-10-06T18:26:10.303402Z"


PRODUCTS = [
    {"id": "p_cable", "name": "USB-C Cable", "price_minor": 999, "currency": "INR", "inventory": 500},
    {"id": "p_keyboard", "name": "Mechanical Keyboard", "price_minor": 8999, "currency": "INR", "inventory": 50},
    {"id": "p_lamp", "name": "Limited Edition Desk Lamp", "price_minor": 14999, "currency": "INR", "inventory": 3},
    {"id": "p_monitor", "name": "27in Monitor", "price_minor": 27999, "currency": "INR", "inventory": 20},
    {"id": "p_mouse", "name": "Wireless Mouse", "price_minor": 2499, "currency": "INR", "inventory": 100},
]

EMPTY_CART = {"id": CART_ID, "status": "open", "order_id": None, "items": [], "subtotal_minor": 0, "currency": "INR"}

CART_ITEMS = [
    {"product_id": "p_cable", "name": "USB-C Cable", "unit_price_minor": 999, "quantity": 2,
     "line_total_minor": 1998, "available_inventory": 500, "in_stock": True},
    {"product_id": "p_keyboard", "name": "Mechanical Keyboard", "unit_price_minor": 8999, "quantity": 1,
     "line_total_minor": 8999, "available_inventory": 50, "in_stock": True},
]
CART = {"id": CART_ID, "status": "open", "order_id": None, "items": CART_ITEMS, "subtotal_minor": 10997,
        "currency": "INR"}
CART_OUT_OF_STOCK = {
    "id": CART_ID, "status": "open", "order_id": None, "subtotal_minor": 44997, "currency": "INR",
    "items": [{"product_id": "p_lamp", "name": "Limited Edition Desk Lamp", "unit_price_minor": 14999, "quantity": 3,
               "line_total_minor": 44997, "available_inventory": 2, "in_stock": False}],
}
CART_CHECKED_OUT = {**CART, "status": "checked_out", "order_id": ORDER_ID}

ORDER_LINES = [
    {"product_id": "p_cable", "product_name": "USB-C Cable", "unit_price_minor": 999, "quantity": 2,
     "line_total_minor": 1998},
    {"product_id": "p_keyboard", "product_name": "Mechanical Keyboard", "unit_price_minor": 8999, "quantity": 1,
     "line_total_minor": 8999},
]
ORDER = {"id": ORDER_ID, "cart_id": CART_ID, "subtotal_minor": 10997, "coupon_code": None, "discount_percent": 0,
         "discount_minor": 0, "total_minor": 10997, "currency": "INR", "created_at": CREATED_AT, "lines": ORDER_LINES}
ORDER_WITH_COUPON = {**ORDER, "coupon_code": CODE, "discount_percent": 10, "discount_minor": 1100,
                     "total_minor": 9897}

COUPON_NEW = {"code": CODE, "percent": 10, "milestone_order": 5, "created_at": CREATED_AT,
              "redeemed_order_id": None, "status": "available"}
COUPONS = [
    {**COUPON_NEW, "redeemed_order_id": ORDER_ID, "status": "redeemed"},
    {"code": "SAVE-2B8E40D6", "percent": 10, "milestone_order": 10, "created_at": "2026-10-06T19:02:44.918204Z",
     "redeemed_order_id": None, "status": "available"},
]

REPORT = {
    "orders_placed": 6,
    "quantity_by_product": [
        {"product_id": "p_cable", "name": "USB-C Cable", "quantity_sold": 7},
        {"product_id": "p_keyboard", "name": "Mechanical Keyboard", "quantity_sold": 2},
        {"product_id": "p_lamp", "name": "Limited Edition Desk Lamp", "quantity_sold": 1},
        {"product_id": "p_monitor", "name": "27in Monitor", "quantity_sold": 1},
        {"product_id": "p_mouse", "name": "Wireless Mouse", "quantity_sold": 2},
    ],
    "revenue": [
        {"currency": "INR", "gross_revenue_minor": 72987, "discounts_minor": 1100, "net_revenue_minor": 71887},
    ],
    "coupons": {"generated": 1, "redeemed": 1, "available": 0},
}


def ok(description, **examples):
    """A success response with one or more named examples: ok("...", name=(summary, value))."""
    return {"description": description, "content": {"application/json": {"examples": {
        name: {"summary": summary, "value": value} for name, (summary, value) in examples.items()}}}}


ADD_ITEM_EXAMPLES = {
    "keyboard": {"summary": "Add one keyboard", "value": {"product_id": "p_keyboard", "quantity": 1}},
    "limited": {"summary": "Add the limited-stock lamp", "value": {"product_id": "p_lamp", "quantity": 2}},
}
SET_QUANTITY_EXAMPLES = {
    "two": {"summary": "Set quantity to 2", "value": {"quantity": 2}},
}
CHECKOUT_EXAMPLES = {
    "plain": {"summary": "No coupon", "value": {}},
    "coupon": {"summary": "With a coupon", "value": {"coupon_code": CODE}},
    "guarded": {"summary": "Refuse if the total changed",
                "description": "The client showed the customer ₹98.97 (9897 paise). If prices moved since, checkout is refused.",
                "value": {"coupon_code": CODE, "expected_total_minor": 9897}},
}
PRODUCT_PATCH_EXAMPLES = {
    "price": {"summary": "Change the price", "value": {"price_minor": 9499}},
    "stock": {"summary": "Set stock level", "value": {"inventory": 2}},
    "both": {"summary": "Price and stock together", "value": {"price_minor": 9499, "currency": "INR", "inventory": 40}},
}


def _err(code, message, details=None):
    return {"error": {"code": code, "message": message, "details": details or {}}}


ERRORS = {
    "CART_NOT_FOUND": (404, "Unknown cart ID", _err(
        "CART_NOT_FOUND", "Cart 0d4c6b1e9f2a4e7c8b3d5a6f1e2c9b70 does not exist")),
    "PRODUCT_NOT_FOUND": (404, "Unknown product ID", _err(
        "PRODUCT_NOT_FOUND", "Product p_speaker does not exist")),
    "ORDER_NOT_FOUND": (404, "Unknown order ID", _err(
        "ORDER_NOT_FOUND", "Order 7c1e9a3f5b2d4e6a8c0b1d3f5e7a9c2b does not exist")),
    "CART_ALREADY_CHECKED_OUT": (409, "Cart is frozen after checkout", _err(
        "CART_ALREADY_CHECKED_OUT", "Cart has already been checked out", {"order_id": ORDER_ID})),
    "CART_CHECKED_OUT_DIFFERENT_COUPON": (409, "Cart already placed with a different coupon", _err(
        "CART_ALREADY_CHECKED_OUT", "Cart was already checked out with a different coupon", {"order_id": ORDER_ID})),
    "ITEM_ALREADY_IN_CART": (409, "Product already in the cart (use PUT)", _err(
        "ITEM_ALREADY_IN_CART", "Product already in cart; use PUT to change its quantity")),
    "INSUFFICIENT_INVENTORY_ADD": (409, "More than is in stock right now", _err(
        "INSUFFICIENT_INVENTORY", "Not enough inventory for requested quantity",
        {"items": [{"product_id": "p_lamp", "requested": 4, "available": 3}]})),
    "INSUFFICIENT_INVENTORY_CHECKOUT": (409, "Stock ran out before checkout", _err(
        "INSUFFICIENT_INVENTORY", "Not enough inventory for one or more items",
        {"items": [{"product_id": "p_lamp", "requested": 3, "available": 2}]})),
    "COUPON_ALREADY_REDEEMED": (409, "Coupon already used by another order", _err(
        "COUPON_ALREADY_REDEEMED", "Coupon has already been redeemed")),
    "TOTAL_CHANGED": (409, "Total differs from expected_total_minor", _err(
        "TOTAL_CHANGED", "Order total differs from what the client expected",
        {"expected_total_minor": 10997, "subtotal_minor": 11497, "discount_minor": 0, "total_minor": 11497})),
    "NO_ELIGIBLE_MILESTONE": (409, "No unrewarded milestone reached yet", _err(
        "NO_ELIGIBLE_MILESTONE", "No unrewarded order milestone has been reached",
        {"orders_placed": 7, "next_milestone_order": 10})),
    "CART_EMPTY": (422, "Nothing to check out", _err("CART_EMPTY", "Cannot check out an empty cart")),
    "COUPON_NOT_FOUND": (422, "Unknown coupon code", _err("COUPON_NOT_FOUND", "Coupon code is not valid")),
    "VALIDATION_ERROR": (422, "Quantity out of range", _err(
        "VALIDATION_ERROR", "Request body is invalid",
        {"errors": [{"type": "greater_than", "loc": ["body", "quantity"], "msg": "Input should be greater than 0",
                     "input": 0, "ctx": {"gt": 0}}]})),
    "VALIDATION_ERROR_EXTRA": (422, "Unknown field in the body", _err(
        "VALIDATION_ERROR", "Request body is invalid",
        {"errors": [{"type": "extra_forbidden", "loc": ["body", "coupon"], "msg": "Extra inputs are not permitted",
                     "input": CODE}]})),
}

_STATUS_TEXT = {404: "Not found", 409: "Conflict with current state", 422: "Invalid request"}


def errors(*names):
    """OpenAPI `responses` for the given ERRORS entries, grouped by HTTP status."""
    out = {}
    for name in names:
        status, summary, value = ERRORS[name]
        entry = out.setdefault(status, {"model": ErrorResponse, "description": _STATUS_TEXT[status],
                                        "content": {"application/json": {"examples": {}}}})
        entry["content"]["application/json"]["examples"][name] = {"summary": summary, "value": value}
    return out
