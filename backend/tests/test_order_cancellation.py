"""Cancellation through persisted WhatsApp turns; all provider boundaries mocked."""
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import Mock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, func, event
from sqlalchemy.exc import NoResultFound

from app.ai.order_cancellation import cancellation_request
from app.models import Customer, InventoryMovement, Message, Order, OrderItem
from app.models.enums import OrderStatus, PaymentMethod, PaymentStatus, MessageType
from app.services import order_cancellation_service as cancellation
from app.services.common import ServiceError
from app.services.order_service import update_order_status
from app.schemas.order import OrderStatusUpdate
from tests.test_cod_checkout import checkout, cart, plan, item, multi, order_count
from tests.test_commerce_conversations import dialogue, refs
from tests.test_catalog_variant_followups import pants


def completed(checkout, db_session, multiple=False):
    checkout.catalog.settings.checkout_required_fields = ["phone"]
    multi(checkout) if multiple else checkout("je veux commander pantalon noir M")
    row = checkout("oui")
    return row, db_session.get(Order, UUID(cart(row)["order_id"]))


def restored(db, order):
    return list(db.scalars(select(InventoryMovement).where(InventoryMovement.reference_id == str(order.id),
        InventoryMovement.reason == cancellation.RESTORE_REASON)))


@pytest.mark.parametrize("text", ["non annulé l commande", "annulé commande", "annuler l commande",
    "bghit nlghi commande", "ma b9itch baghi commande", "cancel commande", "annuler ma commande",
    "je veux annuler la commande", "cancel my order", "I want to cancel my order", "please cancel my order",
    "je souhaiterais annuler ma commande", "could you please cancel my order?", "please withdraw my purchase"])
def test_natural_request_only_prompts_owned_order(checkout, db_session, pants, text):
    original, order = completed(checkout, db_session)
    calls = checkout.service._client.respond.call_count
    row = checkout(text, [plan(text, "cancellation", language="french",
        speech_act="question" if text.endswith("?") else "affirmative")])
    assert order.order_number in row.content and "1× Pantalon Classic" in row.content
    assert "Noir / M" in row.content and "249.00 MAD" in row.content
    assert row.metadata_["confirmation_prompt"]["action"] == "order_cancellation"
    pending = row.metadata_["commerce_state"]["cancellation"]
    assert pending["customer_id"] == str(order.customer_id) and pending["order_ids"] == [str(order.id)]
    assert pending["conversation_id"] == str(row.conversation_id)
    assert pending["status"] == "awaiting_confirmation"
    assert refs(row) == refs(original) and cart(row) == cart(original)
    db_session.refresh(order)
    assert order.status == OrderStatus.CONFIRMED and pants[1].stock_quantity == 4
    assert not restored(db_session, order)
    if cancellation_request(text):
        assert checkout.service._client.respond.call_count == calls


@pytest.mark.parametrize("text", ["non", "no", "don't cancel my order", "if I cancel my order",
    '"cancel my order"', "do not cancel my order", "yes", "kayn f stock?"])
def test_nonrequests_never_match_cancellation_shortcut(text):
    assert not cancellation_request(text)


@pytest.mark.parametrize("answer", ["oui", "yes", "wakha", "je confirme"])
def test_immediate_explicit_confirmation_cancels_once(checkout, db_session, pants, answer):
    original, order = completed(checkout, db_session)
    checkout("cancel my order")
    row = checkout(answer)
    db_session.refresh(order)
    assert order.status == OrderStatus.CANCELLED and pants[1].stock_quantity == 5
    movements = restored(db_session, order)
    assert len(movements) == 1 and movements[0].quantity_change == 1
    assert movements[0].reference_type == "order" and movements[0].product_variant_id == pants[1].id
    assert row.metadata_["commerce_state"]["cancellation"]["confirmation_message_id"] == row.metadata_["in_reply_to"]
    assert cart(row) == cart(original) and cart(row)["status"] == "completed"
    assert refs(row) == refs(original) and order_count(db_session) == 1
    again = checkout("oui")
    assert "already" in again.content.lower() or "déjà" in again.content.lower()
    checkout("cancel my order")
    assert len(restored(db_session, order)) == 1 and pants[1].stock_quantity == 5


