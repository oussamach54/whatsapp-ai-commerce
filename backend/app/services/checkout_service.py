"""Durable checkout checkpoints and the deterministic COD transaction boundary."""
import json
import re
import unicodedata
from decimal import Decimal
from sqlalchemy import select, tuple_

from app.ai.checkout_state import Cart, CustomerValue
from app.ai.scope import normalize
from app.models import Conversation, Customer, Message, Order, Product, ProductVariant
from app.models.enums import MessageDirection, SenderType
from app.services.common import transaction
from app.services.conversation_service import message_order_time

AFFIRMATIVES = {"oui", "yes", "wakha", "ok", "okay", "confirme", "confirm", "nconfirmi", "wakha confirme",
                "iwa", "yallah", "je confirme", "i confirm", "نعم", "واخا", "نأكد", "ايه"}
DECLINES = {"non", "no", "لا"}
CANCELLATIONS = {"cancel", "annule", "ma b9itch baghi", "khliha", "forget it"}
LIMITS = {"customer_name": 255, "phone": 32, "city": 255, "address": 500, "delivery_note": 500, "postal_code": 32}


def answer(text):
    value = normalize(text).strip(" .!✅")
    return True if value in AFFIRMATIVES else False if value in DECLINES else None


def clean_field(name, value):
    value = " ".join("".join(c for c in value if not unicodedata.category(c).startswith("C") or c in "\n\t").split())
    if not value or len(value) > LIMITS[name]:
        raise ValueError("invalid_checkout_field")
    if name == "phone":
        value = re.sub(r"[ ()-]", "", value)
        if not re.fullmatch(r"\+?[0-9]{8,15}", value):
            raise ValueError("invalid_phone")
    return value


def latest_cart(db, catalog):
    ordered = message_order_time()
    anchor = db.execute(select(ordered, Message.id).where(Message.id == catalog.target.inbound_id)).one()
    row = db.execute(select(Message.id, Message.metadata_["checkout_state"]).where(
        Message.conversation_id == catalog.target.conversation_id,
        Message.direction == MessageDirection.INBOUND,
        Message.metadata_.has_key("checkout_state"),
        tuple_(ordered, Message.id) <= tuple_(anchor[0], anchor[1]),
    ).order_by(ordered.desc(), Message.id.desc()).limit(1)).first()
    return (Cart.model_validate_json(json.dumps(row[1])) if row[1] is not None else None, row[0]) if row else (None, None)


def load_cart(catalog):
    with catalog.sessions() as db:
        catalog.authorize(db)
        return latest_cart(db, catalog)


def known_fields(db, catalog, cart):
    customer = db.scalar(select(Customer).join(Conversation).where(Conversation.id == catalog.target.conversation_id))
    if not customer or customer.is_blocked:
        raise ValueError("customer_unavailable")
    # The customer identity was resolved from the authenticated channel webhook.
    if "phone" not in cart.fields:
        try:
            cart.fields["phone"] = CustomerValue(value=clean_field("phone", customer.phone_number), source="channel")
        except ValueError:
            pass
    if "customer_name" not in cart.fields and customer.first_name:
        cart.fields["customer_name"] = CustomerValue(
            value=clean_field("customer_name", " ".join(filter(None, (customer.first_name, customer.last_name)))), source="profile")
    return customer


def missing_fields(settings, cart):
    return [name for name in dict.fromkeys(settings.checkout_required_fields) if name not in cart.fields]


def totals(cart, settings):
    subtotal = sum((Decimal(line.unit_price) * line.quantity for line in cart.items), Decimal("0.00"))
    fee = settings.checkout_shipping_cost
    total = subtotal + fee if fee is not None else None
    if subtotal > Decimal("9999999999.99") or (total is not None and total > Decimal("9999999999.99")):
        raise ValueError("amount_overflow")
    cart.subtotal = format(subtotal, ".2f")
    cart.shipping_cost = format(fee, ".2f") if fee is not None else None
    cart.total = format(total, ".2f") if total is not None else None


def verify_locked(db, cart):
    """Lock in stable order, refresh ORM cache, and verify every item atomically."""
    parents = sorted({line.target.product_id for line in cart.items}, key=str)
    ids = sorted({line.target.variant_id for line in cart.items}, key=str)
    products = {p.id: p for p in db.scalars(select(Product).where(Product.id.in_(parents)).order_by(Product.id)
                .with_for_update().execution_options(populate_existing=True))}
    variants = {v.id: v for v in db.scalars(select(ProductVariant).where(ProductVariant.id.in_(ids)).order_by(ProductVariant.id)
                .with_for_update().execution_options(populate_existing=True))}
    if not cart.items or len(ids) != len(cart.items):
        raise ValueError("invalid_cart")
    changed = False
    for line in cart.items:
        p, v = products.get(line.target.product_id), variants.get(line.target.variant_id)
        if not p or not p.is_active or not v or not v.is_active or v.product_id != p.id:
            return "unavailable", line, variants
        line.stock = v.stock_quantity
        if not 1 <= line.quantity <= 99 or v.stock_quantity < line.quantity:
            return "stock", line, variants
        changed |= Decimal(line.unit_price) != v.price
        line.unit_price = format(v.price, ".2f")
        line.product_name, line.variant_name = p.name, v.name
    return "price" if changed else "valid", None, variants


