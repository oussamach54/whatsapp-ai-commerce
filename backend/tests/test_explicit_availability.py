"""Literal availability constraints through persisted turns, without providers."""
import pytest

from app.ai.conversation_engine import availability_body
from app.models.enums import OrderStatus
from tests.test_cod_checkout import checkout, cart, order_count, plan
from tests.test_commerce_conversations import dialogue, refs
from tests.test_catalog_variant_followups import pants
from tests.test_order_cancellation import completed, restored


@pytest.mark.parametrize("text,index", [
    ("wach Pantalon Classic Noir taille M kayn f stock?", 1),
    ("wach Pantalon Classic Bleu taille M mazal kayn?", 3),
    ("Est-ce que Pantalon Classic Noir taille M est disponible?", 1),
    ("Is Pantalon Classic color noir size M in stock?", 1),
    ("Is Pantalon Classic color bleu size M still available?", 3),
    ("Pantalon Classic Noir taille L disponible?", 2),
])
def test_explicit_availability_preserves_constraints(checkout, pants, db_session, text, index):
    row = checkout(text)
    state = row.metadata_["commerce_state"]
    assert state["operation"] == "stock"
    assert refs(row)["focus"]["variant_id"] == str(pants[index].id)
    assert refs(row)["selection"] is None and not cart(row)
    assert state["constraints"]["color"] == pants[index].color
    assert state["constraints"]["size"] == pants[index].size
    assert ("épuisé" in row.content.lower()) == (pants[index].stock_quantity == 0)
    assert order_count(db_session) == 0
    checkout.service._client.respond.assert_not_called()


def test_other_product_and_labeled_category_brand(checkout, catalog_product, db_session):
    product, variant = catalog_product(name="Chemise Oxford", size="XL", color="vert", stock_quantity=0)
    product.category, product.brand = "Chemises", "Atlas"
    db_session.flush()
    row = checkout("Is Chemise Oxford category Chemises brand Atlas color vert size XL available?")
    assert refs(row)["focus"]["variant_id"] == str(variant.id)
    state = row.metadata_["commerce_state"]
    assert state["constraints"]["brand"] == "atlas"
    assert state["constraints"]["category"] == "chemises"
    assert "out of stock" in row.content.lower()
    assert not cart(row) and order_count(db_session) == 0
    checkout.service._client.respond.assert_not_called()


def test_explicit_then_standalone_uses_fresh_stock(checkout, pants, db_session):
    first = checkout("wach Pantalon Classic Noir taille M kayn f stock?")
    assert "kayn f stock" in first.content.lower()
    pants[1].stock_quantity = 0
    db_session.flush()
    second = checkout("kayn f stock?")
    assert "salat l quantite" in second.content.lower()
    assert refs(second) == refs(first)
    pants[1].stock_quantity = 4
    db_session.flush()
    assert "kayn f stock" in checkout("Pantalon Classic Noir taille M disponible?").content.lower()
    checkout.service._client.respond.assert_not_called()


def test_standalone_without_trusted_recent_focus(checkout, db_session):
    row = checkout("kayn f stock?")
    assert row.content == "Achmen produit kat9sed?"
    assert refs(row)["focus"] is None and not cart(row)
    assert order_count(db_session) == 0
    checkout.service._client.respond.assert_not_called()


def test_unknown_option_still_asks_attribute(checkout, pants):
    row = checkout("wach Pantalon Classic kayn f coton?")
    assert row.metadata_["commerce_state"]["operation"] == "clarify"
    assert "caractéristique" in row.content
    checkout.service._client.respond.assert_not_called()


def test_real_persisted_unresolved_stock_does_not_capture_explicit_question(checkout, pants, db_session):
    row = checkout("Pantalon Classic Bleu taille M disponible?")
    data = dict(row.metadata_)
    state = dict(data["commerce_state"])
    state["unresolved_attribute_value"] = "stock"
    state["active_attribute"] = "color"
    data["commerce_state"] = state
    row.metadata_ = data
    db_session.flush()
    result = checkout("wach Pantalon Classic Noir taille M kayn f stock?")
    assert refs(result)["focus"]["variant_id"] == str(pants[1].id)
    assert result.metadata_["commerce_state"]["unresolved_attribute_value"] is None
    assert result.metadata_["commerce_state"]["operation"] == "stock"
    checkout.service._client.respond.assert_not_called()


