"""Completed-order history must not masquerade as a new purchase."""
import pytest
from sqlalchemy import select, func

from app.models import InventoryMovement, Order
from tests.test_cod_checkout import checkout, cart, item, plan, supply, order_count
from tests.test_commerce_conversations import dialogue
from tests.test_catalog_variant_followups import pants


INCIDENT = "salam bghet n commandé f site ms makhdamch"


def completed(checkout):
    checkout("je veux commander pantalon noir M")
    checkout("oui")
    return cart(supply(checkout))


def test_real_completed_then_website_then_fresh_selection(checkout, pants, db_session):
    old = completed(checkout)
    order = db_session.scalar(select(Order))
    original = {c.name: getattr(order, c.name) for c in Order.__table__.columns}
    row = checkout(INCIDENT, [plan(INCIDENT, "website_ordering", reference="none", language="darija_latin")])
    assert not cart(row)
    assert "directement hna" in row.content and "Chno bghiti" in row.content
    assert "Noir" not in row.content and "249" not in row.content
    # Durable retirement survives another turn and cannot reload the old cart.
    row = checkout("salam")
    assert not cart(row)
    pants[1].price = 251
    db_session.flush()
    row = checkout("pantalon noir M")
    assert cart(row)["id"] != old["id"]
    assert cart(row)["status"] == "awaiting_confirmation"
    assert cart(row)["items"][0]["stock"] == 4
    assert "251.00 MAD" in row.content and "?" in row.content
    assert order_count(db_session) == 1 and pants[1].stock_quantity == 4
    assert db_session.scalar(select(func.count()).select_from(InventoryMovement)) == 1
    db_session.refresh(order)
    assert {c.name: getattr(order, c.name) for c in Order.__table__.columns} == original


@pytest.mark.parametrize("multiple", [False, True])
def test_website_named_purchase_after_completed_is_new(checkout, pants, db_session, multiple):
    old = completed(checkout)
    text = "site ma khdamch, bghit 2 pantalon noir M et wahed bleu M"
    items = [item(quantity=2, terms=["pantalon"])] + ([item("bleu")] if multiple else [])
    row = checkout(text, [plan(text, "website_ordering", reference="none", items=items)])
    assert cart(row)["id"] != old["id"] and cart(row)["status"] == "awaiting_confirmation"
    assert len(cart(row)["items"]) == (2 if multiple else 1)
    assert order_count(db_session) == 1 and pants[1].stock_quantity == 4


@pytest.mark.parametrize("text,intent,extra", [
    ("salam", None, {}),
    ("how can I pay?", "business_question", {"business_topic": "payment"}),
    ("how long does delivery take?", "business_question", {"business_topic": "delivery_time"}),
    ("wach pantalon noir M kayn?", "availability", {"terms": ["pantalon"], "attributes": item()["attributes"], "speech_act": "question"}),
])
def test_nonpurchase_after_completed_does_not_render_success(checkout, pants, db_session, text, intent, extra):
    completed(checkout)
    row = checkout(text, [plan(text, intent, reference="none", language="english", **extra)] if intent else ())
    assert "Your order is confirmed" not in row.content and "confirmée" not in row.content and "t2ekkdat" not in row.content
    assert not cart(row) or cart(row)["status"] == "completed"
    assert order_count(db_session) == 1 and pants[1].stock_quantity == 4


def test_new_purchase_and_duplicate_confirmation(checkout, db_session):
    old = completed(checkout)
    assert cart(checkout("oui"))["id"] == old["id"]
    row = checkout("je veux commander pantalon noir M")
    assert cart(row)["id"] != old["id"] and cart(row)["status"] == "awaiting_confirmation"
    assert order_count(db_session) == 1


def test_unknown_fee_omitted_from_proposal_and_success(checkout):
    row = checkout("je veux commander pantalon noir M")
    assert "livraison" not in row.content.lower()
    checkout("oui")
    row = supply(checkout)
    assert "non confirm" not in row.content.lower()
    assert "m2ekkdinch" not in row.content


def test_unspecified_new_purchase_does_not_assume_previous_product(checkout, db_session):
    completed(checkout)
    text = "I want to place a new order"
    row = checkout(text, [plan(text, "purchase", reference="none", language="english")])
    assert not cart(row) and "What would you like?" in row.content
    assert "249" not in row.content and order_count(db_session) == 1


def test_previous_order_question_does_not_start_purchase(checkout, db_session):
    old = completed(checkout)
    text = "fin wslat commande dyali?"
    row = checkout(text, [plan(text, "business_question", business_topic="order_status", reference="none")])
    assert cart(row)["id"] == old["id"] and cart(row)["status"] == "completed"
    assert "Votre commande est confirmée" not in row.content and order_count(db_session) == 1
    assert "équipe" in row.content
