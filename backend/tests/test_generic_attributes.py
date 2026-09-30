"""Multi-domain conversations; variant labels never fake structured attributes."""
from datetime import timedelta
from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.ai.attribute_adapter import CATALOG_ATTRIBUTES, CatalogAttributeAdapter, CatalogAttribute, UnsupportedAttribute
from app.ai.catalog_schemas import AttributeRequest, CatalogRefs, ProductRef, ResponsePlan, SearchProducts
from app.ai.commerce_state import CommerceState
from app.ai.schemas import ProviderResult
from app.integrations.whatsapp.schemas import ReplyTarget
from app.models import Message, Product, ProductVariant
from app.models.enums import MessageDirection, MessageType, SenderType
from app.services.whatsapp_service import send_automatic_reply
from tests.test_catalog_orchestration import setup, tool, plan
from tests.test_catalog_references import presentation


DOMAINS = {
    "fashion": ("Veste Atelier", [("Noir M", "M", "noir"), ("Noir L", "L", "noir")]),
    "makeup": ("Rouge à lèvres Atelier", [("Nude", None, None), ("Rose", None, None)]),
    "eyewear": ("Lunettes Atelier", [("Noir", None, "noir"), ("Marron", None, "marron")]),
    "electronics": ("Telephone Atelier", [("128GB", None, None), ("256GB", None, None)]),
    "perfume": ("Parfum Atelier", [("100ml", None, None), ("50ml", None, None)]),
    "shoes": ("Chaussures Atelier", [("42 noir", "42", "noir"), ("43 noir", "43", "noir")]),
    "furniture": ("Table Atelier", [("180cm", None, None), ("200cm", None, None)]),
    "retail": ("Cafe Atelier", [("250g", None, None), ("500g", None, None)]),
}


@pytest.fixture
def domain_product(db_session):
    def create(domain):
        name, options = DOMAINS[domain]
        product = Product(name=name, slug=uuid4().hex, category=domain, brand="Atelier")
        variants = [ProductVariant(product=product, name=label, sku=uuid4().hex, size=size, color=color,
                    price=Decimal(250 + index * 50), stock_quantity=3) for index, (label, size, color) in enumerate(options)]
        db_session.add(product)
        db_session.flush()
        return product, variants
    return create


@pytest.fixture
def conversation(db_session, catalog_env, allowed_admission):
    catalog, inbound, active = catalog_env
    responses = []
    service, _, _ = setup(catalog_env, responses)
    def send(*args):
        assert not active
        return uuid4().hex
    sender = Mock(send_text_message=Mock(side_effect=send))
    previous = None
    def ask(text, outputs=()):
        nonlocal inbound, previous
        responses.extend(outputs)
        if previous:
            inbound = Message(conversation_id=inbound.conversation_id, direction=MessageDirection.INBOUND,
                sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT, content=text,
                created_at=previous.created_at + timedelta(seconds=1),
                metadata_={"provider": "whatsapp", "phone_number_id": catalog.settings.whatsapp_phone_number_id})
            db_session.add(inbound)
        else:
            inbound.content = text
        db_session.flush()
        target = ReplyTarget(conversation_id=inbound.conversation_id, inbound_id=inbound.id, phone_number="212600001111")
        send_automatic_reply(target, sender, catalog.sessions, text, service)
        previous = db_session.scalars(select(Message).where(Message.metadata_["in_reply_to"].astext == str(inbound.id))).one()
        previous.created_at = inbound.created_at + timedelta(seconds=.5)
        db_session.flush()
        return previous
    def focus(product, variants):
        items = [ProductRef(product_id=product.id, variant_id=v.id) for v in variants]
        presentation(db_session, inbound, CatalogRefs(presented=items, focus=items[0]))
    ask.focus, ask.service = focus, service
    return ask


