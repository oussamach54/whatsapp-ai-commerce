"""Real-incident regression: focused Noir/M -> L exists but has zero stock."""
from datetime import timedelta
from decimal import Decimal
import json
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.ai.catalog_orchestrator import attribute_change, run_catalog
from app.ai.catalog_schemas import CatalogRefs, GetProducts, ProductRef, SearchProducts
from app.ai.catalog_tools import TOOLS
from app.ai.router import SalesRouter
from app.ai.schemas import AIHistoryMessage
from app.models import Message, ProductVariant
from app.models.enums import MessageDirection, MessageType, SenderType
from tests.test_catalog_orchestration import setup, tool, plan
from tests.test_catalog_references import presentation


@pytest.fixture
def pants(db_session, catalog_env, catalog_product):
    product, m = catalog_product(name="Pantalon Classic")
    m.name, m.price, m.stock_quantity = "Noir / M", Decimal("249.00"), 5
    l = ProductVariant(product=product, sku=uuid4().hex, name="Noir / L", price=Decimal("249.00"),
                       size="L", color="noir", stock_quantity=0)
    blue = ProductVariant(product=product, sku=uuid4().hex, name="Bleu / M", price=Decimal("229.00"),
                          size="M", color="bleu", stock_quantity=3)
    db_session.add_all([l, blue])
    db_session.flush()
    ref = ProductRef(product_id=product.id, variant_id=m.id)
    presentation(db_session, catalog_env[1], CatalogRefs(presented=[ref], focus=ref))
    return product, m, l, blue


@pytest.mark.parametrize("question", ["w taille L?", "et taille L?", "et en taille L?", "taille L?"])
def test_real_incident_deterministic_zero_stock_and_new_focus(db_session, catalog_env, pants, question):
    product, m, l, _ = pants
    # A size match on another color cannot replace the zero-stock black variant.
    db_session.add(ProductVariant(product=product, sku=uuid4().hex, name="Bleu / L", price=Decimal("200"),
                                  size="L", color="bleu", stock_quantity=9))
    db_session.flush()
    service, admission, catalog = setup(catalog_env, [])
    reply = run_catalog(service, question, [AIHistoryMessage(role="assistant", content="Noir / L costs 1 MAD, stock 99")],
                        "darija_latin", admission, catalog)
    assert "Noir / L" in reply.text and "249.00 MAD" in reply.text and "salat l quantite" in reply.text
    assert "Bleu" not in reply.text and "99" not in reply.text and "Ma l9itch" not in reply.text
    assert reply.catalog_refs["focus"] == {"product_id": str(product.id), "variant_id": str(l.id)}
    assert set(reply.catalog_refs["focus"]) == {"product_id", "variant_id"}
    assert reply.catalog_refs["selection"] is None
    service._client.respond.assert_not_called()
    admission.reserve.assert_not_called()


def test_nonexistent_xl_is_not_out_of_stock(catalog_env, pants, catalog_product):
    # XL on another product must not count as a match.
    catalog_product(size="XL", stock_quantity=0)
    service, admission, catalog = setup(catalog_env, [])
    reply = run_catalog(service, "w taille XL?", [], "french", admission, catalog)
    assert "cette variante pour ce produit" in reply.text
    assert "épuisé" not in reply.text and "249" not in reply.text
    # A missing sibling no longer destroys the last verified target.
    assert reply.catalog_refs["focus"]["variant_id"] == str(pants[1].id)
    assert reply.catalog_refs["selection"] is None
    assert reply.commerce_state["attempted"]["outcome"] == "exact_miss"


@pytest.mark.parametrize("question", ["w bleu?", "et en bleu?", "couleur bleu?", "w blue?"])
def test_color_change_preserves_size_without_classifier(catalog_env, pants, question):
    product, _, _, blue = pants
    service, admission, catalog = setup(catalog_env, [])
    admission.begin.return_value = "allowed"
    classifier = Mock()
    router = SalesRouter(service, classifier, admission, catalog.settings, catalog)
    reply = router.reply(question, catalog.target.conversation_id, lambda: [])
    assert "Bleu / M" in reply.text and "229.00 MAD" in reply.text
    assert reply.catalog_refs["focus"]["variant_id"] == str(blue.id)
    classifier.classify.assert_not_called()
    service._client.respond.assert_not_called()
    admission.reserve.assert_not_called()


def test_current_db_attributes_price_stock_used(db_session, catalog_env, pants):
    product, m, l, blue = pants
    # The old focused identity's current color, not historical prose, is authoritative.
    m.color = "bleu"
    other = ProductVariant(product=product, sku=uuid4().hex, name="Bleu / L", price=Decimal("275.00"),
                           size="L", color="bleu", stock_quantity=2)
    db_session.add(other)
    db_session.flush()
    service, admission, catalog = setup(catalog_env, [])
    reply = run_catalog(service, "w taille L?", [AIHistoryMessage(role="assistant", content="Noir M, 249 MAD, stock 5")],
                        "french", admission, catalog)
    assert "Bleu / L" in reply.text and "275.00 MAD" in reply.text and "en stock" in reply.text
    assert reply.catalog_refs["focus"]["variant_id"] == str(other.id)