def test_decline_leaves_order_and_cart_unchanged(checkout, db_session, pants):
    original, order = completed(checkout, db_session)
    checkout("cancel my order")
    row = checkout("non")
    db_session.refresh(order)
    assert order.status == OrderStatus.CONFIRMED and pants[1].stock_quantity == 4
    assert cart(row) == cart(original) and not restored(db_session, order)


def test_generic_affirmative_and_checkout_consent_do_not_cancel(checkout, db_session, pants):
    _, order = completed(checkout, db_session)
    checkout("oui")
    db_session.refresh(order)
    assert order.status == OrderStatus.CONFIRMED and not restored(db_session, order)
    checkout("je veux commander pantalon bleu M")
    row = checkout("oui")
    assert cart(row)["status"] == "completed" and order_count(db_session) == 2
    db_session.refresh(order)
    assert order.status == OrderStatus.CONFIRMED


def test_cancel_consent_does_not_confirm_unrelated_pending_checkout(checkout, db_session, pants):
    _, order = completed(checkout, db_session)
    pending_cart = cart(checkout("je veux commander pantalon bleu M"))
    checkout(f"cancel my order {order.order_number}")
    row = checkout("oui")
    assert cart(row) == pending_cart and cart(row)["status"] == "awaiting_confirmation"
    assert order_count(db_session) == 1 and pants[3].stock_quantity == 3
    db_session.refresh(order)
    assert order.status == OrderStatus.CANCELLED


@pytest.mark.parametrize("question", ["kayn f stock?", "kayn f coton?", "taille XXL?", "merci"])
def test_intervening_prompt_supersedes_cancellation_consent(checkout, db_session, pants, question):
    _, order = completed(checkout, db_session)
    checkout("cancel my order")
    checkout(question)
    checkout("oui")
    db_session.refresh(order)
    assert order.status == OrderStatus.CONFIRMED and not restored(db_session, order)
    assert pants[1].stock_quantity == 4


def foreign_order(db_session):
    customer = Customer(phone_number=uuid4().hex)
    order = Order(customer=customer, status=OrderStatus.CONFIRMED, payment_method=PaymentMethod.COD,
        payment_status=PaymentStatus.PENDING, currency="MAD", subtotal=Decimal("999"), total=Decimal("999"),
        shipping_phone_number="212600009999", shipping_country="MA")
    db_session.add(order)
    db_session.flush()
    return order


def test_foreign_order_and_guessed_number_never_disclose_or_mutate(checkout, db_session):
    other = foreign_order(db_session)
    row = checkout("cancel my order")
    assert other.order_number not in row.content and "999" not in row.content
    row = checkout(f"cancel my order {other.order_number}")
    assert other.order_number not in row.content and "999" not in row.content
    checkout("oui")
    db_session.refresh(other)
    assert other.status == OrderStatus.CONFIRMED and not restored(db_session, other)


def test_multiple_orders_require_bounded_choice_and_new_confirmation(checkout, db_session):
    _, first = completed(checkout, db_session)
    _, second = completed(checkout, db_session)
    row = checkout("cancel my order")
    assert first.order_number in row.content and second.order_number in row.content
    assert row.metadata_["commerce_state"]["cancellation"]["status"] == "choosing"
    chosen_id = UUID(row.metadata_["commerce_state"]["cancellation"]["order_ids"][0])
    assert "confirmation_prompt" not in row.metadata_
    selected = checkout("1")
    assert selected.metadata_["commerce_state"]["cancellation"]["status"] == "awaiting_confirmation"
    assert db_session.get(Order, chosen_id).order_number in selected.content
    checkout("oui")
    db_session.refresh(first)
    db_session.refresh(second)
    assert db_session.get(Order, chosen_id).status == OrderStatus.CANCELLED
    assert (second if chosen_id == first.id else first).status == OrderStatus.CONFIRMED