@pytest.mark.parametrize("domain,initial,followup,key,value", [
    ("makeup", "shade nude", "w rose?", "shade", "rose"),
    ("eyewear", "cadre noir?", "w marron?", "frame_color", "marron"),
    ("electronics", "128GB?", "w 256?", "storage", "256"),
    ("electronics", "3ndkom 128GB?", "w 256GB?", "storage", "256gb"),
    ("perfume", "wach kayn 100ml?", "w 50ml?", "volume", "50ml"),
    ("furniture", "version 2 mètres?", "w 180cm?", "dimensions", "180cm"),
    ("retail", "250g?", "w 500g?", "weight", "500g"),
])
def test_unverified_attribute_sequences(conversation, domain_product, domain, initial, followup, key, value):
    product, variants = domain_product(domain)
    conversation.focus(product, variants)
    for question in (initial, followup):
        row = conversation(question)
        state = row.metadata_["commerce_state"]
        refs = row.metadata_["catalog_refs"]
        assert refs["focus"]["variant_id"] == str(variants[0].id)
        assert refs["selection"] is None
        assert state["pending"]["missing"] == "attribute"
        assert key in state["requested_attributes"]
        assert key not in state["preserved_attributes"]
        assert "MAD" not in row.content
        assert "256" not in (state["constraints"]["size"] or "")
    assert state["requested_attributes"][key] == value
    conversation.service._client.respond.assert_not_called()


@pytest.mark.parametrize("domain,question,expected_key,expected_value", [
    ("fashion", "w L?", "size", "L"),
    ("eyewear", "w marron?", "color", "marron"),
    ("shoes", "pointure 43?", "size", "43"),
    ("shoes", "w 43?", "size", "43"),
])
def test_supported_attributes_use_current_schema_across_domains(conversation, domain_product, domain, question, expected_key, expected_value):
    product, variants = domain_product(domain)
    conversation.focus(product, variants)
    row = conversation(question)
    assert row.metadata_["catalog_refs"]["focus"]["variant_id"] == str(variants[1].id)
    assert row.metadata_["catalog_refs"]["selection"] is None
    assert row.metadata_["commerce_state"]["changed_attributes"][expected_key] == expected_value
    assert "300.00 MAD" in row.content
    assert "frame_color" not in row.metadata_["commerce_state"]["requested_attributes"]
    conversation.service._client.respond.assert_not_called()


@pytest.mark.parametrize("domain,number", [("electronics", "256"), ("perfume", "100"), ("furniture", "180"), ("makeup", "42")])
def test_bare_numbers_do_not_become_sizes(conversation, domain_product, domain, number):
    product, variants = domain_product(domain)
    conversation.focus(product, variants)
    row = conversation("w " + number + "?")
    state = row.metadata_["commerce_state"]
    assert state["unresolved_attribute_value"] == number
    assert state["constraints"]["size"] is None
    assert state["requested_attributes"] == {}
    assert "MAD" not in row.content
    assert row.metadata_["catalog_refs"]["focus"]["variant_id"] == str(variants[0].id)
    conversation.service._client.respond.assert_not_called()


def test_unlabeled_option_does_not_later_become_color(conversation, domain_product):
    product, variants = domain_product("makeup")
    conversation.focus(product, variants)
    first = conversation("had rouge à lèvres kayn f nude?")
    assert first.metadata_["commerce_state"]["unresolved_attribute_value"] == "nude"
    second = conversation("w rose?")
    assert second.metadata_["commerce_state"]["unresolved_attribute_value"] == "rose"
    assert second.metadata_["commerce_state"]["constraints"]["color"] is None
    assert "MAD" not in second.content
    conversation.service._client.respond.assert_not_called()


def test_numeric_context_is_reverified_not_read_from_variant_label(conversation, domain_product, db_session):
    product, variants = domain_product("shoes")
    conversation.focus(product, variants)
    variants[0].size = None  # The name still says '42 noir'; it is not field evidence.
    db_session.flush()
    row = conversation("w 43?")
    assert row.metadata_["commerce_state"]["unresolved_attribute_value"] == "43"
    assert "MAD" not in row.content
    conversation.service._client.respond.assert_not_called()


@pytest.mark.parametrize("domain", list(DOMAINS))
def test_ordinal_selection_and_fresh_price_are_category_independent(conversation, domain_product, db_session, domain):
    product, variants = domain_product(domain)
    conversation.focus(product, variants)
    selected = conversation("je prends le deuxième")
    assert selected.metadata_["catalog_refs"]["selection"]["variant_id"] == str(variants[1].id)
    variants[1].price = Decimal("321.00")
    db_session.flush()
    priced = conversation("ch7al?")
    assert priced.content == "321.00 MAD."
    other = conversation("w l'autre?")
    assert other.metadata_["catalog_refs"]["focus"]["variant_id"] == str(variants[0].id)
    assert other.metadata_["catalog_refs"]["selection"]["variant_id"] == str(variants[1].id)
    conversation.service._client.respond.assert_not_called()


