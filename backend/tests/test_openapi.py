from fastapi.testclient import TestClient
from app.main import app

def test_openapi_and_docs():
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 200
        schema = client.get("/openapi.json").json()
        assert "/api/orders/{order_id}/payment-status" in schema["paths"]
        assert {op["tags"][0] for path, ops in schema["paths"].items() if path.startswith("/api") for op in ops.values()} == {"Customers", "Products", "Orders", "Conversations", "Inventory", "WhatsApp"}
