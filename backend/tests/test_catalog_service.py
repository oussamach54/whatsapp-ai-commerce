from decimal import Decimal
from uuid import uuid4
import pytest
from app.ai.catalog_schemas import SearchProducts, GetProducts
from app.services.catalog_service import CatalogError


def test_discovery_search_and_empty(catalog_env, catalog_product):
    service, _, _ = catalog_env
    assert service.search(SearchProducts()).status == "not_found"
    p, v = catalog_product()
    result = service.search(SearchProducts(terms=["pantalon"]))
    assert result.products[0].id == p.id
    assert result.products[0].variants[0].id == v.id
    assert result.currency == "MAD"
    assert service.search(SearchProducts(terms=["nonexistent"])).status == "not_found"
    assert service.get(GetProducts(product_ids=[uuid4()])).status == "not_found"


@pytest.mark.parametrize("field", ["product", "variant"])
def test_inactive_excluded(db_session, catalog_env, catalog_product, field):
    service, _, _ = catalog_env
    p, v = catalog_product()
    (p if field == "product" else v).is_active = False
    db_session.flush()
    assert not service.search(SearchProducts()).products
    assert not service.get(GetProducts(variant_ids=[v.id])).products


def test_budget_same_variant_and_boundaries(db_session, catalog_env, catalog_product):
    service, _, _ = catalog_env
    p, cheap = catalog_product(price=Decimal("99.99"), stock_quantity=0)
    _, expensive = catalog_product(price=Decimal("300.01"))
    expensive.product = p
    db_session.flush()
    assert not service.search(SearchProducts(max_price="300.00")).products
    expensive.price = Decimal("300.00")
    db_session.flush()
    result = service.search(SearchProducts(max_price="300.00"))
    assert [v.id for v in result.products[0].variants] == [expensive.id]


@pytest.mark.parametrize("size,color,found", [("m", None, True), (None, " NOIR ", True),
    ("M", "noir", True), ("L", "noir", False), ("M", "blanc", False)])
def test_structured_filters(catalog_env, catalog_product, size, color, found):
    service, _, _ = catalog_env
    catalog_product()
    assert bool(service.search(SearchProducts(size=size, color=color)).products) == found


def test_unknown_attributes_and_stock(db_session, catalog_env, catalog_product):
    service, _, _ = catalog_env
    p, v = catalog_product(size=None, color=None, stock_quantity=0)
    assert not service.search(SearchProducts(size="M", in_stock_only=False)).products
    result = service.get(GetProducts(variant_ids=[v.id]))
    dto = result.products[0].variants[0]
    assert dto.size is None and dto.color is None and dto.availability == "out_of_stock"
    v.stock_quantity, v.price = 7, Decimal("222.22")
    db_session.flush()
    dto = service.get(GetProducts(variant_ids=[v.id])).products[0].variants[0]
    assert dto.price == "222.22" and dto.stock_quantity == 7 and dto.availability == "in_stock"
    assert not service.get(GetProducts(variant_ids=[uuid4()])).products


def test_bounds_literal_search_and_parent(catalog_env, catalog_product, db_session):
    service, _, _ = catalog_env
    p, v = catalog_product()
    for _ in range(7):
        _, other = catalog_product()
        other.product = p
    for _ in range(6):
        catalog_product()
    p.description = "x" * 10000
    db_session.flush()
    result = service.search(SearchProducts(limit=5))
    assert len(result.products) == 5 and result.has_more
    assert all(len(p.variants) <= 3 and len(p.description or "") <= 200 for p in result.products)
    detail = service.get(GetProducts(product_ids=[p.id]))
    assert len(detail.products[0].variants) == 6 and detail.products[0].has_more_variants
    assert len(detail.products[0].description) == 600
    for term in ["%", "_", "' OR 1=1 --"]:
        assert not service.search(SearchProducts(terms=[term])).products
    other_p, other_v = catalog_product()
    with pytest.raises(CatalogError):
        service.get(GetProducts(product_ids=[p.id], variant_ids=[other_v.id]))


@pytest.mark.parametrize("phone", [None, "wrong"])
def test_scope_fail_closed(catalog_env, phone):
    service, _, _ = catalog_env
    service.settings.whatsapp_phone_number_id = phone
    with pytest.raises(CatalogError):
        service.search(SearchProducts())


def test_normalization_admin_and_null(client, product):
    path = f"/api/products/{product['id']}/variants"
    result = client.post(path, json={"sku": uuid4().hex, "name": "Keep Name", "price": "1.00",
                                    "size": "  ｍ  ", "color": "  BLEU   Marine  "})
    assert result.status_code == 201
    data = result.json()
    assert (data["size"], data["color"], data["name"]) == ("M", "bleu marine", "Keep Name")
    updated = client.patch(f"/api/variants/{data['id']}", json={"size": " ", "color": None}).json()
    assert updated["size"] is None and updated["color"] is None
    assert client.patch(f"/api/variants/{data['id']}", json={"size": "M\u0000"}).status_code == 422


@pytest.mark.parametrize("value", ["", "mad", "EUR", "USD"])
def test_currency_validation(catalog_env, value):
    from pydantic import ValidationError
    from app.core.config import Settings
    with pytest.raises(ValidationError):
        Settings.model_validate(catalog_env[0].settings.model_dump() | {"catalog_currency": value})


def test_color_alias_and_literal_sku_rank(catalog_env, catalog_product, db_session):
    from app.schemas.product import ProductVariantUpdate
    assert ProductVariantUpdate(color=" BLACK ").color == "noir"
    assert ProductVariantUpdate(color="كحل").color == "noir"
    service, _, _ = catalog_env
    first, _ = catalog_product(name="Needle")
    second, v = catalog_product(name="Other")
    v.sku = "needle"
    db_session.flush()
    result = service.search(SearchProducts(terms=["needle"]))
    assert result.products[0].id == second.id
