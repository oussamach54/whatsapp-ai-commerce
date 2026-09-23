from uuid import UUID
from sqlalchemy.orm import Session, selectinload
from app.models import Product, ProductVariant, ProductImage
from app.schemas.product import ProductCreate, ProductUpdate, ProductVariantCreate, ProductVariantUpdate, ProductImageCreate
from app.services.common import get, listing, transaction, apply_update, ServiceError

PRODUCT_LOAD = (selectinload(Product.images), selectinload(Product.variants))

def get_product(db: Session, product_id: UUID) -> Product:
    return get(db, Product, product_id, PRODUCT_LOAD)

def list_products(db: Session, limit: int = 50, offset: int = 0) -> list[Product]:
    return listing(db, Product, limit, offset, PRODUCT_LOAD)

def create_product(db: Session, data: ProductCreate) -> Product:
    with transaction(db):
        obj = Product(**data.model_dump())
        db.add(obj)
        db.flush()
        resource_id = obj.id
    return get_product(db, resource_id)

def update_product(db: Session, product_id: UUID, data: ProductUpdate) -> Product:
    with transaction(db):
        apply_update(get_product(db, product_id), data)
    return get_product(db, product_id)

def get_variant(db: Session, variant_id: UUID) -> ProductVariant:
    return get(db, ProductVariant, variant_id)

def list_product_variants(db: Session, product_id: UUID, limit: int = 50, offset: int = 0) -> list[ProductVariant]:
    get(db, Product, product_id)
    return listing(db, ProductVariant, limit, offset, filters=(ProductVariant.product_id == product_id,))

def create_variant(db: Session, product_id: UUID, data: ProductVariantCreate) -> ProductVariant:
    with transaction(db):
        get(db, Product, product_id)
        obj = ProductVariant(product_id=product_id, **data.model_dump())
        db.add(obj)
    return obj

def update_variant(db: Session, variant_id: UUID, data: ProductVariantUpdate) -> ProductVariant:
    with transaction(db):
        obj = get_variant(db, variant_id)
        apply_update(obj, data)
    return obj

def create_product_image(db: Session, product_id: UUID, data: ProductImageCreate) -> ProductImage:
    with transaction(db):
        get(db, Product, product_id)
        if data.variant_id is not None and get_variant(db, data.variant_id).product_id != product_id:
            raise ServiceError(422, "Image variant must belong to the product")
        obj = ProductImage(product_id=product_id, **data.model_dump())
        db.add(obj)
    return obj