@pytest.mark.parametrize("domain", ["makeup", "eyewear", "electronics", "perfume", "furniture", "retail"])
def test_generic_discovery_does_not_require_clothing(conversation, domain_product, domain):
    product, variants = domain_product(domain)
    row = conversation("bghit " + product.name)
    assert product.name in row.content
    assert "250.00 MAD" in row.content
    assert row.metadata_["catalog_refs"]["selection"] is None
    if all(v.size is None and v.color is None for v in variants):
        assert "taille" not in row.content and "couleur" not in row.content
        assert " / " not in row.content
    conversation.service._client.respond.assert_not_called()


@pytest.mark.parametrize("domain,text", [
    ("makeup", "3ndi 300dh bghit cadeau"),
    ("eyewear", "bghit lunettes moins de 500dh"),
    ("electronics", "bghit écouteurs budget 400dh"),
    ("perfume", "bghit parfum f 300dh"),
])
def test_semantic_budget_recommendations_are_generic(conversation, domain_product, domain, text):
    product, variants = domain_product(domain)
    row = conversation(text, [tool(args=SearchProducts(terms=[product.name], max_price="9999").model_dump_json()), plan(product)])
    assert "250.00 MAD" in row.content
    assert product.name in row.content
    assert row.metadata_["catalog_refs"]["selection"] is None
    assert "perfect" not in row.content
    assert int(row.metadata_["commerce_state"]["constraints"]["max_price"]) <= 500


def test_semantic_attribute_proposal_is_not_verification(conversation, domain_product):
    product, variants = domain_product("makeup")
    conversation.focus(product, variants)
    proposed = ResponsePlan(action="show", operation="variant", reference="focus", question=None,
        items=[ProductRef(product_id=product.id, variant_id=variants[1].id)],
        requested_attributes=[AttributeRequest(name="skin_type", value="dry")])
    row = conversation("ce produit convient à ma peau?", [ProviderResult(proposed.model_dump_json())])
    assert "MAD" not in row.content
    assert row.metadata_["catalog_refs"]["focus"]["variant_id"] == str(variants[0].id)
    assert row.metadata_["commerce_state"]["requested_attributes"]["skin_type"] == "dry"
    assert "skin_type" not in row.metadata_["commerce_state"]["preserved_attributes"]


def test_future_attribute_contract_without_faking_production_support():
    state = CommerceState(requested_attributes={"shade": "nude", "volume": "100ml", "storage": "256GB"})
    assert CommerceState.model_validate_json(state.model_dump_json()) == state
    assert set(CATALOG_ATTRIBUTES.supported) == {"size", "color"}
    # Only a synthetic future adapter uses this field. No ORM or production
    # service has acquired storage support through this test.
    class FutureAdapter(CatalogAttributeAdapter):
        fields = {"storage": CatalogAttribute(lambda dto: dto.storage, str.upper, "storage", r"\d+GB")}
    adapter = FutureAdapter()
    verified = SimpleNamespace(storage="128GB")
    preserved, updated = adapter.transition(verified, {"storage": "256gb"})
    assert preserved == {} and updated == {"storage": "256GB"}
    assert adapter.matches(SimpleNamespace(storage="256GB"), updated)
    with pytest.raises(UnsupportedAttribute):
        CATALOG_ATTRIBUTES.filters(updated)


@pytest.mark.parametrize("key", ["storage", "shade", "price", "stock_quantity", "product_id", "__dict__"])
def test_requests_cannot_access_arbitrary_dto_or_db_fields(key):
    with pytest.raises((UnsupportedAttribute, ValueError)):
        CATALOG_ATTRIBUTES.filters({key: "1"})


def test_attribute_memory_is_bounded_and_legacy_snapshots_load():
    assert CommerceState.model_validate_json('{"version":1,"constraints":{"size":"M","color":"noir"}}').constraints.size == "M"
    with pytest.raises(ValidationError):
        CommerceState(requested_attributes={f"attribute_{n}": "value" for n in range(9)})
    with pytest.raises(ValidationError):
        CommerceState(requested_attributes={"storage": "x" * 65})