def test_attributes_before_limit_and_discovery_unchanged(db_session, catalog_env, pants):
    product, _, l, _ = pants
    catalog = catalog_env[0]
    for i in range(8):
        db_session.add(ProductVariant(product=product, sku=uuid4().hex, name=f"Small {i}", price=Decimal(i + 1),
                                      size="S", color="bleu", stock_quantity=1))
    db_session.flush()
    unfiltered = catalog.get(GetProducts(product_ids=[product.id]))
    assert len(unfiltered.products[0].variants) == 6 and unfiltered.products[0].has_more_variants
    assert l.id not in {v.id for v in unfiltered.products[0].variants}
    filtered = catalog.get(GetProducts(product_ids=[product.id], size="l", color="BLACK"))
    assert [v.id for v in filtered.products[0].variants] == [l.id]
    assert filtered.products[0].variants[0].availability == "out_of_stock"
    assert not catalog.search(SearchProducts(terms=["pantalon"], size="L", color="noir")).products
    service, admission, catalog = setup(catalog_env, [])
    reply = run_catalog(service, "w taille L?", [], "english", admission, catalog)
    assert "249.00 MAD" in reply.text and "out of stock" in reply.text


@pytest.mark.parametrize("ambiguous", [False, True])
def test_missing_or_ambiguous_focus_clarifies(db_session, catalog_env, pants, ambiguous):
    product, m, _, blue = pants
    refs = CatalogRefs(presented=[ProductRef(product_id=product.id, variant_id=m.id),
                                 ProductRef(product_id=product.id, variant_id=blue.id)]) if ambiguous else CatalogRefs()
    presentation(db_session, catalog_env[1], refs, delta=-0.5)
    service, admission, catalog = setup(catalog_env, [])
    reply = run_catalog(service, "w taille L?", [], "english", admission, catalog)
    assert reply.text == "Which product do you mean?"
    service._client.respond.assert_not_called()


@pytest.mark.parametrize("active_field", ["product", "requested_variant"])
def test_unavailable_product_distinct_from_missing_variant(db_session, catalog_env, pants, active_field):
    product, _, l, _ = pants
    (product if active_field == "product" else l).is_active = False
    db_session.flush()
    service, admission, catalog = setup(catalog_env, [])
    reply = run_catalog(service, "w taille L?", [], "english", admission, catalog)
    expected = "This product is unavailable." if active_field == "product" else "I couldn't find that variant for this product."
    assert reply.text == expected


@pytest.mark.parametrize("question", ["je cherche tous les pantalons taille L", "toutes les couleurs",
    "oublie le noir, je cherche autre chose", "taille toutes", "w bleu pour un autre produit?"])
def test_reset_or_broadening_does_not_inherit_attributes(question):
    assert attribute_change(question) is None


def test_planner_empty_in_stock_search_cannot_prove_nonexistence(catalog_env, pants):
    product, _, l, _ = pants
    # Less obvious phrasing can still use the planner. Its empty discovery result
    # and no_matches plan must be checked against stock-inclusive focused detail.
    outputs = [tool(args=SearchProducts(terms=["pantalon"], size="L", in_stock_only=True).model_dump_json()),
               plan(action="no_matches", reference="focus")]
    service, admission, catalog = setup(catalog_env, outputs)
    reply = run_catalog(service, "vous avez le même en L ?", [], "french", admission, catalog)
    assert "Noir / L" in reply.text and "249.00 MAD" in reply.text and "épuisé" in reply.text
    assert reply.catalog_refs["focus"]["variant_id"] == str(l.id)


def test_detail_supersedes_discovery_stock_filter_on_final_refresh(catalog_env, pants):
    product, _, l, _ = pants
    search = SearchProducts(terms=["pantalon"], size="L", color="noir", in_stock_only=True)
    detail = GetProducts(product_ids=[product.id], size="L", color="noir")
    service, admission, catalog = setup(catalog_env, [tool(args=search.model_dump_json()),
        tool("get_products", detail.model_dump_json(), "call2"), plan(product, l)])
    reply = run_catalog(service, "disponibilité du Pantalon Classic noir L", [], "english", admission, catalog)
    assert "Noir / L" in reply.text and "out of stock" in reply.text
    assert reply.catalog_refs["focus"]["variant_id"] == str(l.id)
    assert len(TOOLS) == 2


def test_following_price_turn_uses_new_focus_and_fresh_price(db_session, catalog_env, pants):
    product, _, l, _ = pants
    service, admission, catalog = setup(catalog_env, [])
    second = run_catalog(service, "w taille L?", [], "darija_latin", admission, catalog)
    inbound = catalog_env[1]
    # Persist the emitted reference through the unchanged Message JSONB format.
    presentation(db_session, inbound, CatalogRefs.model_validate_json(json.dumps(second.catalog_refs)), delta=1)
    third = Message(conversation_id=inbound.conversation_id, direction=MessageDirection.INBOUND,
        sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT, content="ch7al taman dyalha?",
        created_at=inbound.created_at + timedelta(seconds=2), metadata_={"phone_number_id": catalog.settings.whatsapp_phone_number_id})
    db_session.add(third)
    l.price, l.stock_quantity = Decimal("259.00"), 2
    db_session.flush()
    catalog.target = catalog.target.model_copy(update={"inbound_id": third.id})
    service, admission, catalog = setup(catalog_env, [plan(product, reference="focus")])
    reply = run_catalog(service, third.content, [AIHistoryMessage(role="assistant", content="Noir / L 249 MAD out of stock")],
                        "darija_latin", admission, catalog)
    assert "259.00 MAD" in reply.text and "kayn f stock" in reply.text and "249" not in reply.text
    assert reply.catalog_refs["focus"]["variant_id"] == str(l.id)


