import hashlib
import hmac
import json
import secrets
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from app.core.config import get_settings
from app.db.session import get_db
from app.main import app
from app.models import Customer, Conversation, Message
from app.models.enums import ConversationStatus, MessageDirection, SenderType, MessageType
from app.integrations.whatsapp.client import WhatsAppAPIError
from app.integrations.whatsapp.dependencies import get_whatsapp_client_factory, get_reply_session_factory
from app.integrations.whatsapp.parser import parse_messages
from app.services.whatsapp_service import AUTO_REPLY

PATH = "/api/webhooks/whatsapp"

@pytest.fixture
def wa_setup(monkeypatch):
    import httpx
    # Fail closed if any test accidentally tries the real network transport.
    def no_network(*args, **kwargs):
        pytest.fail("Real HTTP requests are forbidden in WhatsApp tests")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", no_network)
    settings = get_settings().model_copy(update={
        "whatsapp_verify_token": SecretStr(secrets.token_hex(24)),
        "whatsapp_app_secret": SecretStr(secrets.token_hex(24)),
        "whatsapp_phone_number_id": "123456789", "whatsapp_access_token": None})
    outbound = Mock()
    outbound.send_text_message.side_effect = lambda *args: "wamid.out." + uuid4().hex
    old = app.dependency_overrides.copy()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_whatsapp_client_factory] = lambda: lambda: outbound
    try:
        yield settings, outbound
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(old)

@pytest.fixture
def wa_client(wa_setup):
    with TestClient(app) as client:
        yield client

@pytest.fixture
def wa_db_client(wa_setup, db_session):
    def override():
        yield db_session
    @contextmanager
    def factory():
        yield db_session
    app.dependency_overrides[get_db] = override
    app.dependency_overrides[get_reply_session_factory] = lambda: factory
    with TestClient(app) as client:
        yield client

def payload(message_id=None, phone="212600000001"):
    return {"object": "whatsapp_business_account", "entry": [{"changes": [{"field": "messages", "value": {
        "metadata": {"phone_number_id": "123456789"}, "messages": [{"from": phone,
        "id": message_id or "wamid.in." + uuid4().hex, "timestamp": "1700000000",
        "type": "text", "text": {"body": "Bonjour"}}]}}]}]}

def signed_post(client, settings, data):
    body = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
    signature = hmac.new(settings.whatsapp_app_secret.get_secret_value().encode(), body, hashlib.sha256).hexdigest()
    return client.post(PATH, content=body, headers={"X-Hub-Signature-256": "sha256=" + signature, "Content-Type": "application/json"})

def test_verification(wa_client, wa_setup):
    settings, _ = wa_setup
    query = {"hub.mode": "subscribe", "hub.verify_token": settings.whatsapp_verify_token.get_secret_value(), "hub.challenge": "001234"}
    response = wa_client.get(PATH, params=query)
    assert response.status_code == 200 and response.text == "001234"
    assert response.headers["content-type"].startswith("text/plain")
    query["hub.mode"] = "wrong"
    assert wa_client.get(PATH, params=query).status_code == 403
    query.update({"hub.mode": "subscribe", "hub.verify_token": "incorrect"})
    assert wa_client.get(PATH, params=query).status_code == 403
    assert wa_client.get(PATH).status_code == 403

@pytest.mark.parametrize("signature", [None, "", "sha256=wrong", "sha256=" + "0" * 64, "sha1=" + "0" * 64])
def test_bad_signatures(wa_client, signature):
    headers = {} if signature is None else {"X-Hub-Signature-256": signature}
    assert wa_client.post(PATH, content=b"{}", headers=headers).status_code == 403

@pytest.mark.parametrize("data", [{}, [], {"entry": None}, {"object": "whatsapp_business_account", "entry": [None, {}]},
    {"object": "whatsapp_business_account", "entry": [{"changes": [None, {"field": "unknown"}]}]}])
def test_unknown_payloads(wa_client, wa_setup, data):
    assert signed_post(wa_client, wa_setup[0], data).status_code == 200
    wa_setup[1].send_text_message.assert_not_called()

def test_status_media_malformed_and_wrong_receiver(wa_client, wa_setup):
    data = payload()
    value = data["entry"][0]["changes"][0]["value"]
    value["statuses"] = [{"status": "read"}]
    value["messages"] = []
    assert signed_post(wa_client, wa_setup[0], data).status_code == 200
    value["messages"] = [{"type": "image", "image": {}}, {"type": "text", "text": []}]
    assert signed_post(wa_client, wa_setup[0], data).status_code == 200
    data = payload()
    data["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"] = "other"
    assert signed_post(wa_client, wa_setup[0], data).status_code == 200
    assert signed_post(wa_client, wa_setup[0], b"{bad json").status_code == 400
    wa_setup[1].send_text_message.assert_not_called()

def test_missing_configuration(wa_client, wa_setup):
    settings, _ = wa_setup
    settings.whatsapp_app_secret = None
    assert wa_client.post(PATH, content=b"{}").status_code == 503
    settings.whatsapp_verify_token = None
    assert wa_client.get(PATH).status_code == 503

