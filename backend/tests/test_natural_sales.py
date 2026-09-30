"""Semantic proposals through the real pipeline; network boundaries are mocked."""
from decimal import Decimal
from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import func, select

from app.ai.catalog_schemas import CustomerIntent, ResponsePlan
from app.ai.schemas import ProviderResult, ScopeDecision
from app.models import Order
from app.services.catalog_service import CatalogService
from app.services.checkout_service import verify_locked
from tests.test_catalog_variant_followups import pants
from tests.test_commerce_conversations import dialogue, refs


def state(row):
    return row.metadata_["commerce_state"]


def proposal(text, intent="availability", color="bleu", size="M", **kwargs):
    data = dict(intent=intent, reference="focus", language="french",
                attributes=[{"name": "color", "value": color}, {"name": "size", "value": size}])
    if intent == "purchase":
        data.update(purchase_intent=True, speech_act="affirmative", evidence=text)
    data.update(kwargs)
    plan = ResponsePlan(action="show", items=[], question=None, reference="none",
                        customer_intent=CustomerIntent.model_validate(data))
    return ProviderResult(plan.model_dump_json())


@pytest.mark.parametrize("text", ["bleu f taille M kyn ?", "bleu M kayn?",
    "vous avez le bleu en M ?", "is blue medium available?", "blue taille M kayn?",
    "واش bleu M كاين؟"])
def test_availability_equivalence(dialogue, pants, db_session, text):
    dialogue("je cherche pantalon noir taille M")
    pants[3].stock_quantity = 7
    pants[3].price = Decimal("231")
    db_session.flush()
    with patch.object(CatalogService, "get", autospec=True, side_effect=CatalogService.get) as reads:
        row = dialogue(text, [proposal(text)])
    assert reads.called
    assert refs(row)["focus"]["variant_id"] == str(pants[3].id)
    assert refs(row)["selection"] is None
    assert "231.00 MAD" in row.content
    assert state(row)["purchase"] is None


@pytest.mark.parametrize("text,color", [
    ("salam bghet n commandé pantalon noir taille M", "noir"),
    ("je veux le noir en M", "noir"), ("I want the black medium", "noir"),
    ("nakhod bleu M", "bleu"), ("salam I want bleu M", "bleu")])
def test_purchase_proposes_verified_variant(dialogue, pants, db_session, text, color):
    dialogue("je cherche pantalon noir taille M")
    before = db_session.scalar(select(func.count()).select_from(Order))
    row = dialogue(text, [proposal(text, "purchase", color=color)])
    purchase = state(row)["purchase"]
    assert purchase["target"]["variant_id"] == str(pants[1 if color == "noir" else 3].id)
    assert purchase["status"] == "awaiting_confirmation"
    assert "?" in row.content
    assert db_session.scalar(select(func.count()).select_from(Order)) == before


@pytest.mark.parametrize("change", ["unchanged", "price", "stock", "inactive"])
def test_confirmation_fresh_read(dialogue, pants, db_session, change):
    before = db_session.scalar(select(func.count()).select_from(Order))
    offered = dialogue("je veux commander pantalon noir M")
    assert state(offered)["purchase"]["status"] == "awaiting_confirmation"
    if change == "price":
        pants[1].price = Decimal("259")
    elif change == "stock":
        pants[1].stock_quantity = 0
    elif change == "inactive":
        pants[0].is_active = False
    db_session.flush()
    with patch("app.services.checkout_service.verify_locked", wraps=verify_locked) as reads:
        row = dialogue("oui")
    assert reads.called
    if change in ("stock", "inactive"):
        assert state(row)["purchase"] is None
    elif change == "price":
        assert state(row)["purchase"]["status"] == "awaiting_confirmation"
        assert "259.00 MAD" in row.content
        row = dialogue("oui")
        assert state(row)["purchase"]["status"] == "confirmed"
        assert state(row)["purchase"]["quoted_unit_price"] == "259.00"
    else:
        assert state(row)["purchase"]["status"] == "confirmed"
    dialogue.service._client.respond.assert_not_called()
    assert db_session.scalar(select(func.count()).select_from(Order)) == before


@pytest.mark.parametrize("color,size,exists", [("noir", "L", True), ("bleu", "L", False)])
def test_zero_stock_distinct_from_missing(dialogue, pants, color, size, exists):
    dialogue("je cherche pantalon noir taille M")
    text = f"is {color} {size} available?"
    row = dialogue(text, [proposal(text, color=color, size=size)])
    if exists:
        assert refs(row)["focus"]["variant_id"] == str(pants[2].id)
        assert "épuisé" in row.content
    else:
        assert state(row)["attempted"] is not None
        assert "pas trouvé" in row.content
    assert state(row)["purchase"] is None


@pytest.mark.parametrize("text,budget", [("chno katnsa7ni?", None),
    ("3ndi 240dh chno katnsa7ni?", "240"), ("que me conseillez-vous ?", None),
    ("I have a 240 MAD budget", "240")])
def test_recommendations_verified(dialogue, pants, text, budget):
    dialogue("je cherche pantalon noir taille M")
    row = dialogue(text, [proposal(text, "recommendation", attributes=[], budget=budget)])
    assert "229.00 MAD" in row.content
    if budget:
        assert "249.00 MAD" not in row.content
    assert refs(row)["selection"] is None


