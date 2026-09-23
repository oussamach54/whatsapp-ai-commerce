from uuid import UUID
from fastapi import APIRouter
from app.api.dependencies import Database, Limit, Offset
from app.schemas.product import ProductCreate, ProductImageCreate, ProductImageRead, ProductRead, ProductUpdate, ProductVariantCreate, ProductVariantRead, ProductVariantUpdate, UUID
from app.services import product_service as service

router = APIRouter(tags=["Products"])

@router.post("/products", response_model=ProductRead, status_code=201)
def create_product(db: Database, data: ProductCreate) -> ProductRead:
    return service.create_product(db, data)

@router.get("/products", response_model=list[ProductRead], status_code=200)
def list_products(db: Database, limit: Limit = 50, offset: Offset = 0) -> list[ProductRead]:
    return service.list_products(db, limit, offset)

@router.get("/products/{product_id}", response_model=ProductRead, status_code=200)
def get_product(db: Database, product_id: UUID) -> ProductRead:
    return service.get_product(db, product_id)

@router.patch("/products/{product_id}", response_model=ProductRead, status_code=200)
def update_product(db: Database, product_id: UUID, data: ProductUpdate) -> ProductRead:
    return service.update_product(db, product_id, data)

@router.post("/products/{product_id}/variants", response_model=ProductVariantRead, status_code=201)
def create_variant(db: Database, product_id: UUID, data: ProductVariantCreate) -> ProductVariantRead:
    return service.create_variant(db, product_id, data)

@router.get("/products/{product_id}/variants", response_model=list[ProductVariantRead], status_code=200)
def list_product_variants(db: Database, product_id: UUID, limit: Limit = 50, offset: Offset = 0) -> list[ProductVariantRead]:
    return service.list_product_variants(db, product_id, limit, offset)

@router.get("/variants/{variant_id}", response_model=ProductVariantRead, status_code=200)
def get_variant(db: Database, variant_id: UUID) -> ProductVariantRead:
    return service.get_variant(db, variant_id)

@router.patch("/variants/{variant_id}", response_model=ProductVariantRead, status_code=200)
def update_variant(db: Database, variant_id: UUID, data: ProductVariantUpdate) -> ProductVariantRead:
    return service.update_variant(db, variant_id, data)

@router.post("/products/{product_id}/images", response_model=ProductImageRead, status_code=201)
def create_product_image(db: Database, product_id: UUID, data: ProductImageCreate) -> ProductImageRead:
    return service.create_product_image(db, product_id, data)
