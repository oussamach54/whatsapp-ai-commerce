"""Independent PostgreSQL cancellation transactions with deterministic lock races."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Event
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import select, func, delete
from sqlalchemy.orm import Session

from app.ai.catalog_orchestrator import run_catalog
from app.integrations.whatsapp.schemas import ReplyTarget
from app.models import Message, Order, ProductVariant, InventoryMovement, Conversation
from app.models.enums import MessageDirection, SenderType, MessageType, OrderStatus, ConversationChannel, ConversationStatus
from app.schemas.order import OrderStatusUpdate
from app.services.catalog_service import CatalogService
from app.services.order_service import update_order_status
from app.services.common import ServiceError
from app.services import order_cancellation_service as cancellation
from tests.test_cod_concurrency import committed_carts, run as create_checkout


def run(settings, sessions, target, text):
    service = Mock(settings=settings)
    service._client.respond.side_effect = AssertionError("Cancellation must not call a provider")
    admission = Mock()
    admission.safe_record.return_value = True
    return run_catalog(service, text, [], "english", admission, CatalogService(sessions, settings, target))


@pytest.fixture
def committed_cancellation(committed_carts):
    engine, sessions, settings, targets, vid = committed_carts
    create_checkout(settings, sessions, targets[0])
    now = datetime.now(timezone.utc)
    with sessions() as db:
        order = db.scalar(select(Order).where(Order.source_conversation_id == targets[0].conversation_id))
        order_id = order.id
        request = Message(conversation_id=targets[0].conversation_id, direction=MessageDirection.INBOUND,
            sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT, content="cancel my order", created_at=now,
            metadata_={"provider": "whatsapp", "phone_number_id": settings.whatsapp_phone_number_id})
        db.add(request)
        db.commit()
        request_target = ReplyTarget(conversation_id=request.conversation_id, inbound_id=request.id, phone_number=targets[0].phone_number)
    proposal = run(settings, sessions, request_target, "cancel my order")
    with sessions() as db:
        prompt = Message(conversation_id=request_target.conversation_id, direction=MessageDirection.OUTBOUND,
            sender_type=SenderType.SYSTEM, message_type=MessageType.TEXT, content=proposal.text,
            external_message_id=uuid4().hex, created_at=now + timedelta(seconds=1), metadata_={"provider": "whatsapp",
            "turn_status": "current", "in_reply_to": str(request_target.inbound_id), "commerce_state": proposal.commerce_state,
            "catalog_refs": proposal.catalog_refs, "confirmation_prompt": proposal.confirmation_prompt})
        confirm = Message(conversation_id=request_target.conversation_id, direction=MessageDirection.INBOUND,
            sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT, content="oui", created_at=now + timedelta(seconds=2),
            metadata_={"provider": "whatsapp", "phone_number_id": settings.whatsapp_phone_number_id})
        db.add_all([prompt, confirm])
        db.commit()
        target = ReplyTarget(conversation_id=confirm.conversation_id, inbound_id=confirm.id, phone_number=targets[0].phone_number)
    return engine, sessions, settings, target, vid, order_id


def test_concurrent_cancellation_workers_reverse_inventory_once(committed_cancellation, monkeypatch):
    engine, sessions, settings, target, vid, oid = committed_cancellation
    barrier = Barrier(2)
    original = cancellation.finalize
    def together(turn, reply):
        barrier.wait(timeout=10)
        return original(turn, reply)
    monkeypatch.setattr(cancellation, "finalize", together)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, settings, sessions, target, "oui") for _ in range(2)]
        replies = [f.result(timeout=20) for f in futures]
    assert all(reply.commerce_state["cancellation"]["status"] == "cancelled" for reply in replies)
    with Session(engine) as db:
        assert db.get(Order, oid).status == OrderStatus.CANCELLED
        assert db.get(ProductVariant, vid).stock_quantity == 1
        assert db.scalar(select(func.count()).select_from(InventoryMovement).where(
            InventoryMovement.reference_id == str(oid), InventoryMovement.reason == cancellation.RESTORE_REASON)) == 1


def test_two_owned_conversations_cancel_same_order_once(committed_cancellation, monkeypatch):
    engine, sessions, settings, first, vid, oid = committed_cancellation
    now = datetime.now(timezone.utc) + timedelta(seconds=5)
    with sessions() as db:
        order = db.get(Order, oid)
        conversation = Conversation(customer_id=order.customer_id, channel=ConversationChannel.WHATSAPP,
            status=ConversationStatus.ACTIVE)
        request = Message(conversation=conversation, direction=MessageDirection.INBOUND,
            sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT, content="cancel my order", created_at=now,
            metadata_={"provider": "whatsapp", "phone_number_id": settings.whatsapp_phone_number_id})
        db.add(request)
        db.commit()
        cid = conversation.id
        request_target = ReplyTarget(conversation_id=cid, inbound_id=request.id, phone_number=first.phone_number)
    try:
        proposal = run(settings, sessions, request_target, "cancel my order")
        with sessions() as db:
            prompt = Message(conversation_id=cid, direction=MessageDirection.OUTBOUND,
                sender_type=SenderType.SYSTEM, message_type=MessageType.TEXT, content=proposal.text,
                external_message_id=uuid4().hex, created_at=now + timedelta(seconds=1), metadata_={"provider": "whatsapp",
                "turn_status": "current", "in_reply_to": str(request_target.inbound_id), "commerce_state": proposal.commerce_state,
                "catalog_refs": proposal.catalog_refs, "confirmation_prompt": proposal.confirmation_prompt})
            origin = Message(conversation_id=cid, direction=MessageDirection.INBOUND,
                sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT, content="oui", created_at=now + timedelta(seconds=2),
                metadata_={"provider": "whatsapp", "phone_number_id": settings.whatsapp_phone_number_id})
            db.add_all([prompt, origin])
            db.commit()
            second = ReplyTarget(conversation_id=cid, inbound_id=origin.id, phone_number=first.phone_number)
        barrier = Barrier(2)
        original = cancellation.finalize
        def together(turn, reply):
            barrier.wait(timeout=10)
            return original(turn, reply)
        monkeypatch.setattr(cancellation, "finalize", together)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run, settings, sessions, target, "oui") for target in (first, second)]
            replies = [f.result(timeout=20) for f in futures]
        assert all(reply.commerce_state["cancellation"]["status"] == "cancelled" for reply in replies)
        with sessions() as db:
            assert db.get(Order, oid).status == OrderStatus.CANCELLED
            assert db.get(ProductVariant, vid).stock_quantity == 1
            assert db.scalar(select(func.count()).select_from(InventoryMovement).where(
                InventoryMovement.reference_id == str(oid), InventoryMovement.reason == cancellation.RESTORE_REASON)) == 1
    finally:
        with sessions() as db:
            db.execute(delete(Message).where(Message.conversation_id == cid))
            db.execute(delete(Conversation).where(Conversation.id == cid))
            db.commit()


@pytest.mark.parametrize("winner", ["fulfillment", "cancellation"])
def test_cancellation_racing_fulfillment_is_serialized(committed_cancellation, monkeypatch, winner):
    engine, sessions, settings, target, vid, oid = committed_cancellation
    locked, attempted = Event(), Event()
    if winner == "fulfillment":
        original = cancellation.finalize
        def attempting(turn, reply):
            attempted.set()
            return original(turn, reply)
        monkeypatch.setattr(cancellation, "finalize", attempting)
        def fulfillment():
            with Session(engine) as db:
                order = db.scalar(select(Order).where(Order.id == oid).with_for_update())
                locked.set()
                assert attempted.wait(10)
                order.status = OrderStatus.SHIPPED
                db.commit()
        with ThreadPoolExecutor(max_workers=2) as pool:
            admin = pool.submit(fulfillment)
            assert locked.wait(10)
            customer = pool.submit(run, settings, sessions, target, "oui")
            admin.result(timeout=20)
            reply = customer.result(timeout=20)
        assert "team" in reply.text
    else:
        original_restore = cancellation.restore_locked
        def hold_order_lock(db, order):
            locked.set()
            assert attempted.wait(10)
            return original_restore(db, order)
        monkeypatch.setattr(cancellation, "restore_locked", hold_order_lock)
        def fulfillment():
            attempted.set()
            with Session(engine) as db:
                with pytest.raises(ServiceError, match="cannot reenter"):
                    update_order_status(db, oid, OrderStatusUpdate(status=OrderStatus.SHIPPED))
        with ThreadPoolExecutor(max_workers=2) as pool:
            customer = pool.submit(run, settings, sessions, target, "oui")
            assert locked.wait(10)
            admin = pool.submit(fulfillment)
            reply = customer.result(timeout=20)
            admin.result(timeout=20)
        assert "successfully" in reply.text
    with Session(engine) as db:
        assert db.get(Order, oid).status == (OrderStatus.SHIPPED if winner == "fulfillment" else OrderStatus.CANCELLED)
        assert db.get(ProductVariant, vid).stock_quantity == (0 if winner == "fulfillment" else 1)
        assert db.scalar(select(func.count()).select_from(InventoryMovement).where(
            InventoryMovement.reference_id == str(oid), InventoryMovement.reason == cancellation.RESTORE_REASON)) == (0 if winner == "fulfillment" else 1)
