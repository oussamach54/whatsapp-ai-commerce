"""One delivered proposal needs one timely affirmative, including re-offers."""
from datetime import timedelta
from unittest.mock import patch
from uuid import UUID

import pytest
from sqlalchemy import select, func
from app.models import Message, Order, InventoryMovement
from app.services import checkout_service
from tests.test_cod_checkout import checkout, cart, item, plan, order_count, supply
from tests.test_commerce_conversations import dialogue
from tests.test_catalog_variant_followups import pants


def test_real_represented_cart_uses_fresh_offer_time(checkout, db_session):
    # Reproduce a carried checkpoint whose original offer is older than the
    # currently displayed proposal. Receipt of the latter starts its own window.
    offer = checkout("je veux commander pantalon bleu M")
    old = cart(offer)
    offer.created_at += timedelta(minutes=10)
    db_session.flush()
    availability = "pantalon bleu taille M"
    row = checkout(availability, [plan(availability, "availability", reference="none",
        terms=["pantalon"], attributes=item("bleu")["attributes"], speech_act="question")])
    assert "Confirmez" not in row.content
    purchase = "oui bghet n commandeh"
    proposal = checkout(purchase, [plan(purchase, "confirmation", reference="focus", language="mixed",
        confirmation=True)])
    assert cart(proposal)["id"] == old["id"] and cart(proposal)["status"] == "awaiting_confirmation"
    assert "Nconfirmiw" in proposal.content
    renewed = cart(proposal)
    assert renewed["version"] == old["version"] + 1
    assert renewed["offered_at"] != old["offered_at"]
    origin = db_session.get(Message, UUID(proposal.metadata_["in_reply_to"]))
    assert origin.metadata_["checkout_state"] == renewed
    proposal.created_at += timedelta(minutes=14)
    db_session.flush()
    with patch.object(checkout_service, "verify_locked", wraps=checkout_service.verify_locked) as verify:
        row = checkout("oui")
    assert cart(row)["id"] == old["id"] and cart(row)["status"] == "confirmed"
    verify.assert_called_once()
    assert "smiya" in row.content and "ville" in row.content and "adresse" in row.content
    assert "téléphone" not in row.content
    assert cart(row)["version"] == renewed["version"]
    assert cart(row)["confirmed_version"] == renewed["version"]
    assert "Nconfirmiw" not in row.content and "Confirmez-vous" not in row.content
    assert order_count(db_session) == 0


@pytest.mark.parametrize("purchase,affirmative,language", [
    ("oui bghet n commandeh", "oui", "darija_latin"),
    ("oui je veux le commander", "oui", "french"),
    ("yes I want to order it", "yes", "english"),
    ("oui bghet n commandeh", "wakha", "darija_latin"),
])
def test_single_confirmation_after_product_discussion(checkout, db_session, purchase, affirmative, language):
    text = "pantalon bleu taille M"
    row = checkout(text, [plan(text, "availability", reference="none", terms=["pantalon"],
        attributes=item("bleu")["attributes"], speech_act="question")])
    assert not cart(row)
    proposal = checkout(purchase, [plan(purchase, "purchase", reference="focus", language=language)])
    assert cart(proposal)["status"] == "awaiting_confirmation"
    with patch.object(checkout_service, "verify_locked", wraps=checkout_service.verify_locked) as verify:
        row = checkout(affirmative)
    verify.assert_called_once()
    assert cart(row)["id"] == cart(proposal)["id"] and cart(row)["status"] == "confirmed"
    assert order_count(db_session) == 0
    assert "phone" in cart(row)["fields"]
    assert not {"customer_name", "city", "address"} & cart(row)["fields"].keys()
    assert "229.00 MAD" not in row.content
    assert "Nconfirmiw" not in row.content and "Confirmez-vous" not in row.content
    assert "Shall I confirm" not in row.content
    expected = ("name", "city", "delivery address") if language == "english" else (
        "nom" if language == "french" else "smiya", "ville", "adresse de livraison")
    assert all(field in row.content for field in expected)
    assert "phone number" not in row.content and "téléphone" not in row.content
    assert cart(row)["version"] == cart(proposal)["version"]
    assert cart(row)["confirmed_version"] == cart(proposal)["version"]


@pytest.mark.parametrize("change", ["price", "stock", "expired"])
def test_confirmation_revalidation_still_requires_safe_transition(checkout, pants, db_session, change):
    proposal = checkout("je veux commander pantalon bleu M")
    if change == "price":
        pants[3].price = 240
    elif change == "stock":
        pants[3].stock_quantity = 0
    else:
        proposal.created_at += timedelta(minutes=16)
    db_session.flush()
    row = checkout("oui")
    assert cart(row)["status"] == ("blocked" if change == "stock" else "awaiting_confirmation")
    assert order_count(db_session) == 0
    if change == "price":
        assert "240.00 MAD" in row.content


def test_availability_affirmative_does_not_create_order(checkout, db_session):
    text = "pantalon bleu taille M"
    checkout(text, [plan(text, "availability", reference="none", terms=["pantalon"],
        attributes=item("bleu")["attributes"], speech_act="question")])
    row = checkout("oui")
    assert not cart(row) or cart(row)["status"] == "awaiting_confirmation"
    assert order_count(db_session) == 0


def test_single_confirmation_then_committed_order_retry(checkout, pants, db_session):
    text = "pantalon bleu taille M"
    checkout(text, [plan(text, "availability", reference="none", terms=["pantalon"],
        attributes=item("bleu")["attributes"], speech_act="question")])
    purchase = "oui bghet n commandeh"
    proposal = checkout(purchase, [plan(purchase, reference="focus")])
    assert cart(checkout("oui"))["status"] == "confirmed"
    completed = supply(checkout)
    saved = cart(completed)
    assert saved["id"] == cart(proposal)["id"] and saved["status"] == "completed"
    assert order_count(db_session) == 1 and pants[3].stock_quantity == 2
    assert cart(checkout("oui"))["order_id"] == saved["order_id"]
    # Retry the same committed inbound through the real background worker.
    from app.integrations.whatsapp.schemas import ReplyTarget
    from app.services.whatsapp_service import send_automatic_reply
    inbound_id = UUID(completed.metadata_["in_reply_to"])
    inbound = db_session.get(Message, inbound_id)
    target = ReplyTarget(conversation_id=completed.conversation_id, inbound_id=inbound_id,
        phone_number="212600001111")
    send_automatic_reply(target, checkout.sender, checkout.catalog.sessions, inbound.content, checkout.service)
    assert order_count(db_session) == 1 and pants[3].stock_quantity == 2
    assert db_session.scalar(select(func.count()).select_from(InventoryMovement)) == 1
    assert db_session.get(Order, UUID(saved["order_id"])).source_cart_id == UUID(saved["id"])
