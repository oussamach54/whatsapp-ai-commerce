def test_inventory_history_and_atomic_stock(client, variant):
    data = {"product_variant_id": variant["id"], "quantity_change": 5, "reason": "restock"}
    response = client.post("/api/inventory/movements", json=data)
    assert response.status_code == 201
    vp = f"/api/variants/{variant['id']}"
    history = f"/api/inventory/variants/{variant['id']}/movements"
    assert client.get(vp).json()["stock_quantity"] == 15
    assert client.get(history).json()[0]["id"] == response.json()["id"]
    for delta in (0, -16):
        data["quantity_change"] = delta
        assert client.post("/api/inventory/movements", json=data).status_code == 422
    assert client.get(vp).json()["stock_quantity"] == 15
    assert len(client.get(history).json()) == 1
    data["quantity_change"] = -2
    assert client.post("/api/inventory/movements", json=data).status_code == 201
    assert client.get(vp).json()["stock_quantity"] == 13
