"""Real independent PostgreSQL transactions: no network, no shared ORM sessions."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import Barrier
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, delete, func
from sqlalchemy.orm import Session

from app.ai.catalog_orchestrator import run_catalog
from app.ai.catalog_schemas import CatalogRefs, ProductRef
from app.ai.checkout_state import Cart, CartLine
from app.core.config import Settings
from app.integrations.whatsapp.schemas import ReplyTarget
from app.models import Customer, Conversation, Message, Product, ProductVariant, Order, OrderItem, InventoryMovement
from app.models.enums import ConversationChannel, ConversationStatus, MessageDirection, MessageType, SenderType
from app.services.catalog_service import CatalogService
from tests.conftest import _test_database_url


@pytest.fixture
def committed_carts():
    engine = create_engine(_test_database_url())
    settings = Settings(_env_file=None, database_host="unused", database_name="unused", database_user="unused",
        database_password="unused", whatsapp_phone_number_id=uuid4().hex, checkout_required_fields=["phone"])
    customers, conversations, targets = [], [], []
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        product = Product(name="Concurrency fixture", slug=uuid4().hex)
        variant = ProductVariant(product=product, name="Last unit", sku=uuid4().hex, price=Decimal("10.10"), stock_quantity=1)
        db.add(product)
        db.flush()
        pid, vid = product.id, variant.id
        for _ in range(2):
            customer = Customer(phone_number="212" + str(uuid4().int)[-9:])
            conversation = Conversation(customer=customer, channel=ConversationChannel.WHATSAPP, status=ConversationStatus.ACTIVE)
            request = Message(conversation=conversation, direction=MessageDirection.INBOUND, sender_type=SenderType.CUSTOMER,
                message_type=MessageType.TEXT, content="I want the last unit", created_at=now - timedelta(seconds=4),
                metadata_={"provider": "whatsapp", "phone_number_id": settings.whatsapp_phone_number_id})
            db.add(request)
            db.flush()
            ref = ProductRef(product_id=pid, variant_id=vid)
            cart = Cart(offered_at=request.created_at, subtotal="10.10", items=[CartLine(target=ref,
                quantity=1, unit_price="10.10", stock=1, product_name=product.name, variant_name=variant.name)])
            request.metadata_ = dict(request.metadata_, checkout_state=cart.model_dump(mode="json"))
            presentation = Message(conversation=conversation, direction=MessageDirection.OUTBOUND, sender_type=SenderType.SYSTEM,
                message_type=MessageType.TEXT, content="10.10 MAD. Confirm?", external_message_id=uuid4().hex,
                created_at=now - timedelta(seconds=3), metadata_={"provider": "whatsapp", "in_reply_to": str(request.id),
                "turn_status": "current", "catalog_refs": CatalogRefs(focus=ref, presented=[ref]).model_dump(mode="json"),
                "commerce_state": {"cart": cart.model_dump(mode="json")}})
            confirm = Message(conversation=conversation, direction=MessageDirection.INBOUND, sender_type=SenderType.CUSTOMER,
                message_type=MessageType.TEXT, content="oui", created_at=now - timedelta(seconds=2),
                metadata_={"provider": "whatsapp", "phone_number_id": settings.whatsapp_phone_number_id})
            db.add_all([presentation, confirm])
            db.flush()
            customers.append(customer.id)
            conversations.append(conversation.id)
            targets.append(ReplyTarget(conversation_id=conversation.id, inbound_id=confirm.id, phone_number=customer.phone_number))
        db.commit()
    @contextmanager
    def sessions():
        with Session(engine) as db:
            yield db
    try:
        yield engine, sessions, settings, targets, vid
    finally:
        with Session(engine) as db:
            ids = select(Order.id).where(Order.source_conversation_id.in_(conversations))
            db.execute(delete(InventoryMovement).where(InventoryMovement.product_variant_id == vid))
            db.execute(delete(OrderItem).where(OrderItem.order_id.in_(ids)))
            db.execute(delete(Order).where(Order.source_conversation_id.in_(conversations)))
            db.execute(delete(Message).where(Message.conversation_id.in_(conversations)))
            db.execute(delete(Conversation).where(Conversation.id.in_(conversations)))
            db.execute(delete(Customer).where(Customer.id.in_(customers)))
            db.execute(delete(ProductVariant).where(ProductVariant.id == vid))
            db.execute(delete(Product).where(Product.id == pid))
            db.commit()
        engine.dispose()


def run(settings, sessions, target):
    service = Mock(settings=settings)
    service._client.respond.side_effect = AssertionError("Confirmation must not call the model")
    admission = Mock()
    admission.safe_record.return_value = True
    return run_catalog(service, "oui", [], "french", admission, CatalogService(sessions, settings, target))


@pytest.mark.parametrize("same_cart", [False, True])
def test_concurrent_last_unit_and_duplicate_cart(committed_carts, same_cart, monkeypatch):
    engine, sessions, settings, targets, vid = committed_carts
    barrier = Barrier(2)
    from app.services import checkout_service
    original_finalize = checkout_service.finalize
    def synchronized_finalize(turn, reply):
        # Both workers have read the same pre-commit state before either writes.
        barrier.wait(timeout=10)
        return original_finalize(turn, reply)
    monkeypatch.setattr(checkout_service, "finalize", synchronized_finalize)
    def worker(target):
        barrier.wait(timeout=10)
        return run(settings, sessions, target)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(worker, target) for target in ([targets[0]] * 2 if same_cart else targets)]
        replies = [future.result(timeout=20) for future in futures]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(OrderItem).where(OrderItem.product_variant_id == vid)) == 1
        assert db.get(ProductVariant, vid).stock_quantity == 0
        assert db.scalar(select(func.count()).select_from(InventoryMovement).where(InventoryMovement.product_variant_id == vid)) == 1
    assert any(reply.commerce_state["cart"]["status"] == "completed" for reply in replies)
    if not same_cart:
        assert {reply.commerce_state["cart"]["status"] for reply in replies} == {"completed", "blocked"}
    else:
        assert all(reply.commerce_state["cart"]["status"] == "completed" for reply in replies)
        assert all("confirmée" in reply.text for reply in replies)


def test_newer_inbound_prevents_stale_order(committed_carts):
    engine, sessions, settings, targets, vid = committed_carts
    with Session(engine) as db:
        db.add(Message(conversation_id=targets[0].conversation_id, direction=MessageDirection.INBOUND,
            sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT, content="cancel",
            created_at=datetime.now(timezone.utc), metadata_={"provider": "whatsapp", "phone_number_id": settings.whatsapp_phone_number_id}))
        db.commit()
    reply = run(settings, sessions, targets[0])
    assert "confirmée" not in reply.text
    with Session(engine) as db:
        assert db.get(ProductVariant, vid).stock_quantity == 1
        assert db.scalar(select(func.count()).select_from(OrderItem).where(OrderItem.product_variant_id == vid)) == 0


def test_unsent_summary_cannot_authorize_order(committed_carts):
    engine, sessions, settings, targets, vid = committed_carts
    with Session(engine) as db:
        db.execute(delete(Message).where(Message.conversation_id == targets[0].conversation_id,
                                        Message.direction == MessageDirection.OUTBOUND))
        db.commit()
    reply = run(settings, sessions, targets[0])
    assert reply.commerce_state["cart"]["status"] == "awaiting_confirmation"
    with Session(engine) as db:
        assert db.get(ProductVariant, vid).stock_quantity == 1


@pytest.mark.parametrize("failure", ["send", "outbound_persistence"])
def test_committed_order_survives_outbound_failure_and_retries(committed_carts, monkeypatch, failure):
    from pydantic import SecretStr
    from app.ai.service import AIService
    from app.integrations.whatsapp.client import WhatsAppAPIError
    from app.integrations.whatsapp.schemas import IncomingText
    from app.services import whatsapp_service
    engine, sessions, settings, targets, vid = committed_carts
    target = targets[0]
    settings.openai_model, settings.openai_api_key = "mock-model", SecretStr("mock-key")
    model = Mock()
    model.respond.side_effect = AssertionError("No model call for confirmation/recovery")
    service = AIService(model, settings=settings, catalog_enabled=True)
    admission = Mock()
    admission.begin.return_value = "allowed"
    admission.safe_record.return_value = True
    monkeypatch.setattr(whatsapp_service, "AIAdmission", Mock(return_value=admission))
    sender = Mock()
    sender.send_text_message.return_value = uuid4().hex
    external_id = uuid4().hex
    with Session(engine) as db:
        db.get(Message, target.inbound_id).external_message_id = external_id
        db.commit()
    original_stage = whatsapp_service.stage_message
    if failure == "send":
        sender.send_text_message.side_effect = WhatsAppAPIError("simulated failure")
    else:
        monkeypatch.setattr(whatsapp_service, "stage_message", Mock(side_effect=RuntimeError("persistence failure")))
    whatsapp_service.send_automatic_reply(target, sender, sessions, "oui", service)
    # Observe a real committed transaction using a new connection/session.
    with Session(engine) as db:
        order = db.scalar(select(Order).where(Order.source_conversation_id == target.conversation_id))
        assert order is not None
        order_id = order.id
        assert db.get(ProductVariant, vid).stock_quantity == 0
        assert db.get(Message, target.inbound_id).metadata_["checkout_state"]["status"] == "completed"
        assert not db.scalar(select(Message.id).where(Message.metadata_["in_reply_to"].astext == str(target.inbound_id)))
        # Provider redelivery remains a no-op even after failed outbound work.
        assert whatsapp_service.persist_inbound(db, IncomingText(external_message_id=external_id,
            phone_number=target.phone_number, text="oui", phone_number_id=settings.whatsapp_phone_number_id)) is None
    sender.send_text_message.side_effect = lambda *args: uuid4().hex
    monkeypatch.setattr(whatsapp_service, "stage_message", original_stage)
    # Explicit worker retry of the same inbound is also safe.
    whatsapp_service.send_automatic_reply(target, sender, sessions, "oui", service)
    assert "Your order is confirmed" in sender.send_text_message.call_args.args[1]
    # A fresh customer confirmation must recover the same order as well.
    with Session(engine) as db:
        retry = whatsapp_service.persist_inbound(db, IncomingText(external_message_id=uuid4().hex,
            phone_number=target.phone_number, text="oui", phone_number_id=settings.whatsapp_phone_number_id))
    whatsapp_service.send_automatic_reply(retry, sender, sessions, "oui", service)
    assert "Votre commande est confirmée" in sender.send_text_message.call_args.args[1]
    with Session(engine) as db:
        assert db.scalars(select(Order.id).where(Order.source_conversation_id == target.conversation_id)).all() == [order_id]
        assert db.get(ProductVariant, vid).stock_quantity == 0
        assert db.scalar(select(func.count()).select_from(InventoryMovement).where(InventoryMovement.product_variant_id == vid)) == 1
    model.respond.assert_not_called()


def test_render_failure_after_commit_recovers_success(committed_carts, monkeypatch):
    from app.ai import checkout
    engine, sessions, settings, targets, vid = committed_carts
    original = checkout.render_cart
    def fail_completed(turn, result="offer", problem=None):
        if result == "completed":
            raise RuntimeError("confirmation rendering failed after commit")
        return original(turn, result, problem)
    monkeypatch.setattr(checkout, "render_cart", fail_completed)
    reply = run(settings, sessions, targets[0])
    assert "confirmée" in reply.text
    assert reply.commerce_state["cart"]["status"] == "completed"
    with Session(engine) as db:
        assert db.get(ProductVariant, vid).stock_quantity == 0
        assert db.scalar(select(func.count()).select_from(OrderItem).where(OrderItem.product_variant_id == vid)) == 1
