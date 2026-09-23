from uuid import UUID
from fastapi import APIRouter
from app.api.dependencies import Database, Limit, Offset
from app.schemas.customer import CustomerCreate, CustomerRead, CustomerUpdate
from app.services import customer_service as service

router = APIRouter(tags=["Customers"])

@router.post("/customers", response_model=CustomerRead, status_code=201)
def create_customer(db: Database, data: CustomerCreate) -> CustomerRead:
    return service.create_customer(db, data)

@router.get("/customers", response_model=list[CustomerRead], status_code=200)
def list_customers(db: Database, limit: Limit = 50, offset: Offset = 0) -> list[CustomerRead]:
    return service.list_customers(db, limit, offset)

@router.get("/customers/{customer_id}", response_model=CustomerRead, status_code=200)
def get_customer(db: Database, customer_id: UUID) -> CustomerRead:
    return service.get_customer(db, customer_id)

@router.patch("/customers/{customer_id}", response_model=CustomerRead, status_code=200)
def update_customer(db: Database, customer_id: UUID, data: CustomerUpdate) -> CustomerRead:
    return service.update_customer(db, customer_id, data)
