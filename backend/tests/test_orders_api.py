from uuid import UUID, uuid4
from decimal import Decimal
import pytest
from sqlalchemy import select, func, event
from app.models import Order, OrderItem

def test_order_totals_snapshots_and_statuses(client, order_payload, variant, db_session):
    assert client.patch(f"/api/variants/{variant['id']}", json={"price": "20.10"}).status_code == 200
    response = client.post("/api/orders", json=order_payload)
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["order_number"].startswith("ORD-")
    assert Decimal(order["subtotal"]) == Decimal("60.30")
    assert Decimal(order["total"]) == Decimal("65.55")
    item = order["items"][0]
    assert item["order_id"] == order["id"]
    assert item["product_variant_id"] == variant["id"]
    assert item["product_name_snapshot"] == "Coffee"
    assert item["variant_name_snapshot"] == "250g"
    assert item["sku_snapshot"] == variant["sku"]
    assert Decimal(item["unit_price"]) == Decimal("20.10")
    client.patch(f"/api/variants/{variant['id']}", json={"price": "99.00", "name": "Changed"})
    db_session.expire_all()
    saved = db_session.scalar(select(OrderItem).where(OrderItem.id == UUID(item["id"])))
    assert saved.unit_price == Decimal("20.10") and saved.variant_name_snapshot == "250g"
    path = f"/api/orders/{order['id']}"
    assert client.get(path).json()["customer"]["id"] == order_payload["customer_id"]
    assert client.patch(path + "/status", json={"status": "confirmed"}).json()["status"] == "confirmed"
    assert client.patch(path + "/payment-status", json={"payment_status": "paid"}).json()["payment_status"] == "paid"
    assert client.patch(path + "/status", json={"status": "invented"}).status_code == 422
    second = client.post("/api/orders", json=order_payload).json()
    assert second["order_number"] != order["order_number"]
    assert len(client.get("/api/orders?limit=1").json()) == 1

@pytest.mark.parametrize("quantity", [0, -1, 1.5])
def test_invalid_quantity(client, order_payload, quantity):
    order_payload["items"][0]["quantity"] = quantity
    assert client.post("/api/orders", json=order_payload).status_code == 422

def test_client_price_rejected(client, order_payload):
    order_payload["items"][0]["unit_price"] = "0.01"
    assert client.post("/api/orders", json=order_payload).status_code == 422

def test_invalid_variant_no_partial_order(client, order_payload, db_session):
    before = db_session.scalar(select(func.count()).select_from(Order))
    order_payload["items"].append({"product_variant_id": str(uuid4()), "quantity": 1})
    assert client.post("/api/orders", json=order_payload).status_code == 404
    assert db_session.scalar(select(func.count()).select_from(Order)) == before

def test_failure_during_persistence_rolls_back(client, order_payload, db_session):
    before = db_session.scalar(select(func.count()).select_from(Order))
    def fail_insert(*args):
        raise RuntimeError("Injected item failure")
    event.listen(OrderItem, "before_insert", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="Injected item failure"):
            client.post("/api/orders", json=order_payload)
    finally:
        event.remove(OrderItem, "before_insert", fail_insert)
    assert db_session.scalar(select(func.count()).select_from(Order)) == before
    assert client.post("/api/orders", json=order_payload).status_code == 201