@pytest.mark.parametrize("status", [OrderStatus.PROCESSING, OrderStatus.SHIPPED, OrderStatus.DELIVERED])
def test_non_cancellable_status_requires_human(checkout, db_session, status):
    _, order = completed(checkout, db_session)
    order.status = status
    db_session.flush()
    row = checkout("cancel my order")
    assert "team" in row.content or "équipe" in row.content
    assert "confirmation_prompt" not in row.metadata_
    checkout("oui")
    db_session.refresh(order)
    assert order.status == status and not restored(db_session, order)


def test_configurable_processing_policy_and_paid_order_refused(checkout, db_session):
    _, order = completed(checkout, db_session)
    order.status = OrderStatus.PROCESSING
    checkout.catalog.settings.customer_cancellation_statuses.append("processing")
    db_session.flush()
    assert "confirmation_prompt" in checkout("cancel my order").metadata_
    order.payment_status = PaymentStatus.PAID
    db_session.flush()
    row = checkout("oui")
    db_session.refresh(order)
    assert order.status == OrderStatus.PROCESSING and not restored(db_session, order)
    assert "équipe" in row.content or "team" in row.content


def test_multi_item_restoration_and_immutable_price_snapshots(checkout, db_session, pants):
    _, order = completed(checkout, db_session, multiple=True)
    snapshots = [(i.id, i.unit_price, i.line_total, i.quantity, i.product_name_snapshot, i.variant_name_snapshot) for i in order.items]
    money = order.subtotal, order.shipping_cost, order.total
    pants[1].price, pants[3].price = Decimal("999.99"), Decimal("1.00")
    db_session.flush()
    prompt = checkout("cancel my order")
    assert "727.00 MAD" in prompt.content and "999.99" not in prompt.content
    checkout("oui")
    assert pants[1].stock_quantity == 5 and pants[3].stock_quantity == 3
    assert sorted(m.quantity_change for m in restored(db_session, order)) == [1, 2]
    db_session.refresh(order)
    assert (order.subtotal, order.shipping_cost, order.total) == money
    assert [(i.id, i.unit_price, i.line_total, i.quantity, i.product_name_snapshot, i.variant_name_snapshot) for i in order.items] == snapshots


def test_order_without_audited_debit_does_not_restore_stock(checkout, db_session, pants):
    # API/manual orders have snapshots but do not decrement inventory.
    other = foreign_order(db_session)
    from app.models import Conversation
    other.customer_id = db_session.get(Conversation, checkout.catalog.target.conversation_id).customer_id
    db_session.add(OrderItem(order=other, product_variant_id=pants[1].id, quantity=2, unit_price=Decimal("249"),
        line_total=Decimal("498"), product_name_snapshot="Pantalon Classic", variant_name_snapshot="Noir / M", sku_snapshot="manual"))
    db_session.flush()
    before = pants[1].stock_quantity
    checkout("cancel my order")
    checkout("oui")
    db_session.refresh(other)
    assert other.status == OrderStatus.CANCELLED and pants[1].stock_quantity == before
    assert not restored(db_session, other)


