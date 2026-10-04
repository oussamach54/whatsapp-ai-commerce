"""Owned COD cancellation: prompt authorization, row locks and ledger reversal."""
import json
from collections import defaultdict
from datetime import timedelta
from sqlalchemy import select, tuple_
from sqlalchemy.orm import selectinload

from app.ai.cancellation_state import PendingCancellation
from app.models import Conversation, Customer, Message, Order, ProductVariant, InventoryMovement
from app.models.enums import OrderStatus, PaymentMethod, PaymentStatus
from app.services.common import transaction
from app.services.conversation_service import message_order_time
from app.services.confirmation_context import prompt_matches

DEBIT_REASON = "cod_order_created"
RESTORE_REASON = "cod_order_cancelled"


def disposition(order, settings):
    if order.status == OrderStatus.CANCELLED:
        return "already"
    if (order.payment_method != PaymentMethod.COD or order.payment_status != PaymentStatus.PENDING
            or order.status.value not in settings.customer_cancellation_statuses):
        return "human"
    return "eligible"


def customer_for(db, catalog):
    catalog.authorize(db)
    customer = db.scalar(select(Customer).join(Conversation).where(
        Conversation.id == catalog.target.conversation_id))
    if not customer or customer.is_blocked:
        raise ValueError("cancellation_customer_unavailable")
    return customer


def owned_orders(db, catalog, customer_id, order_ids=None, number=None, eligible_only=False):
    query = select(Order).where(Order.customer_id == customer_id, Order.payment_method == PaymentMethod.COD,
        Order.created_at >= catalog.turn_time - timedelta(days=catalog.settings.customer_cancellation_recent_days))
    if order_ids is not None:
        query = query.where(Order.id.in_(order_ids))
    if number is not None:
        query = query.where(Order.order_number == number)
    if eligible_only:
        query = query.where(Order.status.in_(catalog.settings.customer_cancellation_statuses),
                            Order.payment_status == PaymentStatus.PENDING)
    return list(db.scalars(query.options(selectinload(Order.items)).order_by(Order.created_at.desc(), Order.id.desc()).limit(4)))


def latest_cancellation(db, catalog):
    from app.models.enums import MessageDirection, SenderType
    ordered = message_order_time()
    anchor = db.execute(select(ordered, Message.id).where(Message.id == catalog.target.inbound_id)).one()
    row = db.execute(select(Message.id, Message.metadata_["cancellation_state"]).where(
        Message.conversation_id == catalog.target.conversation_id,
        Message.direction == MessageDirection.INBOUND, Message.sender_type == SenderType.CUSTOMER,
        Message.metadata_.has_key("cancellation_state"),
        tuple_(ordered, Message.id) <= tuple_(anchor[0], anchor[1]),
    ).order_by(ordered.desc(), Message.id.desc()).limit(1)).first()
    return (PendingCancellation.model_validate_json(json.dumps(row[1])) if row[1] is not None else None, row[0]) if row else (None, None)


def selection_prompt(db, catalog, pending):
    """A persisted choosing checkpoint alone is not delivered selection authority."""
    from app.services.confirmation_context import active_prompt
    customer = customer_for(db, catalog)
    if (not pending or pending.status != "choosing" or pending.customer_id != customer.id
            or pending.conversation_id != catalog.target.conversation_id):
        return None
    now = db.scalar(select(message_order_time()).where(Message.id == catalog.target.inbound_id))
    lifetime = catalog.settings.customer_cancellation_confirmation_seconds
    if not 0 <= (now - pending.offered_at).total_seconds() <= lifetime:
        return None
    prompt = active_prompt(db, catalog, lifetime=lifetime)
    data = (prompt.metadata_.get("commerce_state", {}).get("cancellation") or {}) if prompt else {}
    if (data.get("id") != str(pending.id) or data.get("status") != "choosing"
            or data.get("action") != "order_cancellation"
            or data.get("customer_id") != str(customer.id)
            or data.get("conversation_id") != str(catalog.target.conversation_id)
            or data.get("order_ids") != [str(oid) for oid in pending.order_ids]
            or prompt.metadata_.get("in_reply_to") != str(pending.request_message_id)):
        return None
    return prompt


def authorized(db, catalog, pending):
    from app.services.checkout_service import answer
    from app.models.enums import MessageDirection, MessageType, SenderType
    origin = db.get(Message, catalog.target.inbound_id)
    customer = customer_for(db, catalog)
    return bool(pending and pending.status == "awaiting_confirmation" and len(pending.order_ids) == 1
        and pending.customer_id == customer.id and pending.conversation_id == catalog.target.conversation_id
        and origin.message_type == MessageType.TEXT and origin.direction == MessageDirection.INBOUND
        and origin.sender_type == SenderType.CUSTOMER and answer(origin.content or "") is True
        and 0 <= (catalog.turn_time - pending.offered_at).total_seconds() <= catalog.settings.customer_cancellation_confirmation_seconds
        and prompt_matches(db, catalog, pending.marker(), lifetime=catalog.settings.customer_cancellation_confirmation_seconds))


