import os

import pytest
from sqlalchemy import URL, create_engine
from sqlalchemy.orm import Session


def _test_database_url() -> URL | None:
    required = ("TEST_DATABASE_HOST", "TEST_DATABASE_NAME", "TEST_DATABASE_USER", "TEST_DATABASE_PASSWORD")
    if not all(os.getenv(name) for name in required):
        return None
    return URL.create(
        "postgresql+psycopg",
        host=os.environ["TEST_DATABASE_HOST"],
        port=int(os.getenv("TEST_DATABASE_PORT", "5432")),
        database=os.environ["TEST_DATABASE_NAME"],
        username=os.environ["TEST_DATABASE_USER"],
        password=os.environ["TEST_DATABASE_PASSWORD"],
    )


@pytest.fixture
def db_session() -> Session:
    database_url = _test_database_url()
    if database_url is None:
        pytest.fail("PostgreSQL tests require TEST_DATABASE_HOST, TEST_DATABASE_NAME, TEST_DATABASE_USER and TEST_DATABASE_PASSWORD")

    engine = create_engine(database_url)
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()


@pytest.fixture
def client(db_session):
    from fastapi.testclient import TestClient
    from app.db.session import get_db
    from app.main import app

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)

@pytest.fixture
def customer(client):
    from uuid import uuid4
    response = client.post("/api/customers", json={"phone_number": uuid4().hex})
    assert response.status_code == 201, response.text
    return response.json()

@pytest.fixture
def product(client):
    from uuid import uuid4
    response = client.post("/api/products", json={"name": "Coffee", "slug": uuid4().hex})
    assert response.status_code == 201, response.text
    return response.json()

@pytest.fixture
def variant(client, product):
    from uuid import uuid4
    response = client.post(f"/api/products/{product['id']}/variants", json={
        "sku": uuid4().hex, "name": "250g", "price": "19.90", "stock_quantity": 10})
    assert response.status_code == 201, response.text
    return response.json()

@pytest.fixture
def order_payload(customer, variant):
    return {"customer_id": customer["id"], "items": [{"product_variant_id": variant["id"], "quantity": 3}],
        "currency": "MAD", "shipping_cost": "5.25", "shipping_full_name": "Test Customer",
        "shipping_phone_number": customer["phone_number"], "shipping_address_line": "1 Test Street",
        "shipping_city": "Casablanca", "shipping_country": "MA"}
