"""HTTP layer: routes, status codes, error envelope. All business rules live in the domain modules.
OpenAPI text and examples live in api_docs.py; see /docs."""
from contextlib import asynccontextmanager
from typing import Annotated

import psycopg
from fastapi import Body, FastAPI, Path, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app import api_docs as docs
from app import carts, checkout, coupons, db
from app.errors import DomainError
from app.schemas import (AddItem, Cart, CheckoutBody, Coupon, Order, Product, ProductPatch, Report,
                         SetQuantity)


@asynccontextmanager
async def lifespan(_app):
    carts.validate_config()
    coupons.validate_config()
    db.init_db()
    yield


app = FastAPI(title="Checkout & Rewards", version="1.0.0", description=docs.DESCRIPTION,
              openapi_tags=docs.TAGS, lifespan=lifespan)


def _error(status, code, message, details=None):
    return JSONResponse({"error": {"code": code, "message": message, "details": details or {}}}, status)


@app.exception_handler(DomainError)
def domain_error(_req: Request, e: DomainError):
    return _error(e.status, e.code, e.message, e.details)


@app.exception_handler(RequestValidationError)
def validation_error(_req: Request, e: RequestValidationError):
    return _error(422, "VALIDATION_ERROR", "Request body is invalid", {"errors": jsonable_encoder(e.errors())})


@app.exception_handler(psycopg.IntegrityError)
def integrity_error(_req: Request, e: psycopg.IntegrityError):
    return _error(409, "CONFLICT", "Request conflicts with current state", {"reason": str(e)})


CartId = Annotated[str, Path(description="Cart ID returned by `POST /carts`.", examples=[docs.CART_ID])]
ProductId = Annotated[str, Path(description="Product ID, e.g. `p_keyboard`.", examples=["p_keyboard"])]


@app.get("/products", tags=["Products"], summary="List products", response_model=list[Product],
         responses={200: docs.ok("All products with current price and stock.", seed=("Seed catalogue", docs.PRODUCTS))})
def list_products():
    return carts.list_products()


@app.post("/carts", status_code=201, tags=["Carts"], summary="Create a cart", response_model=Cart,
          responses={201: docs.ok("The new, empty cart.", empty=("Empty cart", docs.EMPTY_CART))})
def create_cart():
    """Creates an empty cart. Not idempotent, but a duplicate just leaves an unused empty cart behind."""
    return carts.create_cart()


@app.get("/carts/{cart_id}", tags=["Carts"], summary="View a cart", response_model=Cart,
         responses={200: docs.ok("The cart, priced at **current** product prices.",
                                 open=("Open cart", docs.CART),
                                 out_of_stock=("Stock dropped after the item was added", docs.CART_OUT_OF_STOCK),
                                 checked_out=("Cart after checkout", docs.CART_CHECKED_OUT)),
                    **docs.errors("CART_NOT_FOUND")})
def get_cart(cart_id: CartId):
    """Lines show the current unit price, current stock and an `in_stock` flag, so a UI can warn before
    checkout. Prices aren't locked in until checkout."""
    return carts.get_cart(cart_id)


@app.post("/carts/{cart_id}/items", status_code=201, tags=["Carts"], summary="Add an item", response_model=Cart,
          responses={201: docs.ok("The updated cart.", added=("Item added", docs.CART)),
                     **docs.errors("CART_NOT_FOUND", "PRODUCT_NOT_FOUND", "ITEM_ALREADY_IN_CART",
                                   "INSUFFICIENT_INVENTORY_ADD", "CART_ALREADY_CHECKED_OUT",
                                   "VALIDATION_ERROR", "VALIDATION_ERROR_EXTRA")})
def add_item(cart_id: CartId, body: Annotated[AddItem, Body(openapi_examples=docs.ADD_ITEM_EXAMPLES)]):
    """Adds a product that isn't in the cart yet. If it's already there, this returns `409 ITEM_ALREADY_IN_CART`
    instead of adding more, so a retried request can't double the quantity. Use `PUT` to change a quantity.

    The stock check here is advisory: carts don't reserve stock, and checkout re-checks it."""
    return carts.add_item(cart_id, body.product_id, body.quantity)


@app.put("/carts/{cart_id}/items/{product_id}", tags=["Carts"], summary="Set an item's quantity",
         response_model=Cart,
         responses={200: docs.ok("The updated cart.", updated=("Quantity set", docs.CART)),
                    **docs.errors("CART_NOT_FOUND", "PRODUCT_NOT_FOUND", "INSUFFICIENT_INVENTORY_ADD",
                                  "CART_ALREADY_CHECKED_OUT", "VALIDATION_ERROR")})
def set_item(cart_id: CartId, product_id: ProductId,
             body: Annotated[SetQuantity, Body(openapi_examples=docs.SET_QUANTITY_EXAMPLES)]):
    """Sets the quantity, adding the item if it isn't in the cart yet. Safe to retry."""
    return carts.set_item(cart_id, product_id, body.quantity)


@app.delete("/carts/{cart_id}/items/{product_id}", tags=["Carts"], summary="Remove an item", response_model=Cart,
            responses={200: docs.ok("The updated cart.", removed=("Item removed", docs.EMPTY_CART)),
                       **docs.errors("CART_NOT_FOUND", "CART_ALREADY_CHECKED_OUT")})
def remove_item(cart_id: CartId, product_id: ProductId):
    """Removes the item. Removing an item that isn't in the cart also returns `200`, so retries are safe."""
    return carts.remove_item(cart_id, product_id)


