"""HTTP contract: status codes, error codes, validation, idempotent replay."""
import pytest

from conftest import inventory


def test_checkout_retry_over_http_replays_original_order(client):
    cart_id = client.post("/carts").json()["id"]
    client.post(f"/carts/{cart_id}/items", json={"product_id": "p_mouse", "quantity": 3})

    first = client.post(f"/carts/{cart_id}/checkout")
    retry = client.post(f"/carts/{cart_id}/checkout")
    different = client.post(f"/carts/{cart_id}/checkout", json={"coupon_code": "SAVE-OTHER"})
    mutate = client.put(f"/carts/{cart_id}/items/p_mouse", json={"quantity": 1})

    assert (first.status_code, retry.status_code) == (201, 200)
    assert retry.json() == first.json()
    assert different.status_code == 409 and different.json()["error"]["code"] == "CART_ALREADY_CHECKED_OUT"
    assert mutate.status_code == 409 and mutate.json()["error"]["code"] == "CART_ALREADY_CHECKED_OUT"
    assert inventory("p_mouse") == 97


@pytest.mark.parametrize("body,status,code", [
    ({"product_id": "p_mouse", "quantity": 0}, 422, "VALIDATION_ERROR"),
    ({"product_id": "p_mouse", "quantity": 1.5}, 422, "VALIDATION_ERROR"),
    ({"product_id": "p_mouse", "quantity": "2"}, 422, "VALIDATION_ERROR"),
    ({"product_id": "p_mouse", "quantity": 1, "price": 0}, 422, "VALIDATION_ERROR"),
    ({"product_id": "nope", "quantity": 1}, 404, "PRODUCT_NOT_FOUND"),
    ({"product_id": "p_lamp", "quantity": 4}, 409, "INSUFFICIENT_INVENTORY"),
])
def test_invalid_items_are_rejected(client, body, status, code):
    cart_id = client.post("/carts").json()["id"]
    r = client.post(f"/carts/{cart_id}/items", json=body)
    assert (r.status_code, r.json()["error"]["code"]) == (status, code)
    assert client.get(f"/carts/{cart_id}").json()["items"] == []


def test_checkout_error_codes(client):
    cart_id = client.post("/carts").json()["id"]
    assert client.post(f"/carts/{cart_id}/checkout").json()["error"]["code"] == "CART_EMPTY"
    client.post(f"/carts/{cart_id}/items", json={"product_id": "p_cable", "quantity": 1})
    r = client.post(f"/carts/{cart_id}/checkout", json={"coupon_code": "SAVE-FAKE"})
    assert (r.status_code, r.json()["error"]["code"]) == (422, "COUPON_NOT_FOUND")
    assert client.post("/carts/missing/checkout").status_code == 404