@pytest.mark.parametrize("text,topic", [("ila khdit 3 tn9ess lia taman?", "discount"),
    ("livraison l Casa ch7al?", "delivery_price"), ("n9der nrj3 produit?", "returns")])
def test_unknown_policies(dialogue, text, topic):
    dialogue("je cherche pantalon noir taille M")
    row = dialogue(text, [proposal(text, "business_question", attributes=[], business_topic=topic)])
    assert "information confirmée" in row.content
    assert "MAD" not in row.content
    assert state(row)["purchase"] is None


def test_semantic_confirmation_cannot_authorize_question(dialogue):
    dialogue("je veux commander pantalon noir M")
    row = dialogue("are you saying I confirmed?", [proposal("", "confirmation", attributes=[])])
    assert state(row)["purchase"] is None or state(row)["purchase"]["status"] != "confirmed"


def test_quantity_checks_current_stock(dialogue):
    dialogue("je cherche pantalon noir taille M")
    text = "I want six black medium"
    row = dialogue(text, [proposal(text, "purchase", color="noir", quantity=6)])
    assert state(row)["purchase"] is None


def test_greeting_purchase_without_prior_context(dialogue, pants):
    text = "salam bghet n commandé pantalon noir taille M"
    row = dialogue(text, [proposal(text, "purchase", color="noir", reference="none", terms=["pantalon"])])
    assert state(row)["purchase"]["target"]["variant_id"] == str(pants[1].id)
    assert state(row)["purchase"]["status"] == "awaiting_confirmation"
    assert refs(row)["selection"] is None


def test_recommendation_explicit_attribute_is_not_relaxed(dialogue):
    dialogue("je cherche pantalon noir taille M")
    text = "recommend something black"
    row = dialogue(text, [proposal(text, "recommendation", color="noir")])
    assert "Noir / M" in row.content
    assert "Bleu / M" not in row.content


def test_semantic_purchase_clarification_preserves_quantity(dialogue):
    text = "I want two pantalons"
    scope = ScopeDecision(scope="commerce", intent="purchase", language_style="english", security_action="none")
    with patch("app.ai.classifier.OpenAIClassifier.classify", return_value=scope):
        row = dialogue(text, [proposal(text, "purchase", reference="none", terms=["pantalon"], attributes=[], quantity=2)])
    assert state(row)["pending"]["quantity"] == 2
    dialogue("noir")
    row = dialogue("M")
    assert state(row)["purchase"]["quantity"] == 2
    assert state(row)["purchase"]["status"] == "awaiting_confirmation"
    assert refs(row)["selection"] is None


def test_decline_does_not_confirm(dialogue):
    dialogue("je veux commander pantalon noir M")
    row = dialogue("non")
    assert state(row)["purchase"] is None


def test_changed_price_is_rechecked_for_stock_on_second_yes(dialogue, pants, db_session):
    dialogue("je veux commander pantalon noir M")
    pants[1].price = Decimal("259")
    db_session.flush()
    assert state(dialogue("oui"))["purchase"]["status"] == "awaiting_confirmation"
    pants[1].stock_quantity = 0
    db_session.flush()
    assert state(dialogue("oui"))["purchase"] is None


def test_expired_offer_cannot_be_confirmed(dialogue, db_session):
    from app.models import Message
    from uuid import UUID
    row = dialogue("je veux commander pantalon noir M")
    inbound = db_session.get(Message, UUID(row.metadata_["in_reply_to"]))
    cart = dict(inbound.metadata_["checkout_state"], offered_at=(row.created_at - timedelta(minutes=16)).isoformat())
    inbound.metadata_ = dict(inbound.metadata_, checkout_state=cart)
    db_session.flush()
    row = dialogue("oui", [proposal("oui", "confirmation", attributes=[])])
    assert state(row)["cart"]["status"] == "awaiting_confirmation"
    assert state(row)["cart"]["order_id"] is None


def test_semantic_stock_change_below_quantity(dialogue, pants, db_session):
    dialogue("je cherche pantalon noir taille M")
    text = "I want two black medium"
    row = dialogue(text, [proposal(text, "purchase", color="noir", quantity=2)])
    assert state(row)["purchase"]["quantity"] == 2
    pants[1].stock_quantity = 1
    db_session.flush()
    row = dialogue("oui")
    assert state(row)["purchase"] is None
    assert refs(row)["selection"] is None


def test_business_source_is_unconfigured():
    from app.ai.business_knowledge import BUSINESS_KNOWLEDGE
    from typing import get_args
    from app.ai.catalog_schemas import BusinessTopic
    for topic in get_args(BusinessTopic):
        assert BUSINESS_KNOWLEDGE.lookup(topic, quantity=3, language="french") is None


@pytest.mark.parametrize("speech", ["question", "negative", "hypothetical", "quoted"])
def test_unsafe_semantic_purchase_does_not_offer(dialogue, speech):
    dialogue("je cherche pantalon noir taille M")
    text = "could I buy black medium?"
    row = dialogue(text, [proposal(text, "purchase", color="noir", speech_act=speech)])
    assert state(row)["purchase"] is None
    assert refs(row)["selection"] is None
