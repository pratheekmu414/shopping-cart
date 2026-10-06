"""Business rules: coupon lifecycle, milestone semantics, money math, snapshots, reconciliation."""
import pytest

from app import carts, checkout, coupons
from app.errors import DomainError
from conftest import cart_with, inventory, place_orders


def test_failed_checkout_does_not_consume_coupon():
    place_orders(2)
    code = coupons.generate_coupon()["code"]
    cart_id = cart_with("p_lamp", 3)
    carts.update_product("p_lamp", inventory=2)

    with pytest.raises(DomainError) as e:
        checkout.checkout(cart_id, code)
    assert e.value.code == "INSUFFICIENT_INVENTORY"
    assert [c["status"] for c in coupons.list_coupons()] == ["available"]
    assert inventory("p_lamp") == 2

    order, _ = checkout.checkout(cart_with("p_lamp", 2), code)
    assert order["coupon_code"] == code


def test_no_coupon_before_milestone():
    place_orders(1)
    with pytest.raises(DomainError) as e:
        coupons.generate_coupon()
    assert e.value.code == "NO_ELIGIBLE_MILESTONE"
    assert e.value.details == {"orders_placed": 1, "next_milestone_order": 2}


def test_changing_n_and_x_only_applies_going_forward(monkeypatch):
    place_orders(2)
    old = coupons.generate_coupon()

    monkeypatch.setattr(coupons, "COUPON_EVERY_N", 3)
    monkeypatch.setattr(coupons, "COUPON_PERCENT", 50)
    place_orders(2)
    with pytest.raises(DomainError) as e:
        coupons.generate_coupon()
    assert e.value.details["next_milestone_order"] == 5

    place_orders(1)
    new = coupons.generate_coupon()
    assert (new["milestone_order"], new["percent"]) == (5, 50)

    order, _ = checkout.checkout(cart_with("p_keyboard", 1), old["code"])
    assert order["discount_percent"] == 10


@pytest.mark.parametrize("subtotal,percent,expected", [
    (1000, 10, 100),
    (999, 10, 100),
    (5, 10, 1),
    (4, 10, 0),
    (0, 10, 0),
    (1234, 100, 1234),
])
def test_discount_rounding(subtotal, percent, expected):
    assert checkout.compute_discount(subtotal, percent) == expected


def test_order_snapshot_survives_price_change_and_report_reconciles():
    place_orders(2)
    code = coupons.generate_coupon()["code"]
    cart_id = carts.create_cart()["id"]
    carts.add_item(cart_id, "p_keyboard", 1)
    carts.add_item(cart_id, "p_cable", 3)
    order, _ = checkout.checkout(cart_id, code)
    carts.update_product("p_keyboard", price_minor=1)

    order = checkout.get_order(order["id"])
    assert order["subtotal_minor"] == 8999 + 3 * 999 == 11996
    assert order["discount_minor"] == 1200
    assert order["total_minor"] == 10796
    assert {l["product_id"]: l["unit_price_minor"] for l in order["lines"]}["p_keyboard"] == 8999

    report = coupons.report()
    assert report == coupons.report()
    assert report["orders_placed"] == 3
    assert report["revenue"] == [{"currency": "INR", "gross_revenue_minor": 2 * 999 + 11996,
                                  "discounts_minor": 1200, "net_revenue_minor": 2 * 999 + 11996 - 1200}]
    sold = {p["product_id"]: p["quantity_sold"] for p in report["quantity_by_product"]}
    assert sold["p_cable"] == 5 and sold["p_keyboard"] == 1


def test_price_change_is_caught_by_expected_total():
    cart_id = cart_with("p_mouse", 2)
    seen = carts.get_cart(cart_id)["subtotal_minor"]
    carts.update_product("p_mouse", price_minor=2999)

    with pytest.raises(DomainError) as e:
        checkout.checkout(cart_id, expected_total_minor=seen)
    assert e.value.code == "TOTAL_CHANGED" and e.value.details["total_minor"] == 5998
    assert inventory("p_mouse") == 100


def test_changing_currency_only_applies_to_new_orders(monkeypatch):
    inr_order, _ = checkout.checkout(cart_with("p_mouse", 1))

    monkeypatch.setattr(carts, "CURRENCY", "USD")
    usd_order, _ = checkout.checkout(cart_with("p_mouse", 2))

    assert checkout.get_order(inr_order["id"])["currency"] == "INR"
    assert usd_order["currency"] == "USD"
    assert carts.get_cart(cart_with("p_cable", 1))["currency"] == "USD"
    assert coupons.report()["revenue"] == [
        {"currency": "INR", "gross_revenue_minor": 2499, "discounts_minor": 0, "net_revenue_minor": 2499},
        {"currency": "USD", "gross_revenue_minor": 4998, "discounts_minor": 0, "net_revenue_minor": 4998},
    ]
