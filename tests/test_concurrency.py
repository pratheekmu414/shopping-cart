"""Competing and repeated operations: oversell, duplicate orders, double redemption, double issue."""
from app import checkout, coupons
from app.errors import DomainError
from conftest import cart_with, inventory, place_orders, race


def test_concurrent_checkouts_never_oversell():
    carts = [cart_with("p_lamp", 1) for _ in range(12)]

    results = race(checkout.checkout, [(c,) for c in carts])

    won = [r for r in results if isinstance(r, tuple)]
    lost = [r for r in results if isinstance(r, DomainError)]
    assert len(won) == 3
    assert {e.code for e in lost} == {"INSUFFICIENT_INVENTORY"}
    assert inventory("p_lamp") == 0
    assert coupons.report()["orders_placed"] == 3


def test_concurrent_retries_of_one_checkout_create_exactly_one_order():
    cart_id = cart_with("p_monitor", 2)

    results = race(checkout.checkout, [(cart_id,)] * 8)

    assert sum(created for _, created in results) == 1
    assert len({order["id"] for order, _ in results}) == 1
    assert inventory("p_monitor") == 18


def test_coupon_redeemed_by_exactly_one_of_many_concurrent_checkouts():
    place_orders(2)
    code = coupons.generate_coupon()["code"]
    products = ["p_cable", "p_keyboard", "p_lamp", "p_monitor", "p_mouse"]
    carts = [cart_with(p, 1) for p in products]

    results = race(checkout.checkout, [(c, code) for c in carts])

    won = [r for r in results if isinstance(r, tuple)]
    lost = [r for r in results if isinstance(r, DomainError)]
    assert len(won) == 1 and won[0][0]["discount_percent"] == 10
    assert {e.code for e in lost} == {"COUPON_ALREADY_REDEEMED"}
    report = coupons.report()
    assert report["orders_placed"] == 3
    assert sum(p["quantity_sold"] for p in report["quantity_by_product"]) == 3
    assert report["coupons"] == {"generated": 1, "redeemed": 1, "available": 0}


def test_coupon_milestones_issue_at_most_one_coupon_each():
    place_orders(5)
    results = race(coupons.generate_coupon, [()] * 6)

    issued = sorted(r["milestone_order"] for r in results if isinstance(r, dict))
    assert issued == [2, 4]
    assert all(r.code == "NO_ELIGIBLE_MILESTONE" for r in results if isinstance(r, DomainError))
