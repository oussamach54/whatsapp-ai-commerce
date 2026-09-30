import json
from decimal import Decimal
from unittest.mock import Mock
from uuid import uuid4
import pytest
from app.ai.catalog_orchestrator import run_catalog
from app.ai.catalog_schemas import SearchProducts, GetProducts, ResponsePlan, ProductRef, CatalogRefs
from app.ai.schemas import ProviderResult, AIHistoryMessage
from app.ai.service import AIService
from tests.test_catalog_references import presentation


def plan(p=None, v=None, action="show", reference="none"):
    return ProviderResult(ResponsePlan(action=action,
        items=[ProductRef(product_id=p.id, variant_id=v.id if v else None)] if p else [],
        question="product" if action == "clarify" else None, reference=reference).model_dump_json(),
        {"input_tokens": 10, "output_tokens": 20, "cached_input_tokens": 0})


def tool(name="search_products", args=None, call_id="call1"):
    return ProviderResult("", {"input_tokens": 5, "output_tokens": 10}, items=[
        {"type": "reasoning", "id": "rs_test", "summary": []},
        {"type": "function_call", "name": name, "arguments": args or SearchProducts().model_dump_json(), "call_id": call_id}])


def setup(catalog_env, outputs):
    catalog, _, active = catalog_env
    client = Mock()
    def respond(*args, **kwargs):
        assert not active, "DB session open during OpenAI"
        assert kwargs["parallel_tool_calls"] is False
        assert len(kwargs["tools"]) == 2
        result = outputs.pop(0)
        if isinstance(result, Exception):
            raise result
        return result() if callable(result) else result
    client.respond.side_effect = respond
    service = AIService(client, settings=catalog.settings, catalog_enabled=True)
    admission = Mock()
    admission.reserve.return_value = "allowed"
    admission.safe_record.return_value = True
    return service, admission, catalog


def test_gift_current_facts_and_call_accounting(catalog_env, catalog_product, db_session):
    p, v = catalog_product()
    def final():
        v.price = Decimal("230.00")
        db_session.flush()
        return plan(p)
    service, admission, catalog = setup(catalog_env, [tool(), final])
    reply = run_catalog(service, "3ndi 300dh bghet cadeau l mama", [AIHistoryMessage(role="assistant", content="Price 1 MAD stock 999")],
                        "darija_latin", admission, catalog)
    assert "230.00 MAD" in reply.text and "999" not in reply.text
    assert [c.args[0] for c in admission.reserve.call_args_list] == ["generation", "generation_2"]
    messages = service._client.respond.call_args.args[0]
    assert any(x.get("type") == "reasoning" for x in messages)
    assert any(x.get("type") == "function_call_output" and x["call_id"] == "call1" for x in messages)
    assert reply.catalog_refs["presented"][0]["product_id"] == str(p.id)


def test_explicit_budget_cannot_be_relaxed(catalog_env, catalog_product):
    p, _ = catalog_product(price=Decimal("301.00"))
    service, admission, catalog = setup(catalog_env, [tool(args=SearchProducts(max_price="999").model_dump_json()),
                                                   plan(action="no_matches")])
    reply = run_catalog(service, "budget 300dh", [], "french", admission, catalog)
    assert "pas trouvé" in reply.text and not reply.catalog_refs["presented"]


def test_second_reference_current_stock(db_session, catalog_env, catalog_product):
    catalog, inbound, _ = catalog_env
    p1, _ = catalog_product(name="Premier")
    p2, v2 = catalog_product(name="Deuxième", stock_quantity=0)
    refs = CatalogRefs(presented=[ProductRef(product_id=p1.id), ProductRef(product_id=p2.id)])
    source = presentation(db_session, inbound, refs)
    service, admission, catalog = setup(catalog_env, [plan(p2, action="select", reference="second")])
    reply = run_catalog(service, "je prends le deuxième", [], "french", admission, catalog)
    assert "épuisé" in reply.text and "Premier" not in reply.text
    assert reply.catalog_refs["selection"]["variant_id"] is None
    assert reply.catalog_refs["selection"]["source_message_id"] == str(source.id)