@pytest.mark.parametrize("failure", ["undelivered", "send_error", "outbound_persistence", "expired", "wrong_marker", "image_origin"])
def test_missing_or_invalid_prompt_cannot_authorize(checkout, db_session, failure):
    _, order = completed(checkout, db_session)
    if failure in ("send_error", "outbound_persistence"):
        from app.integrations.whatsapp.client import WhatsAppAPIError
        original = checkout.sender.send_text_message.side_effect
        if failure == "send_error":
            checkout.sender.send_text_message.side_effect = WhatsAppAPIError("simulated")
            with pytest.raises(NoResultFound):
                checkout("cancel my order")
        else:
            with patch("app.services.whatsapp_service.stage_message", side_effect=RuntimeError("simulated")):
                with pytest.raises(NoResultFound):
                    checkout("cancel my order")
        checkout.sender.send_text_message.side_effect = original
        # The dialogue fixture advances from its last successful outbound. Give
        # the failed intervening inbound a distinct, earlier receipt position.
        failed = db_session.scalar(select(Message).where(Message.content == "cancel my order").order_by(Message.created_at.desc()))
        failed.created_at -= timedelta(seconds=.25)
        db_session.flush()
    else:
        row = checkout("cancel my order")
        if failure == "undelivered":
            row.external_message_id = ""
        elif failure == "wrong_marker":
            row.metadata_ = dict(row.metadata_, confirmation_prompt={"action": "checkout"})
        elif failure == "expired":
            inbound = db_session.get(Message, UUID(row.metadata_["in_reply_to"]))
            data = dict(inbound.metadata_["cancellation_state"])
            data["offered_at"] = (datetime.fromisoformat(data["offered_at"]) - timedelta(minutes=16)).isoformat()
            inbound.metadata_ = dict(inbound.metadata_, cancellation_state=data)
        else:
            original = cancellation.authorized
            def image_origin(db, catalog, pending):
                db.get(Message, catalog.target.inbound_id).message_type = MessageType.IMAGE
                db.flush()
                return original(db, catalog, pending)
            with patch.object(cancellation, "authorized", side_effect=image_origin):
                checkout("oui")
            db_session.refresh(order)
            assert order.status == OrderStatus.CONFIRMED
            return
        db_session.flush()
    checkout("oui")
    db_session.refresh(order)
    assert order.status == OrderStatus.CONFIRMED and not restored(db_session, order)


def test_duplicate_webhook_and_worker_retry_are_idempotent(checkout, db_session, pants):
    from app.integrations.whatsapp.schemas import IncomingText, ReplyTarget
    from app.services.whatsapp_service import persist_inbound, send_automatic_reply
    _, order = completed(checkout, db_session)
    checkout("cancel my order")
    row = checkout("oui")
    inbound = db_session.get(Message, UUID(row.metadata_["in_reply_to"]))
    external = uuid4().hex
    inbound.external_message_id = external
    db_session.flush()
    catalog = checkout.catalog
    customer = db_session.get(Customer, order.customer_id)
    incoming = IncomingText(external_message_id=external, phone_number=customer.phone_number, text="oui",
        phone_number_id=catalog.settings.whatsapp_phone_number_id)
    assert persist_inbound(db_session, incoming) is None
    target = ReplyTarget(conversation_id=row.conversation_id, inbound_id=inbound.id, phone_number=customer.phone_number)
    send_automatic_reply(target, checkout.sender, catalog.sessions, "oui", checkout.service)
    assert len(restored(db_session, order)) == 1 and pants[1].stock_quantity == 5


def test_atomic_rollback_if_inventory_audit_insert_fails(checkout, db_session, pants):
    _, order = completed(checkout, db_session)
    checkout("cancel my order")
    def fail(mapper, connection, movement):
        if movement.reason == cancellation.RESTORE_REASON:
            raise RuntimeError("injected audit failure")
    event.listen(InventoryMovement, "before_insert", fail)
    try:
        row = checkout("oui")
    finally:
        event.remove(InventoryMovement, "before_insert", fail)
    db_session.refresh(order)
    assert order.status == OrderStatus.CONFIRMED and pants[1].stock_quantity == 4
    assert not restored(db_session, order) and "successfully" not in row.content


@pytest.mark.parametrize("key", ["cancellation_state", "confirmation_prompt"])
def test_control_metadata_is_not_client_writable(client, customer, key):
    conv = client.post("/api/conversations", json={"customer_id": customer["id"], "channel": "whatsapp", "status": "active"}).json()
    response = client.post(f"/api/conversations/{conv['id']}/messages", json={"direction": "outbound", "sender_type": "system",
        "content": "cancel", "metadata": {key: {"action": "order_cancellation"}}})
    assert response.status_code == 422


