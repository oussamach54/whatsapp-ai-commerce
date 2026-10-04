import os

import pytest
from sqlalchemy import URL, create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def catalog_env(db_session):
    from contextlib import contextmanager
    from uuid import uuid4
    from app.core.config import Settings
    from app.models import Customer, Conversation, Message
    from app.models.enums import ConversationChannel, ConversationStatus, MessageDirection, MessageType, SenderType
    from app.integrations.whatsapp.schemas import ReplyTarget
    from app.services.catalog_service import CatalogService
    settings = Settings(_env_file=None, database_host="localhost", database_name="test",
        database_user="test", database_password="test", whatsapp_phone_number_id="catalog-business",
        openai_model="mock-model", openai_api_key="mock-key")
    conversation = Conversation(customer=Customer(phone_number=uuid4().hex),
        channel=ConversationChannel.WHATSAPP, status=ConversationStatus.ACTIVE)
    inbound = Message(conversation=conversation, direction=MessageDirection.INBOUND,
        sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT, content="montre-moi vos produits",
        metadata_={"provider": "whatsapp", "phone_number_id": settings.whatsapp_phone_number_id})
    db_session.add(inbound)
    db_session.flush()
    target = ReplyTarget(conversation_id=conversation.id, inbound_id=inbound.id, phone_number="212600001111")
    active = []
    @contextmanager
    def sessions():
        active.append(True)
        try:
            yield db_session
        finally:
            active.pop()
    return CatalogService(sessions, settings, target), inbound, active


@pytest.fixture
def catalog_product(db_session):
    from uuid import uuid4
    from decimal import Decimal
    from app.models import Product, ProductVariant
    def create(name="Pantalon", **kwargs):
        p = Product(name=name, slug=uuid4().hex, category="vêtements", description="Description catalogue")
        v = ProductVariant(product=p, name="Noir M", sku=uuid4().hex,
            price=Decimal("100.00"), size="M", color="noir", stock_quantity=4)
        for k, value in kwargs.items():
            setattr(v, k, value)
        db_session.add(p)
        db_session.flush()
        return p, v
    return create


@pytest.fixture
def allowed_admission(monkeypatch):
    """Isolate pre-existing network/persistence tests; real admission has DB tests."""
    from unittest.mock import Mock
    admission = Mock()
    admission.begin.return_value = "allowed"
    admission.reserve.return_value = "allowed"
    admission.safe_record.return_value = True
    monkeypatch.setattr("app.services.whatsapp_service.AIAdmission", Mock(return_value=admission))
    return admission


@pytest.fixture(autouse=True)
def block_paid_ai_requests(monkeypatch):
    import httpx
    import httpx2

    def no_network(*args, **kwargs):
        pytest.fail("Real OpenAI SDK network requests are forbidden in tests")

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", no_network)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)


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