def restore_locked(db, order):
    """Only audited COD debits are reversible; never assume an order consumed stock."""
    movements = list(db.scalars(select(InventoryMovement).where(
        InventoryMovement.reference_type == "order", InventoryMovement.reference_id == str(order.id),
        InventoryMovement.reason.in_([DEBIT_REASON, RESTORE_REASON]))))
    debits, credits, quantities = defaultdict(int), defaultdict(int), defaultdict(int)
    for item in order.items:
        quantities[item.product_variant_id] += item.quantity
    for movement in movements:
        if movement.reason == DEBIT_REASON and movement.quantity_change < 0:
            debits[movement.product_variant_id] -= movement.quantity_change
        elif movement.reason == RESTORE_REASON and movement.quantity_change > 0:
            credits[movement.product_variant_id] += movement.quantity_change
        else:
            raise ValueError("cancellation_invalid_inventory_audit")
    if any(qty > quantities[vid] for vid, qty in debits.items()) or any(qty > debits[vid] for vid, qty in credits.items()):
        raise ValueError("cancellation_invalid_inventory_audit")
    restore = {vid: qty - credits[vid] for vid, qty in debits.items() if qty > credits[vid]}
    variants = {v.id: v for v in db.scalars(select(ProductVariant).where(ProductVariant.id.in_(restore))
        .order_by(ProductVariant.id).with_for_update().execution_options(populate_existing=True))}
    if len(variants) != len(restore):
        raise ValueError("cancellation_missing_inventory")
    for vid, quantity in restore.items():
        variant = variants[vid]
        if variant.stock_quantity + quantity > 2147483647:
            raise ValueError("cancellation_inventory_overflow")
        variant.stock_quantity += quantity
        db.add(InventoryMovement(product_variant_id=vid, quantity_change=quantity, reason=RESTORE_REASON,
            reference_type="order", reference_id=str(order.id)))


def finalize(turn, reply):
    pending = turn.state.cancellation
    dirty = getattr(turn, "cancellation_dirty", False)
    # Every unrelated processed turn supersedes an unfinished cancellation prompt,
    # including multimodal turns and failed outbound responses.
    if not dirty and pending and pending.status in ("choosing", "awaiting_confirmation"):
        pending.status = "superseded"
        dirty = True
    if not dirty:
        return reply
    from app.ai.order_cancellation import response
    from app.services.whatsapp_service import _superseded
    with turn.catalog.sessions() as db:
        with transaction(db):
            customer = customer_for(db, turn.catalog)
            db.execute(select(Conversation.id).where(Conversation.id == turn.catalog.target.conversation_id).with_for_update())
            if _superseded(db, turn.catalog.target):
                raise ValueError("superseded_cancellation")
            current, checkpoint = latest_cancellation(db, turn.catalog)
            if getattr(turn, "cancellation_confirming", False):
                # Retry after commit recovers the durable outcome without another reversal.
                if current and current.id == pending.id and current.status == "cancelled":
                    turn.state.cancellation = pending = current
                    reply = response(turn, "already", getattr(turn, "cancellation_order_number", ""))
                elif (checkpoint != getattr(turn.catalog, "cancellation_checkpoint", None)
                      or not current or current.id != pending.id or current.order_ids != pending.order_ids
                      or current.customer_id != pending.customer_id or current.conversation_id != pending.conversation_id
                      or current.status != "awaiting_confirmation" or not authorized(db, turn.catalog, pending)):
                    pending.status = "superseded"
                    reply = response(turn, "expired")
                else:
                    order = db.scalar(select(Order).where(Order.id == pending.order_ids[0], Order.customer_id == customer.id)
                        .with_for_update().execution_options(populate_existing=True))
                    if not order:
                        pending.status = "superseded"
                        reply = response(turn, "none")
                    else:
                        result = disposition(order, turn.catalog.settings)
                        if result == "eligible":
                            restore_locked(db, order)
                            order.status = OrderStatus.CANCELLED
                            pending.status = "cancelled"
                            pending.confirmation_message_id = turn.catalog.target.inbound_id
                            reply = response(turn, "success", order.order_number)
                        else:
                            pending.status = "cancelled" if result == "already" else "superseded"
                            reply = response(turn, result, order.order_number)
            elif checkpoint != getattr(turn.catalog, "cancellation_checkpoint", None):
                raise ValueError("stale_cancellation")
            inbound = db.get(Message, turn.catalog.target.inbound_id)
            inbound.metadata_ = dict(inbound.metadata_ or {}, cancellation_state=pending.model_dump(mode="json") if pending else None)
            db.flush()
    reply.commerce_state = turn.state.model_dump(mode="json")
    return reply