def confirmation_authorized(db, catalog, cart):
    if cart.confirmed_version != cart.version or not cart.confirmation_message_id:
        return False
    origin = db.get(Message, cart.confirmation_message_id)
    if not origin or origin.conversation_id != catalog.target.conversation_id or origin.direction != MessageDirection.INBOUND or origin.sender_type != SenderType.CUSTOMER or answer(origin.content or "") is not True:
        return False
    ordered = message_order_time()
    anchor = db.scalar(select(ordered).where(Message.id == origin.id))
    if not 0 <= (anchor - cart.offered_at).total_seconds() <= 900:
        return False
    # A successful channel send with this exact version must predate consent.
    return bool(db.scalar(select(Message.id).where(
        Message.conversation_id == catalog.target.conversation_id,
        Message.direction == MessageDirection.OUTBOUND,
        Message.metadata_["provider"].astext == "whatsapp",
        Message.metadata_["commerce_state"]["cart"]["id"].astext == str(cart.id),
        Message.metadata_["commerce_state"]["cart"]["version"].astext == str(cart.version),
        Message.metadata_["commerce_state"]["cart"]["status"].astext == "awaiting_confirmation",
        Message.metadata_["turn_status"].astext == "current",
        Message.external_message_id.is_not(None), Message.external_message_id != "",
        ordered < anchor).limit(1)))


def recover_completed(turn):
    """Resolve errors after commit using durable state, never an in-memory claim."""
    from app.ai.checkout import phrase
    from app.ai.schemas import SalesReply
    with turn.catalog.sessions() as db:
        turn.catalog.authorize(db)
        cart, _ = latest_cart(db, turn.catalog)
        if cart is None or cart.status != "completed" or turn.state.cart is None or cart.id != turn.state.cart.id:
            return None
        order = db.scalar(select(Order).where(Order.source_cart_id == cart.id,
            Order.source_conversation_id == turn.catalog.target.conversation_id))
        if order is None:
            return None
        cart.order_id, cart.order_number = order.id, order.order_number
    turn.state.cart = cart
    return SalesReply(phrase(turn.style, "Votre commande est confirmée ✅", "Your order is confirmed ✅",
        "Commande dyalk t2ekkdat ✅", "الطلبية ديالك تأكدات ✅"),
        catalog_refs=turn.refs.model_dump(mode="json"), commerce_state=turn.state.model_dump(mode="json"))


def finalize(turn, reply):
    """Commit only after admission succeeds. Never hold a transaction over a send."""
    if not getattr(turn, "checkout_dirty", False):
        return reply
    from app.ai.checkout import render_cart, sync_purchase
    from app.services.whatsapp_service import _superseded
    from app.services.order_service import stage_cod_order
    cart = turn.state.cart
    result, problem = None, None
    with turn.catalog.sessions() as db:
        with transaction(db):
            turn.catalog.authorize(db)
            db.execute(select(Conversation.id).where(Conversation.id == turn.catalog.target.conversation_id).with_for_update())
            if _superseded(db, turn.catalog.target):
                raise ValueError("superseded_checkout")
            current, checkpoint = latest_cart(db, turn.catalog)
            if cart is None:
                # An explicit new journey retires active context, not its order.
                if checkpoint != getattr(turn, "checkout_checkpoint", None):
                    raise ValueError("stale_checkout")
                inbound = db.get(Message, turn.catalog.target.inbound_id)
                inbound.metadata_ = dict(inbound.metadata_ or {}, checkout_state=None)
                db.flush()
                return reply
            customer = known_fields(db, turn.catalog, cart)
            existing = db.scalar(select(Order).where(Order.source_cart_id == cart.id))
            if existing:
                if existing.customer_id != customer.id or existing.source_conversation_id != turn.catalog.target.conversation_id:
                    raise ValueError("order_scope_mismatch")
                # A concurrent retry may have loaded the pre-commit checkpoint.
                # Recover the committed cart before rejecting stale draft writes.
                if current and current.id == cart.id and current.status == "completed":
                    cart = turn.state.cart = current
                cart.status, cart.order_id, cart.order_number = "completed", existing.id, existing.order_number
                result = "completed"
            elif checkpoint != getattr(turn, "checkout_checkpoint", None):
                raise ValueError("stale_checkout")
            elif cart.status == "confirmed":
                if not confirmation_authorized(db, turn.catalog, cart):
                    cart.status, cart.confirmed_version, cart.confirmation_message_id = "awaiting_confirmation", None, None
                    result = "offer"
                else:
                    result, problem, variants = verify_locked(db, cart)
                    old_fee = cart.shipping_cost
                    totals(cart, turn.catalog.settings)
                    if result == "price" or (result == "valid" and old_fee != cart.shipping_cost):
                        cart.version += 1
                        cart.status, cart.confirmed_version, cart.confirmation_message_id = "awaiting_confirmation", None, None
                        cart.offered_at = turn.catalog.turn_time
                        result = "price"
                    elif result in ("stock", "unavailable"):
                        cart.status, cart.confirmed_version, cart.confirmation_message_id = "blocked", None, None
                    elif not missing_fields(turn.catalog.settings, cart):
                        turn.checkout_order_attempted = True
                        order = stage_cod_order(db, customer, cart, variants, turn.catalog)
                        cart.status, cart.order_id, cart.order_number = "completed", order.id, order.order_number
                        result = "completed"
                    else:
                        result = "missing"
            inbound = db.get(Message, turn.catalog.target.inbound_id)
            inbound.metadata_ = dict(inbound.metadata_ or {}, checkout_state=cart.model_dump(mode="json"),
                                     checkout_private=bool(getattr(turn, "checkout_private", False)))
            db.flush()
    sync_purchase(turn)
    reply.commerce_state = turn.state.model_dump(mode="json")
    if result:
        reply.text = render_cart(turn, result, problem)
    return reply
