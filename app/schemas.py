"""Request and response shapes. Request validation is the trust boundary; the domain modules
assume clean input. Response models drive the OpenAPI schema and drop any field not listed."""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


Quantity = Field(strict=True, gt=0, le=1000,
                 description="Whole units, 1–1000. Must be a JSON integer: `1.5` and `\"2\"` are rejected.")


class AddItem(Body):
    product_id: str = Field(description="ID of an existing product, e.g. `p_keyboard`.")
    quantity: int = Quantity


class SetQuantity(Body):
    quantity: int = Quantity


class CheckoutBody(Body):
    coupon_code: str | None = Field(
        None, description="Optional coupon from `POST /admin/coupons`. Unknown or used codes fail the checkout.")
    expected_total_minor: int | None = Field(
        None, strict=True, ge=0,
        description="Optional. The total the client showed the customer. If the real total differs "
                    "(price changed, coupon changed), checkout fails with `409 TOTAL_CHANGED` and nothing is written.")


class ProductPatch(Body):
    price_minor: int | None = Field(None, strict=True, ge=0, description="New unit price in minor units. Omit to keep.")
    inventory: int | None = Field(None, strict=True, ge=0, description="New absolute stock level. Omit to keep.")


Currency = Field(description="ISO 4217 code all amounts on this object are in, e.g. `INR`. "
                             "Amounts are integers in its minor unit: paise for INR, cents for USD, yen for JPY.")


class Product(BaseModel):
    id: str
    name: str
    price_minor: int = Field(description="Current unit price in minor units.")
    currency: str = Currency
    inventory: int = Field(description="Units available right now.")


class CartItem(BaseModel):
    product_id: str
    name: str
    unit_price_minor: int = Field(description="The product's **current** price; not locked in until checkout.")
    quantity: int
    line_total_minor: int = Field(description="`unit_price_minor × quantity`.")
    available_inventory: int = Field(description="Current stock. Carts don't reserve stock.")
    in_stock: bool = Field(description="`quantity <= available_inventory` right now. Checkout re-checks.")


class Cart(BaseModel):
    id: str
    status: Literal["open", "checked_out"]
    order_id: str | None = Field(description="The order this cart became, once checked out.")
    items: list[CartItem]
    subtotal_minor: int = Field(description="Sum of line totals at current prices, before any coupon.")
    currency: str = Currency


class OrderLine(BaseModel):
    product_id: str
    product_name: str = Field(description="Snapshot of the product name at checkout.")
    unit_price_minor: int = Field(description="Snapshot of the price charged at checkout.")
    quantity: int
    line_total_minor: int


class Order(BaseModel):
    id: str
    cart_id: str
    subtotal_minor: int = Field(description="Sum of `lines[].line_total_minor`.")
    coupon_code: str | None
    discount_percent: int = Field(description="Taken from the coupon when it was issued; 0 without a coupon.")
    discount_minor: int = Field(description="`round_half_up(subtotal × percent / 100)`, never above the subtotal.")
    total_minor: int = Field(description="`subtotal_minor − discount_minor`, always ≥ 0.")
    currency: str = Field(description="ISO 4217 code the order was priced in, copied at checkout. "
                                      "It never changes, even if the store's currency does.")
    created_at: datetime
    lines: list[OrderLine]


class Coupon(BaseModel):
    code: str
    percent: int = Field(description="Discount this coupon gives. Fixed when issued; later config changes don't affect it.")
    milestone_order: int = Field(description="The order count that earned this coupon, e.g. 5, 10, 15.")
    created_at: datetime
    redeemed_order_id: str | None = Field(description="The order that used this coupon, if any.")
    status: Literal["available", "redeemed"]


class ProductSales(BaseModel):
    product_id: str
    name: str
    quantity_sold: int


class CouponCounts(BaseModel):
    generated: int
    redeemed: int
    available: int = Field(description="`generated − redeemed`.")


class Revenue(BaseModel):
    currency: str
    gross_revenue_minor: int = Field(description="Sum of order subtotals in this currency (before discounts).")
    discounts_minor: int = Field(description="Sum of order discounts in this currency.")
    net_revenue_minor: int = Field(description="`gross − discounts`, equal to the sum of order totals in this currency.")


class Report(BaseModel):
    orders_placed: int
    quantity_by_product: list[ProductSales]
    revenue: list[Revenue] = Field(
        description="One entry per currency that orders were placed in. Normally a single entry; "
                    "more only if the store's currency was changed. Amounts in different currencies are never added together.")
    coupons: CouponCounts


class ErrorBody(BaseModel):
    code: str = Field(description="Stable, machine-readable error code. Branch on this, not on `message`.")
    message: str = Field(description="Human-readable explanation.")
    details: dict[str, Any] = Field(description="Structured data the client can act on; `{}` when there is none.")


class ErrorResponse(BaseModel):
    error: ErrorBody
