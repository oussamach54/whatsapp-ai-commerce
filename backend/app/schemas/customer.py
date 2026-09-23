from pydantic import Field
from app.schemas.common import Schema, TimestampRead, UpdateSchema

class CustomerCreate(Schema):
    phone_number: str = Field(min_length=1, max_length=32)
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=320)
    preferred_language: str | None = Field(default=None, max_length=16)
    notes: str | None = None
    is_blocked: bool = False

class CustomerUpdate(UpdateSchema):
    required_fields = {"phone_number", "is_blocked"}
    phone_number: str | None = Field(default=None, min_length=1, max_length=32)
    first_name: str | None = Field(default=None, max_length=100)
    last_name: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=320)
    preferred_language: str | None = Field(default=None, max_length=16)
    notes: str | None = None
    is_blocked: bool | None = None

class CustomerRead(CustomerCreate, TimestampRead):
    pass
