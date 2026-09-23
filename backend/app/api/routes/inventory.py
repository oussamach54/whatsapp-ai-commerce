from uuid import UUID
from fastapi import APIRouter
from app.api.dependencies import Database, Limit, Offset
from app.schemas.inventory import InventoryMovementCreate, InventoryMovementRead, UUID
from app.services import inventory_service as service

router = APIRouter(tags=["Inventory"])

@router.post("/inventory/movements", response_model=InventoryMovementRead, status_code=201)
def create_inventory_movement(db: Database, data: InventoryMovementCreate) -> InventoryMovementRead:
    return service.create_inventory_movement(db, data)

@router.get("/inventory/movements", response_model=list[InventoryMovementRead], status_code=200)
def list_movements(db: Database, limit: Limit = 50, offset: Offset = 0) -> list[InventoryMovementRead]:
    return service.list_movements(db, limit, offset)

@router.get("/inventory/variants/{variant_id}/movements", response_model=list[InventoryMovementRead], status_code=200)
def get_variant_movement_history(db: Database, variant_id: UUID, limit: Limit = 50, offset: Offset = 0) -> list[InventoryMovementRead]:
    return service.get_variant_movement_history(db, variant_id, limit, offset)