def test_cancelled_order_cannot_reenter_fulfillment(checkout, db_session):
    _, order = completed(checkout, db_session)
    checkout("cancel my order")
    checkout("oui")
    with pytest.raises(ServiceError, match="cannot reenter"):
        update_order_status(db_session, order.id, OrderStatusUpdate(status=OrderStatus.SHIPPED))


def test_cancellation_logs_do_not_include_customer_data(checkout, db_session, caplog):
    _, order = completed(checkout, db_session)
    customer = db_session.get(Customer, order.customer_id)
    with caplog.at_level("INFO"):
        checkout("cancel my order")
        checkout("oui")
    assert customer.phone_number not in caplog.text
    assert "12 rue Test" not in caplog.text and "mock-key" not in caplog.text


def test_ownership_rechecked_after_prompt(checkout, db_session):
    _, order = completed(checkout, db_session)
    checkout("cancel my order")
    other = foreign_order(db_session)
    order.customer_id = other.customer_id
    db_session.flush()
    checkout("oui")
    db_session.refresh(order)
    assert order.status == OrderStatus.CONFIRMED and not restored(db_session, order)


def test_pending_existing_status_is_cancellable(checkout, db_session):
    _, order = completed(checkout, db_session)
    order.status = OrderStatus.PENDING
    db_session.flush()
    checkout("cancel my order")
    checkout("oui")
    db_session.refresh(order)
    assert order.status == OrderStatus.CANCELLED


def test_foreign_number_does_not_fall_back_to_own_order(checkout, db_session):
    _, own = completed(checkout, db_session)
    other = foreign_order(db_session)
    row = checkout(f"cancel my order {other.order_number}")
    assert own.order_number not in row.content and other.order_number not in row.content
    checkout("oui")
    db_session.refresh(own)
    db_session.refresh(other)
    assert own.status == other.status == OrderStatus.CONFIRMED


def test_old_order_is_not_silently_reused(checkout, db_session):
    _, order = completed(checkout, db_session)
    order.created_at -= timedelta(days=31)
    db_session.flush()
    row = checkout("cancel my order")
    assert "confirmation_prompt" not in row.metadata_
    checkout("oui")
    db_session.refresh(order)
    assert order.status == OrderStatus.CONFIRMED


def test_cancellation_commit_survives_failed_success_delivery(checkout, db_session, pants):
    from app.integrations.whatsapp.client import WhatsAppAPIError
    from app.integrations.whatsapp.schemas import ReplyTarget
    from app.services.whatsapp_service import send_automatic_reply
    _, order = completed(checkout, db_session)
    checkout("cancel my order")
    original = checkout.sender.send_text_message.side_effect
    checkout.sender.send_text_message.side_effect = WhatsAppAPIError("simulated")
    with pytest.raises(NoResultFound):
        checkout("oui")
    db_session.refresh(order)
    assert order.status == OrderStatus.CANCELLED and pants[1].stock_quantity == 5
    origin = db_session.scalar(select(Message).where(Message.metadata_.has_key("cancellation_state"))
        .order_by(Message.created_at.desc(), Message.id.desc()))
    assert origin.metadata_["cancellation_state"]["status"] == "cancelled"
    checkout.sender.send_text_message.side_effect = original
    customer = db_session.get(Customer, order.customer_id)
    target = ReplyTarget(conversation_id=origin.conversation_id, inbound_id=origin.id, phone_number=customer.phone_number)
    send_automatic_reply(target, checkout.sender, checkout.catalog.sessions, "oui", checkout.service)
    assert len(restored(db_session, order)) == 1 and pants[1].stock_quantity == 5
    assert "already" in checkout.sender.send_text_message.call_args.args[1].lower()
