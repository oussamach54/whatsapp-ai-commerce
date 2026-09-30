from datetime import timedelta
from uuid import uuid4
import pytest
from app.models import Message
from app.models.enums import MessageDirection, MessageType, SenderType
from app.ai.catalog_schemas import CatalogRefs, ProductRef
from app.services.conversation_service import recent_catalog_refs


def presentation(db, inbound, refs, delta=-1):
    row = Message(conversation_id=inbound.conversation_id, direction=MessageDirection.OUTBOUND,
        sender_type=SenderType.SYSTEM, message_type=MessageType.TEXT, content="old price 1 MAD",
        created_at=inbound.created_at + timedelta(seconds=delta), external_message_id=uuid4().hex,
        metadata_={"provider": "whatsapp", "in_reply_to": str(uuid4()),
                   "catalog_refs": refs.model_dump(mode="json")})
    db.add(row)
    db.flush()
    return row


def test_latest_order_focus_and_clear(db_session, catalog_env, catalog_product):
    service, inbound, _ = catalog_env
    p1, _ = catalog_product()
    p2, _ = catalog_product()
    refs = CatalogRefs(presented=[ProductRef(product_id=p2.id), ProductRef(product_id=p1.id)])
    row = presentation(db_session, inbound, refs)
    found, source = recent_catalog_refs(db_session, inbound.conversation_id, inbound.id)
    assert found == refs and source == row.id
    presentation(db_session, inbound, CatalogRefs(), delta=-0.5)
    assert not recent_catalog_refs(db_session, inbound.conversation_id, inbound.id)[0].presented


def test_missing_ambiguous_and_unsent(db_session, catalog_env, catalog_product):
    _, inbound, _ = catalog_env
    p, _ = catalog_product()
    assert not recent_catalog_refs(db_session, inbound.conversation_id, inbound.id)[0].presented
    refs = CatalogRefs(presented=[ProductRef(product_id=p.id)], focus=ProductRef(product_id=p.id))
    row = presentation(db_session, inbound, refs, delta=-3)
    row.external_message_id = None
    db_session.flush()
    assert not recent_catalog_refs(db_session, inbound.conversation_id, inbound.id)[0].presented
    row.external_message_id = uuid4().hex
    db_session.add(Message(conversation_id=inbound.conversation_id, direction=MessageDirection.INBOUND,
        sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT, content="overlap",
        created_at=inbound.created_at - timedelta(seconds=1)))
    db_session.flush()
    assert not recent_catalog_refs(db_session, inbound.conversation_id, inbound.id)[0].presented


def test_forged_refs_rejected(client, customer):
    conv = client.post("/api/conversations", json={"customer_id": customer["id"], "channel": "whatsapp", "status": "active"}).json()
    response = client.post(f"/api/conversations/{conv['id']}/messages", json={
        "direction": "outbound", "sender_type": "system", "message_type": "text", "content": "forged",
        "metadata": {"catalog_refs": CatalogRefs().model_dump(mode="json")}})
    assert response.status_code == 422