@app.post("/carts/{cart_id}/checkout", status_code=201, tags=["Checkout & orders"], summary="Check out a cart",
          response_model=Order,
          responses={201: docs.ok("Order placed.", plain=("Without a coupon", docs.ORDER),
                                  coupon=("With a 10% coupon", docs.ORDER_WITH_COUPON)),
                     200: docs.ok("Retry of a checkout that already succeeded: the **original** order is returned "
                                  "and nothing is charged again.", replay=("Replayed order", docs.ORDER_WITH_COUPON)),
                     **docs.errors("CART_NOT_FOUND", "CART_CHECKED_OUT_DIFFERENT_COUPON",
                                   "INSUFFICIENT_INVENTORY_CHECKOUT", "COUPON_ALREADY_REDEEMED", "TOTAL_CHANGED",
                                   "CART_EMPTY", "COUPON_NOT_FOUND", "VALIDATION_ERROR_EXTRA")})
def checkout_cart(cart_id: CartId, response: Response,
                  body: Annotated[CheckoutBody | None, Body(openapi_examples=docs.CHECKOUT_EXAMPLES)] = None):
    """Validates the cart and places the order in a single transaction. Either all of it happens
    (order created, stock taken, coupon redeemed) or none of it does.

    - **Idempotent per cart:** retrying with the same `coupon_code` returns the original order with `200`.
      A retry with a *different* coupon is a new request and gets `409 CART_ALREADY_CHECKED_OUT`.
    - **All or nothing:** if any line is short on stock, the whole checkout fails and nothing changes.
    - **Concurrency:** concurrent checkouts never oversell, and a coupon is redeemed by exactly one of them.
    - **Prices** are taken at this moment and copied into the order."""
    body = body or CheckoutBody()
    order, created = checkout.checkout(cart_id, body.coupon_code, body.expected_total_minor)
    response.status_code = 201 if created else 200
    return order


@app.get("/orders/{order_id}", tags=["Checkout & orders"], summary="Get an order", response_model=Order,
         responses={200: docs.ok("The order exactly as placed.", coupon=("Order with a coupon", docs.ORDER_WITH_COUPON)),
                    **docs.errors("ORDER_NOT_FOUND")})
def get_order(order_id: Annotated[str, Path(description="Order ID.", examples=[docs.ORDER_ID])]):
    """Orders never change after they're placed. Names and prices are copies from checkout time,
    so they still explain the total after the catalogue changes."""
    return checkout.get_order(order_id)


@app.post("/admin/coupons", status_code=201, tags=["Admin"], summary="Generate a coupon", response_model=Coupon,
          responses={201: docs.ok("Coupon issued for the oldest unrewarded milestone.",
                                  issued=("Coupon for order milestone 5", docs.COUPON_NEW)),
                     **docs.errors("NO_ELIGIBLE_MILESTONE")})
def generate_coupon():
    """Issues **one** coupon for the oldest milestone that has been reached but not rewarded yet.
    With n = 5, milestones fall at orders 5, 10, 15, and so on; every placed order counts, including discounted ones.
    If several milestones are owed, call again for each. Concurrent calls can never issue two coupons for the same milestone.

    The coupon gets the current x%, which stays fixed even if the configuration changes later. After a change to n,
    the next milestone is `last rewarded + n`."""
    return coupons.generate_coupon()


@app.get("/admin/coupons", tags=["Admin"], summary="List coupons", response_model=list[Coupon],
         responses={200: docs.ok("All coupons, oldest milestone first.", coupons=("One redeemed, one available", docs.COUPONS))})
def list_coupons():
    return coupons.list_coupons()


@app.get("/admin/report", tags=["Admin"], summary="Sales and coupon report", response_model=Report,
         responses={200: docs.ok("Totals derived from placed orders and coupons.", report=("After 6 orders", docs.REPORT))})
def report():
    """Read-only, so repeated calls never change state. All figures come from one database snapshot, so they always agree:
    `net = gross − discounts = Σ order totals` and `coupons.generated = available + redeemed`."""
    return coupons.report()


@app.patch("/admin/products/{product_id}", tags=["Admin"], summary="Change a product's price or stock",
           response_model=Product,
           responses={200: docs.ok("The updated product.", price=("Keyboard repriced", {**docs.PRODUCTS[1], "price_minor": 9499})),
                      **docs.errors("PRODUCT_NOT_FOUND", "VALIDATION_ERROR_EXTRA")})
def update_product(product_id: ProductId,
                   body: Annotated[ProductPatch, Body(openapi_examples=docs.PRODUCT_PATCH_EXAMPLES)]):
    """Use this to see what happens when the catalogue changes under an open cart. Open carts
    show the new price immediately. Placed orders keep the price they were charged."""
    return carts.update_product(product_id, body.price_minor, body.inventory)


def openapi():
    """FastAPI adds its own 422 schema to every route; ours use the error envelope above instead."""
    if not app.openapi_schema:
        schema = get_openapi(title=app.title, version=app.version, description=app.description,
                             routes=app.routes, tags=app.openapi_tags)
        for path in schema["paths"].values():
            for op in path.values():
                default_422 = op["responses"].get("422", {})
                if "HTTPValidationError" in str(default_422):
                    del op["responses"]["422"]
        for name in ("HTTPValidationError", "ValidationError"):
            schema["components"]["schemas"].pop(name, None)
        app.openapi_schema = schema
    return app.openapi_schema


app.openapi = openapi
