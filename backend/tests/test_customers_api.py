from uuid import uuid4

def test_customer_create_retrieve_update_duplicate(client, customer):
    path = f"/api/customers/{customer['id']}"
    assert client.get(path).json() == customer
    assert client.patch(path, json={"first_name": "Sam"}).json()["first_name"] == "Sam"
    assert client.patch(path, json={"first_name": None}).json()["first_name"] is None
    assert client.patch(path, json={"phone_number": None}).status_code == 422
    assert client.post("/api/customers", json={"phone_number": customer["phone_number"]}).status_code == 409
    assert client.get(path).status_code == 200
    assert client.get(f"/api/customers/{uuid4()}").status_code == 404
    assert len(client.get("/api/customers?limit=1").json()) == 1
    assert client.get("/api/customers?limit=101").status_code == 422
    assert client.get("/api/customers?offset=-1").status_code == 422