def test_focus_refresh_and_missing_reference(db_session, catalog_env, catalog_product):
    catalog, inbound, _ = catalog_env
    p, v = catalog_product(price=Decimal("55.00"))
    service, admission, catalog = setup(catalog_env, [])
    reply = run_catalog(service, "ch7al taman dyalo?", [], "darija_latin", admission, catalog)
    assert "kat9sed" in reply.text
    service._client.respond.assert_not_called()
    presentation(db_session, inbound, CatalogRefs(presented=[ProductRef(product_id=p.id)], focus=ProductRef(product_id=p.id)))
    service, admission, catalog = setup(catalog_env, [plan(p, reference="focus")])
    reply = run_catalog(service, "ch7al taman dyalo?", [], "darija_latin", admission, catalog)
    assert "55.00 MAD" in reply.text


@pytest.mark.parametrize("outputs", [[RuntimeError("secret")], [ProviderResult("not json")],
    [tool("run_sql")], [tool(args='{"sql":"SELECT 1"}')],
    [tool(), tool(call_id="call2"), tool(call_id="call3")]])
def test_failures_safe(catalog_env, outputs):
    service, admission, catalog = setup(catalog_env, outputs.copy())
    reply = run_catalog(service, "montre-moi vos produits", [], "french", admission, catalog)
    assert "consulter" in reply.text and "secret" not in reply.text
    assert service._client.respond.call_count <= 3
    assert reply.catalog_refs["presented"] == []


def test_admission_failure_no_call(catalog_env):
    service, admission, catalog = setup(catalog_env, [])
    admission.reserve.side_effect = RuntimeError("storage failed")
    run_catalog(service, "produits", [], "french", admission, catalog)
    service._client.respond.assert_not_called()


def test_catalog_db_failure_no_call(catalog_env, monkeypatch):
    service, admission, catalog = setup(catalog_env, [])
    monkeypatch.setattr(catalog, "authorize", Mock(side_effect=RuntimeError("db secret")))
    reply = run_catalog(service, "produits", [], "french", admission, catalog)
    assert "consulter" in reply.text
    service._client.respond.assert_not_called()


def test_unverified_plan(catalog_env, catalog_product):
    p, _ = catalog_product()
    service, admission, catalog = setup(catalog_env, [plan(p)])
    reply = run_catalog(service, "produits", [], "french", admission, catalog)
    assert "consulter" in reply.text and not reply.catalog_refs["presented"]


def test_fresh_stock_disappearance(db_session, catalog_env, catalog_product):
    p, v = catalog_product()
    def final():
        v.stock_quantity = 0
        db_session.flush()
        return plan(p)
    service, admission, catalog = setup(catalog_env, [tool(), final])
    reply = run_catalog(service, "produits en stock", [], "french", admission, catalog)
    assert "pas trouvé" in reply.text


def test_deadline_stops_before_paid_call(catalog_env, monkeypatch):
    service, admission, catalog = setup(catalog_env, [])
    ticks = iter([0, 46])
    monkeypatch.setattr("app.ai.catalog_orchestrator.monotonic", lambda: next(ticks))
    # The catalog's independent clock is stubbed only for this deadline unit test.
    monkeypatch.setattr("app.services.catalog_service.monotonic", lambda: 0)
    reply = run_catalog(service, "produits", [], "english", admission, catalog)
    assert "right now" in reply.text
    admission.reserve.assert_not_called()
    service._client.respond.assert_not_called()


def test_prior_user_budget_is_preserved(catalog_env, catalog_product):
    catalog_product(price=Decimal("400.00"))
    service, admission, catalog = setup(catalog_env, [tool(), plan(action="no_matches")])
    reply = run_catalog(service, "chno katnsa7ni?", [AIHistoryMessage(role="user", content="budget 300dh")],
                        "french", admission, catalog)
    assert "pas trouvé" in reply.text


def test_failed_telemetry_stops_continuation(catalog_env):
    service, admission, catalog = setup(catalog_env, [tool()])
    admission.safe_record.return_value = False
    reply = run_catalog(service, "produits", [], "french", admission, catalog)
    assert "consulter" in reply.text
    assert service._client.respond.call_count == 1
