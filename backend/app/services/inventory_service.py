from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import InventoryMovement, ProductVariant
from app.schemas.inventory import InventoryMovementCreate
from app.services.common import get, listing, transaction, ServiceError

def create_inventory_movement(db: Session, data: InventoryMovementCreate) -> InventoryMovement:
    with transaction(db):
        variant = db.scalar(select(ProductVariant).where(ProductVariant.id == data.product_variant_id).with_for_update().execution_options(populate_existing=True))
        if variant is None:
            raise ServiceError(404, "ProductVariant not found")
        stock = variant.stock_quantity + data.quantity_change
        if data.quantity_change == 0 or not 0 <= stock <= 2147483647:
            raise ServiceError(422, "Movement must be nonzero and leave stock within supported limits")
        variant.stock_quantity = stock
        obj = InventoryMovement(**data.model_dump())
        db.add(obj)
    return obj

def list_movements(db: Session, limit: int = 50, offset: int = 0) -> list[InventoryMovement]:
    return listing(db, InventoryMovement, limit, offset)

def get_variant_movement_history(db: Session, variant_id: UUID, limit: int = 50, offset: int = 0) -> list[InventoryMovement]:
    get(db, ProductVariant, variant_id)
    return listing(db, InventoryMovement, limit, offset, filters=(InventoryMovement.product_variant_id == variant_id,))
