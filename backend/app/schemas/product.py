from datetime import datetime
from uuid import UUID
from pydantic import Field
from app.schemas.common import Money, Schema, TimestampRead, UpdateSchema

class ProductVariantCreate(Schema):
    sku: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    price: Money
    compare_at_price: Money | None = None
    stock_quantity: int = Field(default=0, ge=0, le=2147483647, strict=True)
    is_active: bool = True

class ProductVariantUpdate(UpdateSchema):
    required_fields = {"sku", "name", "price", "stock_quantity", "is_active"}
    sku: str | None = Field(default=None, min_length=1, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    price: Money | None = None
    compare_at_price: Money | None = None
    stock_quantity: int | None = Field(default=None, ge=0, le=2147483647, strict=True)
    is_active: bool | None = None

class ProductVariantRead(ProductVariantCreate, TimestampRead):
    product_id: UUID

class ProductImageCreate(Schema):
    variant_id: UUID | None = None
    url: str = Field(min_length=1)
    alt_text: str | None = Field(default=None, max_length=255)
    sort_order: int = Field(default=0, ge=-2147483648, le=2147483647)

class ProductImageRead(ProductImageCreate):
    id: UUID
    product_id: UUID
    created_at: datetime

class ProductCreate(Schema):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=255)
    description: str | None = None
    brand: str | None = Field(default=None, max_length=255)
    category: str | None = Field(default=None, max_length=255)
    is_active: bool = True

class ProductUpdate(UpdateSchema):
    required_fields = {"name", "slug", "is_active"}
    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    brand: str | None = Field(default=None, max_length=255)
    category: str | None = Field(default=None, max_length=255)
    is_active: bool | None = None

class ProductRead(ProductCreate, TimestampRead):
    images: list[ProductImageRead]
    variants: list[ProductVariantRead]
