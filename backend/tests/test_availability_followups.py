"""Verified multimodal follow-ups through persisted WhatsApp state; providers mocked."""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app.ai.conversation_engine import STOCK
from tests.test_cod_checkout import cart, order_count
from tests.test_commerce_conversations import refs
from tests.test_multimodal_commerce import multimodal, observation
from tests.test_cod_checkout import checkout
from tests.test_commerce_conversations import dialogue
from tests.test_catalog_variant_followups import pants


def image_focus(multimodal):
    caption = "wach endkom pantalon bhal hada"
    return multimodal(caption, observation(caption, language="darija_latin"))


def test_image_price_followup_reads_fresh_db(multimodal, pants, db_session):
    first = image_focus(multimodal)
    assert refs(first)["selection"] is None and not cart(first)
    pants[3].price = Decimal("237.00")
    db_session.flush()
    calls = multimodal.checkout.service._client.respond.call_count
    row = multimodal.checkout("bchhal hada")
    assert row.content.splitlines()[0] == "237.00 MAD."
    assert refs(row) == refs(first)
    assert row.metadata_["commerce_state"]["operation"] == "price"
    assert multimodal.checkout.service._client.respond.call_count == calls


@pytest.mark.parametrize("question,expected", [
    ("kayn f stock?", "kayn f stock"),
    ("wach mazal kayn?", "kayn f stock"),
    ("disponible?", "En stock"),
    ("est-ce que disponible?", "En stock"),
    ("is it in stock?", "In stock"),
    ("is it still available?", "In stock"),
])
def test_image_price_then_availability(multimodal, pants, db_session, question, expected):
    first = image_focus(multimodal)
    assert multimodal.checkout("bchhal hada").content.splitlines()[0] == "229.00 MAD."
    calls = multimodal.checkout.service._client.respond.call_count
    row = multimodal.checkout(question)
    assert expected.lower() in row.content.lower()
    assert row.metadata_["commerce_state"]["operation"] == "stock"
    assert row.metadata_["commerce_state"]["pending"] is None
    assert refs(row) == refs(first)
    assert refs(row)["focus"]["variant_id"] == str(pants[3].id)
    assert not cart(row) and order_count(db_session) == 0
    assert multimodal.checkout.service._client.respond.call_count == calls


def test_availability_without_focus_clarifies(multimodal, db_session):
    row = multimodal.checkout("is it in stock?")
    assert row.content == "Which product do you mean?"
    assert refs(row)["focus"] is None and refs(row)["selection"] is None
    assert not cart(row) and order_count(db_session) == 0
    multimodal.checkout.service._client.respond.assert_not_called()


@pytest.mark.parametrize("failure", ["stale", "failed_link", "inactive_variant"])
def test_unusable_reference_never_supplies_stock(multimodal, pants, db_session, failure):
    first = image_focus(multimodal)
    if failure == "stale":
        data = dict(first.metadata_)
        state = dict(data["commerce_state"])
        state["commercial_at"] = (datetime.fromisoformat(state["commercial_at"]) - timedelta(minutes=16)).isoformat()
        data["commerce_state"] = state
        first.metadata_ = data
        db_session.flush()
    elif failure == "failed_link":
        multimodal("https://shop.example.com/products/missing", image=False)
    else:
        pants[3].is_active = False
        db_session.flush()
    calls = multimodal.checkout.service._client.respond.call_count
    row = multimodal.checkout("is it in stock?")
    assert "in stock" not in row.content.lower() and "229.00" not in row.content
    assert not cart(row) and order_count(db_session) == 0
    assert multimodal.checkout.service._client.respond.call_count == calls


def test_stock_change_between_followups_is_fresh(multimodal, pants, db_session):
    image_focus(multimodal)
    assert "stock" in multimodal.checkout("is it in stock?").content.lower()
    pants[3].stock_quantity = 0
    db_session.flush()
    assert "out of stock" in multimodal.checkout("is it in stock?").content.lower()
    pants[3].stock_quantity = 7
    db_session.flush()
    restored = multimodal.checkout("is it in stock?")
    assert "in stock" in restored.content.lower()
    assert "out of stock" not in restored.content.lower()


def test_stock_question_cannot_confirm_pending_cart(multimodal, db_session):
    offered = multimodal("je veux commander ce pantalon", observation("je veux commander ce pantalon", intent="purchase"))
    assert cart(offered)["status"] == "awaiting_confirmation"
    row = multimodal.checkout("kayn f stock?")
    assert "stock" in row.content
    assert cart(row)["id"] == cart(offered)["id"]
    assert cart(row)["status"] == "awaiting_confirmation"
    assert cart(row)["confirmation_message_id"] is None
    assert refs(row)["selection"] == refs(offered)["selection"]
    assert order_count(db_session) == 0


@pytest.mark.parametrize("text", ["kayn f coton?", "disponible en rose?", "stock material?", "is it available in cotton?", "yes, in stock?"])
def test_unknown_attributes_and_confirmation_are_not_availability_shortcuts(text):
    assert STOCK.fullmatch(text) is None
