"""Seed only the agreed WhatsApp development catalog. No external API calls."""
import argparse
import json

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Product, ProductVariant
from app.schemas.product import ProductCreate, ProductVariantCreate
from app.services import product_service


CATALOG = (
    (dict(name="Pantalon Classic", slug="test-pantalon-classic", category="Pantalon", brand="Test Fashion"), (
        dict(sku="TEST-PANT-NOIR-M", name="Noir / M", price="249.00", size="M", color="noir", stock_quantity=5),
        dict(sku="TEST-PANT-NOIR-L", name="Noir / L", price="249.00", size="L", color="noir", stock_quantity=0),
        dict(sku="TEST-PANT-BLEU-M", name="Bleu / M", price="229.00", size="M", color="bleu", stock_quantity=3),
    )),
    (dict(name="Parfum Élégance", slug="test-parfum-elegance", category="Parfum", brand="Test Beauty"), (
        dict(sku="TEST-PARFUM-001", name="Standard", price="299.00", size=None, color=None, stock_quantity=4),
    )),
    (dict(name="Sac Élégant", slug="test-sac-elegant", category="Accessoire", brand="Test Fashion"), (
        dict(sku="TEST-SAC-001", name="Standard", price="350.00", size=None, color="noir", stock_quantity=2),
    )),
)


class SeedConflict(ValueError):
    pass


def validate_environment(settings):
    if settings.environment != "development" or settings.catalog_currency != "MAD":
        raise SeedConflict("Seed requires ENVIRONMENT=development and CATALOG_CURRENCY=MAD")


def seed_catalog(db, settings):
    """Caller owns an outer transaction; service commits release only savepoints.

    Existing records must match; modified stock/prices are never silently reset.
    Preflight all identities before making any writes.
    """
    validate_environment(settings)
    db.execute(text("SELECT pg_advisory_xact_lock(609270006)"))
    prepared = [(ProductCreate(**p), [ProductVariantCreate(**v) for v in variants]) for p, variants in CATALOG]
    products = {p.slug: p for p in db.scalars(select(Product).where(
        Product.slug.in_([p.slug for p, _ in prepared])))}
    variants = {v.sku: v for v in db.scalars(select(ProductVariant).where(
        ProductVariant.sku.in_([v.sku for _, items in prepared for v in items])))}
    for data, items in prepared:
        product = products.get(data.slug)
        if product and any(getattr(product, field) != getattr(data, field)
                           for field in ("name", "category", "brand", "is_active")):
            raise SeedConflict(f"Conflicting product slug: {data.slug}; no records changed")
        for variant_data in items:
            variant = variants.get(variant_data.sku)
            if variant and (product is None or variant.product_id != product.id or any(
                    getattr(variant, field) != getattr(variant_data, field) for field in
                    ("name", "price", "size", "color", "stock_quantity", "is_active"))):
                raise SeedConflict(f"Conflicting variant SKU: {variant_data.sku}; no records changed")
    created_products = created_variants = 0
    for data, items in prepared:
        product = products.get(data.slug)
        if product is None:
            product = product_service.create_product(db, data)
            created_products += 1
        for variant_data in items:
            if variant_data.sku not in variants:
                product_service.create_variant(db, product.id, variant_data)
                created_variants += 1
    return {"created_products": created_products, "created_variants": created_variants}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--development", action="store_true", required=True,
                        help="Confirm this connection is the development database, never production")
    parser.parse_args()
    settings = get_settings()
    validate_environment(settings)
    engine = create_engine(settings.database_url)
    try:
        # Existing CRUD services commit individually. Savepoints retain one atomic
        # outer transaction, including the seed lock, until all records succeed.
        with engine.begin() as connection:
            with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
                result = seed_catalog(db, settings)
        print(json.dumps(result))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
