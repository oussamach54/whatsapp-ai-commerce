import json
from unittest.mock import Mock
import pytest
from pydantic import ValidationError
from app.ai.catalog_tools import CatalogTools, TOOLS
from app.ai.catalog_schemas import SearchProducts, GetProducts
from app.services.catalog_service import CatalogError


@pytest.mark.parametrize("args", [{"limit": 6}, {"limit": True}, {"terms": ["x"] * 7},
    {"terms": ["x" * 65]}, {"terms": [""]}, {"sql": "select 1"}, {"max_price": "NaN"},
    {"max_price": "-1"}, {"max_price": 1}, {"in_stock_only": "true"}, {"size": 42}])
def test_invalid_arguments(args):
    service = Mock()
    with pytest.raises(ValidationError):
        CatalogTools(service).execute("search_products", json.dumps(args))
    service.search.assert_not_called()


def test_unknown_tool_and_strict_schema():
    with pytest.raises(CatalogError):
        CatalogTools(Mock()).execute("run_sql", "{}")
    with pytest.raises(ValidationError):
        GetProducts()
    assert {t["name"] for t in TOOLS} == {"search_products", "get_products"}
    for tool in TOOLS:
        assert tool["strict"] and not tool["parameters"]["additionalProperties"]
        assert set(tool["parameters"]["required"]) == set(tool["parameters"]["properties"])


def test_output_bound(catalog_env, catalog_product, db_session):
    service, _, _ = catalog_env
    for _ in range(5):
        p, v = catalog_product(name="n" * 255)
        p.description, p.brand, p.category = "d" * 1000, "b" * 255, "c" * 255
        v.name = "v" * 255
        for _ in range(2):
            _, other = catalog_product(name="x" * 255)
            other.product = p
    db_session.flush()
    tools = CatalogTools(service)
    result = tools.execute("search_products", SearchProducts(limit=5).model_dump_json())
    assert len(result) <= 8000
    assert len(tools.candidates) == len(json.loads(result)["products"])
