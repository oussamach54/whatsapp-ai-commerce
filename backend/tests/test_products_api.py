from decimal import Decimal

def test_product_variant_prices_and_duplicates(client, product, variant):
    path = f"/api/products/{product['id']}"
    assert client.get(path).json()["variants"][0]["id"] == variant["id"]
    assert Decimal(variant["price"]) == Decimal("19.90")
    assert client.post(path + "/variants", json={"sku": variant["sku"], "name": "Duplicate", "price": "1.00"}).status_code == 409
    assert client.post("/api/products", json={"slug": product["slug"], "name": "Duplicate"}).status_code == 409
    assert client.patch(path, json={"name": "New coffee"}).json()["name"] == "New coffee"
    vp = f"/api/variants/{variant['id']}"
    assert client.patch(vp, json={"price": "23.45"}).json()["price"] == "23.45"
    assert client.patch(vp, json={"price": "1.001"}).status_code == 422
    assert client.patch(vp, json={"stock_quantity": -1}).status_code == 422
    assert len(client.get(path + "/variants?limit=1").json()) == 1
    image = client.post(path + "/images", json={"url": "https://example.com/coffee.jpg", "variant_id": variant["id"]})
    assert image.status_code == 201
    assert client.get(path).json()["images"][0]["id"] == image.json()["id"]
