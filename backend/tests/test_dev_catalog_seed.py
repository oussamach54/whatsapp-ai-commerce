from decimal import Decimal
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.models import Product, ProductVariant
from scripts.seed_dev_catalog import seed_catalog, SeedConflict


def test_seed_exact_idempotent_and_unrelated_untouched(db_session, catalog_env, catalog_product):
    unrelated, variant = catalog_product(name="Unrelated")
    settings = catalog_env[0].settings
    assert seed_catalog(db_session, settings) == {"created_products": 3, "created_variants": 5}
    first = db_session.execute(select(ProductVariant.id, ProductVariant.sku).where(
        ProductVariant.sku.like("TEST-%"))).all()
    assert seed_catalog(db_session, settings) == {"created_products": 0, "created_variants": 0}
    assert set(first) == set(db_session.execute(select(ProductVariant.id, ProductVariant.sku).where(
        ProductVariant.sku.like("TEST-%"))).all())
    noir_m = db_session.scalar(select(ProductVariant).where(ProductVariant.sku == "TEST-PANT-NOIR-M"))
    assert (noir_m.price, noir_m.size, noir_m.color, noir_m.stock_quantity, noir_m.is_active) == (
        Decimal("249.00"), "M", "noir", 5, True)
    assert db_session.get(Product, unrelated.id).name == "Unrelated"
    assert db_session.get(ProductVariant, variant.id).stock_quantity == 4


def test_collision_preflight_does_not_write(db_session, catalog_env, catalog_product):
    p, v = catalog_product()
    v.sku = "TEST-SAC-001"
    db_session.flush()
    with pytest.raises(SeedConflict):
        seed_catalog(db_session, catalog_env[0].settings)
    assert db_session.scalar(select(Product).where(Product.slug == "test-pantalon-classic")) is None
    assert v.product_id == p.id and v.price == Decimal("100.00")


def test_existing_seed_changes_are_not_reset(db_session, catalog_env):
    settings = catalog_env[0].settings
    seed_catalog(db_session, settings)
    v = db_session.scalar(select(ProductVariant).where(ProductVariant.sku == "TEST-PANT-NOIR-M"))
    v.stock_quantity = 1
    db_session.flush()
    with pytest.raises(SeedConflict):
        seed_catalog(db_session, settings)
    assert v.stock_quantity == 1


@pytest.mark.parametrize("environment", ["production", "staging", ""])
def test_non_development_refused_before_db_access(catalog_env, environment):
    db = Mock()
    settings = catalog_env[0].settings.model_copy(update={"environment": environment})
    with pytest.raises(SeedConflict):
        seed_catalog(db, settings)
    db.execute.assert_not_called()
