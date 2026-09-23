from datetime import datetime
from decimal import Decimal
from typing import Annotated, ClassVar, Self
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator

Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2, allow_inf_nan=False)]
Quantity = Annotated[int, Field(strict=True, gt=0, le=2147483647)]

class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

class TimestampRead(Schema):
    id: UUID
    created_at: datetime
    updated_at: datetime

class UpdateSchema(Schema):
    @model_validator(mode="after")
    def reject_null_required(self) -> Self:
        for name in self.model_fields_set:
            if getattr(self, name) is None and name in self.required_fields:
                raise ValueError(f"{name} cannot be null")
        return self
    required_fields: ClassVar[set[str]] = set()
