from uuid import UUID
from fastapi import APIRouter
from app.api.dependencies import Database, Limit, Offset
from app.schemas.order import OrderCreate, OrderRead, OrderStatusUpdate, PaymentStatusUpdate, UUID
from app.services import order_service as service

router = APIRouter(tags=["Orders"])

@router.post("/orders", response_model=OrderRead, status_code=201)
def create_order(db: Database, data: OrderCreate) -> OrderRead:
    return service.create_order(db, data)

@router.get("/orders", response_model=list[OrderRead], status_code=200)
def list_orders(db: Database, limit: Limit = 50, offset: Offset = 0) -> list[OrderRead]:
    return service.list_orders(db, limit, offset)

@router.get("/orders/{order_id}", response_model=OrderRead, status_code=200)
def get_order(db: Database, order_id: UUID) -> OrderRead:
    return service.get_order(db, order_id)

@router.patch("/orders/{order_id}/status", response_model=OrderRead, status_code=200)
def update_order_status(db: Database, order_id: UUID, data: OrderStatusUpdate) -> OrderRead:
    return service.update_order_status(db, order_id, data)

@router.patch("/orders/{order_id}/payment-status", response_model=OrderRead, status_code=200)
def update_payment_status(db: Database, order_id: UUID, data: PaymentStatusUpdate) -> OrderRead:
    return service.update_payment_status(db, order_id, data)
