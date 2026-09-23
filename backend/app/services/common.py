from contextlib import contextmanager
from collections.abc import Iterator, Sequence
from pydantic import BaseModel
from sqlalchemy.orm.interfaces import ORMOption
from sqlalchemy.sql.elements import ColumnElement
from typing import TypeVar
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, DataError
from sqlalchemy.orm import Session
from app.db.base import Base

T = TypeVar("T", bound=Base)

class ServiceError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)

@contextmanager
def transaction(db: Session) -> Iterator[None]:
    """The public write operation owns one commit and rolls back every failure."""
    try:
        yield
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        code = getattr(exc.orig, "sqlstate", None)
        if code == "23505":
            raise ServiceError(409, "A record with this unique value already exists") from exc
        raise ServiceError(422, "Operation violates a database constraint") from exc
    except DataError as exc:
        db.rollback()
        raise ServiceError(422, "Value exceeds database limits") from exc
    except Exception:
        db.rollback()
        raise

def get(db: Session, model: type[T], resource_id: UUID, options: Sequence[ORMOption] = ()) -> T:
    obj = db.scalar(select(model).where(model.id == resource_id).options(*options))
    if obj is None:
        raise ServiceError(404, f"{model.__name__} not found")
    return obj

def listing(db: Session, model: type[T], limit: int = 50, offset: int = 0, options: Sequence[ORMOption] = (), filters: Sequence[ColumnElement[bool]] = ()) -> list[T]:
    return list(db.scalars(select(model).where(*filters).options(*options).order_by(model.created_at, model.id).limit(limit).offset(offset)))

def apply_update(obj: T, data: BaseModel) -> None:
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(obj, key, value)