def test_parser_timestamp_and_invalid_item():
    data = payload()
    parsed = parse_messages(data, "123456789")
    assert parsed[0].timestamp == datetime.fromtimestamp(1700000000, tz=timezone.utc)
    value = data["entry"][0]["changes"][0]["value"]
    value["messages"].append({"type": "text", "text": {"body": "missing id"}})
    assert len(parse_messages(data, "123456789")) == 1

def test_inbound_reply_idempotency_and_reuse(wa_db_client, wa_setup, db_session):
    settings, outbound = wa_setup
    phone = "212" + str(uuid4().int)[:12]
    data = payload(phone=phone)
    external_id = data["entry"][0]["changes"][0]["value"]["messages"][0]["id"]
    assert signed_post(wa_db_client, settings, data).status_code == 200
    inbound = db_session.scalars(select(Message).where(Message.external_message_id == external_id)).one()
    assert inbound.content == "Bonjour" and inbound.sender_type == SenderType.CUSTOMER
    assert inbound.direction == MessageDirection.INBOUND
    assert inbound.created_at == datetime.fromtimestamp(1700000000, tz=timezone.utc)
    assert inbound.metadata_["provider"] == "whatsapp"
    conversation_id = inbound.conversation_id
    outbound.send_text_message.assert_called_once_with(phone, AUTO_REPLY)
    messages = list(db_session.scalars(select(Message).where(Message.conversation_id == conversation_id)))
    assert len(messages) == 2
    reply = next(m for m in messages if m.direction == MessageDirection.OUTBOUND)
    assert reply.sender_type == SenderType.SYSTEM and reply.content == AUTO_REPLY
    assert reply.external_message_id.startswith("wamid.out.")
    assert signed_post(wa_db_client, settings, data).status_code == 200
    assert outbound.send_text_message.call_count == 1
    assert db_session.scalar(select(func.count()).select_from(Customer).where(Customer.phone_number == phone)) == 1
    customer = db_session.scalars(select(Customer).where(Customer.phone_number == phone)).one()
    assert db_session.scalar(select(func.count()).select_from(Conversation).where(Conversation.customer_id == customer.id)) == 1
    assert db_session.scalar(select(func.count()).select_from(Message).where(Message.external_message_id == external_id)) == 1
    assert signed_post(wa_db_client, settings, payload(phone=phone)).status_code == 200
    assert outbound.send_text_message.call_count == 2
    assert db_session.scalar(select(func.count()).select_from(Conversation).where(Conversation.customer_id == customer.id)) == 1
    conversation = db_session.get(Conversation, conversation_id)
    conversation.status = ConversationStatus.CLOSED
    db_session.commit()
    assert signed_post(wa_db_client, settings, payload(phone=phone)).status_code == 200
    assert db_session.scalar(select(func.count()).select_from(Conversation).where(Conversation.customer_id == customer.id)) == 2

def test_outbound_failure_keeps_inbound(wa_db_client, wa_setup, db_session):
    settings, outbound = wa_setup
    outbound.send_text_message.side_effect = WhatsAppAPIError("Test failure")
    data = payload(phone="212" + str(uuid4().int)[:12])
    external_id = data["entry"][0]["changes"][0]["value"]["messages"][0]["id"]
    assert signed_post(wa_db_client, settings, data).status_code == 200
    assert signed_post(wa_db_client, settings, data).status_code == 200
    inbound = db_session.scalars(select(Message).where(Message.external_message_id == external_id)).one()
    assert inbound.content == "Bonjour"
    assert db_session.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == inbound.conversation_id)) == 1
    assert outbound.send_text_message.call_count == 1

def test_database_enforces_external_id_uniqueness(wa_db_client, wa_setup, db_session):
    data = payload(phone="212" + str(uuid4().int)[:12])
    assert signed_post(wa_db_client, wa_setup[0], data).status_code == 200
    external_id = data["entry"][0]["changes"][0]["value"]["messages"][0]["id"]
    inbound = db_session.scalars(select(Message).where(Message.external_message_id == external_id)).one()
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.add(Message(conversation_id=inbound.conversation_id, external_message_id=external_id,
                direction=MessageDirection.INBOUND, sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT))
            db_session.flush()


def test_text_route_schedules_only_new_messages(wa_client, wa_setup, monkeypatch):
    from app.api.routes import whatsapp
    from app.integrations.whatsapp.schemas import ReplyTarget
    target = ReplyTarget(conversation_id=uuid4(), inbound_id=uuid4(), phone_number="212600000001")
    persist = Mock(side_effect=[target, None])
    reply = Mock()
    monkeypatch.setattr(whatsapp, "persist_inbound", persist)
    monkeypatch.setattr(whatsapp, "send_automatic_reply", reply)
    data = payload()
    assert signed_post(wa_client, wa_setup[0], data).status_code == 200
    assert signed_post(wa_client, wa_setup[0], data).status_code == 200
    assert persist.call_count == 2
    reply.assert_called_once()
    assert reply.call_args.args[0] == target
    assert reply.call_args.args[1] is wa_setup[1]


def test_raw_body_tampering_rejected(wa_client, wa_setup):
    body = json.dumps(payload()).encode()
    signature = hmac.new(wa_setup[0].whatsapp_app_secret.get_secret_value().encode(), body, hashlib.sha256).hexdigest()
    response = wa_client.post(PATH, content=body + b" ", headers={"X-Hub-Signature-256": "sha256=" + signature})
    assert response.status_code == 403
