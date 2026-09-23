from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Customer
from app.schemas.customer import CustomerCreate, CustomerUpdate
from app.services.common import get, listing, transaction, apply_update

def get_customer(db: Session, customer_id: UUID) -> Customer:
    return get(db, Customer, customer_id)

def get_customer_by_phone(db: Session, phone_number: str) -> Customer | None:
    return db.scalar(select(Customer).where(Customer.phone_number == phone_number))

def list_customers(db: Session, limit: int = 50, offset: int = 0) -> list[Customer]:
    return listing(db, Customer, limit, offset)

def create_customer(db: Session, data: CustomerCreate) -> Customer:
    with transaction(db):
        obj = Customer(**data.model_dump())
        db.add(obj)
    return obj

def update_customer(db: Session, customer_id: UUID, data: CustomerUpdate) -> Customer:
    with transaction(db):
        obj = get_customer(db, customer_id)
        apply_update(obj, data)
    return obj


def resolve_customer_for_update(db: Session, phone_number: str) -> Customer:
    """Resolve and lock a customer within the caller's transaction; no commit."""
    from sqlalchemy.dialects.postgresql import insert

    customer = get_customer_by_phone(db, phone_number)
    if customer is None:
        db.execute(insert(Customer).values(phone_number=phone_number).on_conflict_do_nothing(index_elements=[Customer.phone_number]))
    return db.scalars(select(Customer).where(Customer.phone_number == phone_number).with_for_update()).one()
