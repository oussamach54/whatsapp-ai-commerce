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
        apply_update(get_order(db, order_id), data)
    return get_order(db, order_id)

def update_payment_status(db: Session, order_id: UUID, data: PaymentStatusUpdate) -> Order:
    with transaction(db):
        apply_update(get_order(db, order_id), data)
    return get_order(db, order_id)