@pytest.mark.parametrize("target", ["Missing Product Noir taille M", "one two three four five six seven eight"])
def test_failed_or_unparsed_explicit_reference_cannot_reuse_focus(checkout, pants, target):
    checkout("Pantalon Classic Bleu taille M disponible?")
    row = checkout(target + " in stock?")
    assert "en stock" not in row.content.lower() and "229.00" not in row.content
    assert not cart(row)
    followup = checkout("kayn f stock?")
    assert "kayn f stock" not in followup.content.lower() and "229.00" not in followup.content
    checkout.service._client.respond.assert_not_called()


@pytest.mark.parametrize("question", ["wach Pantalon Classic Noir taille M kayn f stock?",
                                       "Pantalon Classic Bleu taille M disponible"])
def test_availability_does_not_confirm_checkout(checkout, pants, db_session, question):
    offered = checkout("je veux commander pantalon noir M")
    assert cart(offered)["status"] == "awaiting_confirmation"
    stock = pants[1].stock_quantity
    row = checkout(question)
    assert cart(row) == cart(offered)
    assert refs(row)["selection"] == refs(offered)["selection"]
    assert order_count(db_session) == 0 and pants[1].stock_quantity == stock
    assert "confirmation_prompt" not in row.metadata_
    if "Bleu" in question:
        assert refs(row)["focus"]["variant_id"] == str(pants[3].id)
        assert refs(row)["focus"]["variant_id"] != refs(row)["selection"]["variant_id"]


def test_availability_does_not_confirm_cancellation(checkout, pants, db_session):
    _, order = completed(checkout, db_session)
    offered = checkout("cancel my order")
    assert offered.metadata_["commerce_state"]["cancellation"]["status"] == "awaiting_confirmation"
    stock = pants[1].stock_quantity
    row = checkout("wach Pantalon Classic Noir taille M kayn f stock?")
    db_session.refresh(order)
    assert order.status == OrderStatus.CONFIRMED and not restored(db_session, order)
    assert pants[1].stock_quantity == stock
    assert row.metadata_["commerce_state"]["cancellation"]["status"] == "superseded"
    assert "confirmation_prompt" not in row.metadata_
    # A stock reply also cannot leave the preceding confirmation offer active.
    checkout("oui")
    db_session.refresh(order)
    assert order.status == OrderStatus.CONFIRMED and not restored(db_session, order)


@pytest.mark.parametrize("text", ["kayn f coton?", "wach produit kayn f coton?", "stock material?", "storage 128gb?"])
def test_no_arbitrary_attribute_becomes_stock(text):
    assert availability_body(text) is None


@pytest.mark.parametrize("intent", ["purchase", "confirmation", "cancellation"])
def test_implicit_variant_availability_cannot_acquire_mutation_intent(checkout, pants, db_session, intent):
    first = checkout("Pantalon Classic Noir taille M disponible?")
    text = "is blue medium available?"
    row = checkout(text, [plan(text, intent)])
    assert refs(row)["selection"] == refs(first)["selection"]
    assert not cart(row) and order_count(db_session) == 0
    assert row.metadata_["commerce_state"]["cancellation"] is None
    assert "confirmation_prompt" not in row.metadata_


def test_policy_interrupt_delivers_explicit_unchanged_checkout_offer(checkout, pants, db_session):
    offered = checkout("je veux commander pantalon noir M")
    text = "what is the delivery policy?"
    row = checkout(text, [plan(text, "business_question", business_topic="delivery_time")])
    assert cart(row) == cart(offered)
    assert "Confirmez-vous la commande ?" in row.content
    assert row.metadata_["confirmation_prompt"] == offered.metadata_["confirmation_prompt"]
    assert "1× Pantalon Classic" in row.content
    assert order_count(db_session) == 0
    assert cart(checkout("oui"))["status"] == "confirmed"