def test_stock_inclusive_detail_does_not_drop_size_color_constraints(catalog_env, pants):
    product, m, l, _ = pants
    service, admission, catalog = setup(catalog_env, [
        tool(args=SearchProducts(size="L", color="noir").model_dump_json()),
        tool("get_products", GetProducts(product_ids=[product.id]).model_dump_json(), "call2"),
        plan(product)])
    reply = run_catalog(service, "disponibilité du Pantalon Classic noir L", [], "french", admission, catalog)
    assert "Noir / L" in reply.text and "épuisé" in reply.text
    assert "Noir / M" not in reply.text and "Bleu / M" not in reply.text


@pytest.mark.parametrize("size,expected", [("XL", "cette variante"), ("L", "consulter le catalogue")])
def test_detail_no_matches_plan_requires_stock_inclusive_absence(catalog_env, pants, size, expected):
    product, _, _, _ = pants
    service, admission, catalog = setup(catalog_env, [
        tool("get_products", GetProducts(product_ids=[product.id], size=size, color="noir").model_dump_json()),
        plan(action="no_matches")])
    reply = run_catalog(service, "vérifier le Pantalon Classic", [], "french", admission, catalog)
    # XL has a proven absence; L must not be called nonexistent by a bad model plan.
    assert expected in reply.text


@pytest.mark.parametrize("style,absent,unavailable,stock", [
    ("english", "that variant", "This product is unavailable", "out of stock"),
    ("french", "cette variante", "Ce produit", "épuisé"),
    ("darija_latin", "Ma l9itch had variante", "Had produit", "salat l quantite"),
    ("mixed", "Ma l9itch had variante", "Had produit", "salat l quantite"),
    ("darija_arabic", "ما لقيتش هاد النسخة", "هاد المنتوج", "سالا المخزون"),
])
def test_localized_existence_states(db_session, catalog_env, pants, style, absent, unavailable, stock):
    product, _, _, _ = pants
    service, admission, catalog = setup(catalog_env, [])
    exists = run_catalog(service, "w taille L?", [], style, admission, catalog)
    assert stock in exists.text and absent not in exists.text
    missing = run_catalog(service, "w taille XL?", [], style, admission, catalog)
    assert absent in missing.text and stock not in missing.text
    product.is_active = False
    db_session.flush()
    gone = run_catalog(service, "w taille L?", [], style, admission, catalog)
    assert unavailable in gone.text and stock not in gone.text


@pytest.mark.parametrize("question,index", [("w taille L?", 2), ("w bleu?", 3)])
def test_pipeline_persists_new_focus_without_network_or_paid_calls(db_session, catalog_env, pants, question, index):
    from app.services.whatsapp_service import send_automatic_reply
    catalog, inbound, active = catalog_env
    inbound.content = question
    db_session.flush()
    service, _, _ = setup(catalog_env, [])
    def send(*args):
        assert not active, "DB session open during mocked WhatsApp send"
        return uuid4().hex
    whatsapp = Mock(send_text_message=Mock(side_effect=send))
    send_automatic_reply(catalog.target, whatsapp, catalog.sessions, question, service)
    outbound = db_session.scalars(select(Message).where(
        Message.conversation_id == inbound.conversation_id,
        Message.metadata_["in_reply_to"].astext == str(inbound.id),
        Message.direction == MessageDirection.OUTBOUND)).one()
    assert outbound.metadata_["catalog_refs"]["focus"] == {
        "product_id": str(pants[0].id), "variant_id": str(pants[index].id)}
    assert outbound.content == whatsapp.send_text_message.call_args.args[1]
    assert inbound.metadata_["ai_guard"]["attempts"] == {}
    service._client.respond.assert_not_called()


def test_broadened_request_does_not_retain_old_color(db_session, catalog_env, pants):
    product, _, _, _ = pants
    db_session.add(ProductVariant(product=product, sku=uuid4().hex, name="Bleu / L", price=Decimal("229"),
                                  size="L", color="bleu", stock_quantity=3))
    db_session.flush()
    service, admission, catalog = setup(catalog_env, [
        tool(args=SearchProducts(terms=["pantalon"], size="L", in_stock_only=False).model_dump_json()), plan(product)])
    reply = run_catalog(service, "je cherche tous les pantalons taille L, toutes les couleurs", [],
                        "french", admission, catalog)
    assert "Noir / L" in reply.text and "Bleu / L" in reply.text
