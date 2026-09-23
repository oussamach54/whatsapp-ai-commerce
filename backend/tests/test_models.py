from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.conversation import Conversation, Message
from app.models.customer import Customer
from app.models.enums import (
    ConversationChannel,
    ConversationStatus,
    MessageDirection,
    MessageType,
    OrderStatus,
    PaymentMethod,
    PaymentStatus,
    SenderType,
)
from app.models.order import Order, OrderItem
from app.models.product import Product, ProductVariant


def make_customer(phone_number: str = "+212600000001") -> Customer:
    return Customer(phone_number=phone_number)


def make_variant(sku: str = "SKU-001") -> ProductVariant:
    product = Product(name="Test product", slug=f"test-product-{sku.lower()}")
    return ProductVariant(product=product, sku=sku, name="Default", price=Decimal("99.90"), stock_quantity=10)


def test_customer_creation(db_session) -> None:
    customer = make_customer()
    db_session.add(customer)
    db_session.flush()

    assert customer.id is not None


def test_product_variant_creation_and_decimal_price(db_session) -> None:
    variant = make_variant()
    db_session.add(variant)
    db_session.flush()
    db_session.refresh(variant)

    assert variant.product.variants == [variant]
    assert variant.price == Decimal("99.90")


def test_order_item_relationship_uses_snapshots(db_session) -> None:
    customer = make_customer()
    variant = make_variant()
    order = Order(
        customer=customer,
        status=OrderStatus.PENDING,
        payment_method=PaymentMethod.COD,
        payment_status=PaymentStatus.PENDING,
        currency="MAD",
        subtotal=Decimal("99.90"),
        shipping_cost=Decimal("0.00"),
        total=Decimal("99.90"),
        shipping_full_name="Test Customer",
        shipping_phone_number=customer.phone_number,
        shipping_address_line="1 Test Street",
        shipping_city="Casablanca",
        shipping_country="MA",
        items=[OrderItem(product_variant=variant, product_name_snapshot="Test product", variant_name_snapshot="Default", sku_snapshot=variant.sku, unit_price=Decimal("99.90"), quantity=1, line_total=Decimal("99.90"))],
    )
    db_session.add(order)
    db_session.flush()

    assert order.order_number.startswith("ORD-")
    assert order.items[0].product_variant is variant


def test_conversation_messages_relationship(db_session) -> None:
    customer = make_customer()
    conversation = Conversation(customer=customer, channel=ConversationChannel.WHATSAPP, status=ConversationStatus.ACTIVE)
    conversation.messages.append(Message(direction=MessageDirection.INBOUND, sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT, content="Hello"))
    db_session.add(conversation)
    db_session.flush()

    assert conversation.messages[0].conversation is conversation


@pytest.mark.parametrize(("field", "first", "second"), [("phone", "+212600000002", "+212600000002"), ("sku", "SKU-UNIQUE", "SKU-UNIQUE")])
def test_unique_constraints(db_session, field: str, first: str, second: str) -> None:
    if field == "phone":
        db_session.add(make_customer(first))
        db_session.flush()
        db_session.add(make_customer(second))
    else:
        db_session.add(make_variant(first))
        db_session.flush()
        db_session.add(make_variant(second))

    with pytest.raises(IntegrityError):
        db_session.flush()
