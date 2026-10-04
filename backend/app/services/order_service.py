from decimal import Decimal
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, joinedload
from app.models import Customer, Order, OrderItem, ProductVariant
from app.models.enums import OrderStatus
from app.schemas.order import OrderCreate, OrderStatusUpdate, PaymentStatusUpdate
from app.services.common import get, listing, transaction, apply_update, ServiceError

ORDER_LOAD = (selectinload(Order.customer), selectinload(Order.items))
MAX_MONEY = Decimal("9999999999.99")

def get_order(db: Session, order_id: UUID) -> Order:
    return get(db, Order, order_id, ORDER_LOAD)

def list_orders(db: Session, limit: int = 50, offset: int = 0) -> list[Order]:
    return listing(db, Order, limit, offset, ORDER_LOAD)

def create_order(db: Session, data: OrderCreate) -> Order:
    with transaction(db):
        get(db, Customer, data.customer_id)
        ids = {item.product_variant_id for item in data.items}
        variants = {v.id: v for v in db.scalars(select(ProductVariant).where(ProductVariant.id.in_(ids)).options(joinedload(ProductVariant.product)))}
        if ids != variants.keys():
            raise ServiceError(404, "ProductVariant not found")
        items = []
        subtotal = Decimal("0.00")
        for item in data.items:
            if item.quantity <= 0:
                raise ServiceError(422, "Quantity must be positive")
            variant = variants[item.product_variant_id]
            line_total = variant.price * item.quantity
            subtotal += line_total
            items.append(OrderItem(product_variant_id=variant.id, quantity=item.quantity,
                product_name_snapshot=variant.product.name, variant_name_snapshot=variant.name,
                sku_snapshot=variant.sku, unit_price=variant.price, line_total=line_total))
        total = subtotal + data.shipping_cost
        if total > MAX_MONEY:
            raise ServiceError(422, "Order total exceeds supported amount")
        obj = Order(**data.model_dump(exclude={"items"}), status=OrderStatus.PENDING,
                    items=items, subtotal=subtotal, total=total)
        db.add(obj)
        db.flush()  # PostgreSQL supplies order_number from the existing sequence.
        resource_id = obj.id
    return get_order(db, resource_id)

def update_order_status(db: Session, order_id: UUID, data: OrderStatusUpdate) -> Order:
    with transaction(db):
        order = db.scalar(select(Order).where(Order.id == order_id).with_for_update()
                          .execution_options(populate_existing=True))
        if order is None:
            raise ServiceError(404, "Order not found")
        if order.status == OrderStatus.CANCELLED and data.status != OrderStatus.CANCELLED:
            raise ServiceError(409, "A cancelled order cannot reenter fulfillment")
        apply_update(order, data)
    return get_order(db, order_id)

def update_payment_status(db: Session, order_id: UUID, data: PaymentStatusUpdate) -> Order:
    with transaction(db):
        apply_update(get_order(db, order_id), data)
    return get_order(db, order_id)


def stage_cod_order(db, customer, cart, variants, catalog):
    """Internal stage in checkout's authorized, locked transaction. No LLM tool."""
    from app.models import InventoryMovement
    from app.models.enums import PaymentMethod, PaymentStatus
    from app.services.checkout_service import missing_fields, confirmation_authorized
    if cart.status != "confirmed" or missing_fields(catalog.settings, cart) or not confirmation_authorized(db, catalog, cart):
        raise ServiceError(422, "Checkout not authorized")
    fields = {name: value.value for name, value in cart.fields.items()}
    order = Order(customer_id=customer.id, status=OrderStatus.CONFIRMED,
        payment_method=PaymentMethod.COD, payment_status=PaymentStatus.PENDING,
        currency=catalog.settings.catalog_currency, subtotal=Decimal(cart.subtotal),
        shipping_cost=Decimal(cart.shipping_cost) if cart.shipping_cost is not None else None,
        total=Decimal(cart.total) if cart.total is not None else None,
        shipping_full_name=fields.get("customer_name"), shipping_phone_number=fields.get("phone", customer.phone_number),
        shipping_city=fields.get("city"), shipping_address_line=fields.get("address"),
        shipping_postal_code=fields.get("postal_code"), shipping_country=catalog.settings.checkout_country,
        customer_notes=fields.get("delivery_note"), source_cart_id=cart.id,
        source_conversation_id=catalog.target.conversation_id, source_message_id=cart.confirmation_message_id)
    db.add(order)
    db.flush()
    for line in cart.items:
        variant = variants[line.target.variant_id]
        if variant.stock_quantity < line.quantity or variant.price != Decimal(line.unit_price):
            raise ServiceError(409, "Catalog changed")
        db.add(OrderItem(order_id=order.id, product_variant_id=variant.id, quantity=line.quantity,
            product_name_snapshot=line.product_name, variant_name_snapshot=line.variant_name,
            sku_snapshot=variant.sku, unit_price=variant.price, line_total=variant.price * line.quantity))
        variant.stock_quantity -= line.quantity
        db.add(InventoryMovement(product_variant_id=variant.id, quantity_change=-line.quantity,
            reason="cod_order_created", reference_type="order", reference_id=str(order.id)))
    db.flush()
    return order
