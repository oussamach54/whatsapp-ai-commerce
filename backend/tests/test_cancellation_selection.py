"""Real copied-option regression, bounded snapshots and delivered selection authority."""
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound

from app.ai.order_cancellation import choice_summary, order_summary
from app.models import Message, Order, OrderItem
from app.models.enums import OrderStatus
from tests.test_cod_checkout import checkout, cart
from tests.test_commerce_conversations import dialogue, refs
from tests.test_catalog_variant_followups import pants
from tests.test_order_cancellation import completed, restored, foreign_order
from tests.test_multimodal_commerce import multimodal, observation, URL


@pytest.fixture
def choices(checkout, db_session):
    for _ in range(3):
        completed(checkout, db_session)
    row = checkout("non annulé l commande")
    ids = row.metadata_["commerce_state"]["cancellation"]["order_ids"]
    return row, [db_session.get(Order, UUID(oid)) for oid in ids]


@pytest.mark.parametrize("selection", ["3", "la 3", "numéro 3", "third one", "order_number", "copy"])
def test_selection_resolves_only_third_presented_order(choices, checkout, db_session, pants, selection):
    prompt, orders = choices
    text = orders[2].order_number if selection == "order_number" else prompt.content.split("\n\n")[3] if selection == "copy" else selection
    before = pants[1].stock_quantity
    calls = checkout.service._client.respond.call_count
    selected = checkout(text)
    pending = selected.metadata_["commerce_state"]["cancellation"]
    assert pending["order_ids"] == [str(orders[2].id)]
    assert pending["status"] == "awaiting_confirmation"
    assert pending["id"] != prompt.metadata_["commerce_state"]["cancellation"]["id"]
    assert selected.metadata_["confirmation_prompt"]["action"] == "order_cancellation"
    assert orders[2].order_number in selected.content and "1× Pantalon Classic" in selected.content
    assert all(db_session.get(Order, order.id).status == OrderStatus.CONFIRMED for order in orders)
    assert pants[1].stock_quantity == before and not restored(db_session, orders[2])
    assert cart(selected) == cart(prompt) and refs(selected) == refs(prompt)
    assert checkout.service._client.respond.call_count == calls
    cancelled = checkout("oui")
    for i, order in enumerate(orders):
        db_session.refresh(order)
        assert order.status == (OrderStatus.CANCELLED if i == 2 else OrderStatus.CONFIRMED)
    assert pants[1].stock_quantity == before + 1
    assert len(restored(db_session, orders[2])) == 1
    assert cart(cancelled) == cart(prompt)
    checkout("oui")
    assert pants[1].stock_quantity == before + 1 and len(restored(db_session, orders[2])) == 1


def test_exact_legacy_copied_row_replays_real_incident(choices, checkout, db_session):
    prompt, orders = choices
    # Test-only replay of the old successfully delivered list format.
    rows = [f"{i}. {order.order_number} — {order.subtotal:.2f} MAD — {order.created_at.date()}"
            for i, order in enumerate(orders, 1)]
    prompt.content = "\n".join(rows) + "\nQuelle commande souhaitez-vous annuler ?"
    db_session.flush()
    row = checkout(rows[2])
    assert row.metadata_["commerce_state"]["cancellation"]["order_ids"] == [str(orders[2].id)]
    assert orders[2].order_number in row.content and "confirmation_prompt" in row.metadata_
    assert all(db_session.get(Order, order.id).status == OrderStatus.CONFIRMED for order in orders)


@pytest.mark.parametrize("invalid", ["0", "4", "99", "9999999999999999999999999999999", "fourth one", "uuid", "foreign", "unpresented", "conflicting_copy", "altered_copy"])
def test_invalid_choices_reask_only_original_candidate_set(choices, checkout, db_session, invalid):
    prompt, orders = choices
    if invalid == "uuid":
        text = str(orders[2].id)
    elif invalid in ("foreign", "unpresented"):
        extra = foreign_order(db_session)
        if invalid == "unpresented":
            extra.customer_id = orders[0].customer_id
            db_session.flush()
        text = extra.order_number
    elif invalid == "conflicting_copy":
        text = prompt.content.split("\n\n")[3].replace("3. ", "1. ", 1)
    elif invalid == "altered_copy":
        text = prompt.content.split("\n\n")[3].replace("249.00", "0.01")
    else:
        text = invalid
    row = checkout(text)
    pending = row.metadata_["commerce_state"]["cancellation"]
    assert pending["status"] == "choosing"
    assert pending["order_ids"] == [str(order.id) for order in orders]
    assert "confirmation_prompt" not in row.metadata_
    assert cart(row) == cart(prompt) and refs(row) == refs(prompt)
    assert all(db_session.get(Order, order.id).status == OrderStatus.CONFIRMED for order in orders)
    assert all(not restored(db_session, order) for order in orders)
    # The new list is itself a delivered retry context.
    assert checkout("3").metadata_["commerce_state"]["cancellation"]["order_ids"] == [str(orders[2].id)]


def test_selection_bypasses_catalog_attributes_semantics_and_classifier(choices, checkout):
    prompt, orders = choices
    copied = prompt.content.split("\n\n")[3]
    with patch("app.ai.classifier.OpenAIClassifier.classify", side_effect=AssertionError("no classifier")), \
         patch("app.ai.catalog_orchestrator._run_catalog", side_effect=AssertionError("no semantic catalog")), \
         patch("app.services.catalog_service.CatalogService.get", side_effect=AssertionError("no product lookup")), \
         patch("app.services.catalog_service.CatalogService.search", side_effect=AssertionError("no product search")):
        row = checkout(copied)
    assert row.metadata_["commerce_state"]["cancellation"]["order_ids"] == [str(orders[2].id)]
    assert row.metadata_["commerce_state"]["operation"] == "order_cancel"


def test_successful_list_preserves_durable_candidate_order(choices, db_session):
    prompt, orders = choices
    origin = db_session.get(Message, UUID(prompt.metadata_["in_reply_to"]))
    stored = origin.metadata_["cancellation_state"]
    assert stored == prompt.metadata_["commerce_state"]["cancellation"]
    assert stored["status"] == "choosing" and stored["order_ids"] == [str(order.id) for order in orders]
    assert prompt.external_message_id and prompt.metadata_["turn_status"] == "current"


@pytest.mark.parametrize("failure", ["undelivered", "expired", "wrong_context", "changed_owner", "send"])
def test_invalid_list_context_cannot_authorize_selection(choices, checkout, db_session, failure):
    prompt, orders = choices
    if failure == "undelivered":
        prompt.external_message_id = ""
    elif failure == "expired":
        origin = db_session.get(Message, UUID(prompt.metadata_["in_reply_to"]))
        data = dict(origin.metadata_["cancellation_state"])
        data["offered_at"] = (datetime.fromisoformat(data["offered_at"]) - timedelta(minutes=16)).isoformat()
        origin.metadata_ = dict(origin.metadata_, cancellation_state=data)
    elif failure == "wrong_context":
        state = dict(prompt.metadata_["commerce_state"])
        state["cancellation"] = dict(state["cancellation"], order_ids=list(reversed(state["cancellation"]["order_ids"])))
        prompt.metadata_ = dict(prompt.metadata_, commerce_state=state)
    elif failure == "changed_owner":
        other = foreign_order(db_session)
        orders[2].customer_id = other.customer_id
    else:
        from app.integrations.whatsapp.client import WhatsAppAPIError
        original = checkout.sender.send_text_message.side_effect
        checkout.sender.send_text_message.side_effect = WhatsAppAPIError("simulated")
        with pytest.raises(NoResultFound):
            checkout("non annulé l commande")
        checkout.sender.send_text_message.side_effect = original
        failed = db_session.scalar(select(Message).where(Message.content == "non annulé l commande").order_by(Message.created_at.desc()))
        failed.created_at -= timedelta(seconds=.25)
    db_session.flush()
    row = checkout("3")
    assert "confirmation_prompt" not in row.metadata_
    assert row.metadata_["commerce_state"]["cancellation"]["status"] != "awaiting_confirmation"
    assert all(db_session.get(Order, order.id).status == OrderStatus.CONFIRMED for order in orders)
    assert all(not restored(db_session, order) for order in orders)


@pytest.mark.parametrize("style", ["french", "english", "darija_latin", "darija_arabic"])
def test_list_uses_historical_item_snapshots_and_order_totals(choices, db_session, pants, style):
    _, orders = choices
    orders[0].items[0].product_name_snapshot = "Parfum Élégance"
    orders[0].items[0].variant_name_snapshot = "Standard"
    orders[0].items[0].quantity = 2
    orders[0].total = Decimal("507.25")
    orders[0].shipping_cost = Decimal("9.25")
    pants[0].name = "CURRENT NAME MUST NOT APPEAR"
    pants[1].name = "CURRENT VARIANT MUST NOT APPEAR"
    pants[1].price = Decimal("9999")
    db_session.flush()
    text = choice_summary(orders, style)
    assert "2× Parfum Élégance — Standard" in text
    assert "Noir / M" in text and "507.25 MAD" in text
    assert "CURRENT" not in text and "9999" not in text
    for order in orders:
        assert order.order_number in text and order.created_at.strftime("%d/%m/%Y") in text
        assert str(order.id) not in text and str(order.customer_id) not in text
    assert len(text) < 3000


def test_missing_variant_and_long_snapshot_names_are_bounded(choices):
    _, orders = choices
    orders[0].items[0].product_name_snapshot = ("Very long historical product name " * 8)[:255]
    orders[0].items[0].variant_name_snapshot = ""
    text = choice_summary(orders, "english")
    assert "Very long historical product name" in text and "…" in text
    assert "None" not in text and "Standard" not in text
    assert max(map(len, text.splitlines())) < 220
    assert len(text) < 3000


def test_multi_item_and_large_orders_show_bounded_snapshot_previews(choices, db_session, pants):
    _, orders = choices
    order = orders[0]
    for i in range(20):
        db_session.add(OrderItem(order=order, product_variant_id=pants[1].id,
            product_name_snapshot=f"Historical item {i}", variant_name_snapshot=f"Stored variant {i}",
            sku_snapshot=uuid4().hex, quantity=2, unit_price=Decimal("10"), line_total=Decimal("20")))
    db_session.flush()
    text = choice_summary(orders, "english")
    first = text.split("\n\n")[0]
    assert first.count("   - ") == 4  # Three preview lines plus remaining-item notice.
    assert "+ 18 more items." in first
    assert any(f"Historical item {i}" in first for i in range(20))
    assert len(text) < 3000
    summary = order_summary(order, "english")
    assert "+ 15 more items." in summary and len(summary) < 1600
    assert order.order_number in summary and "249.00 MAD" in summary


def test_selection_then_decline_preserves_orders_and_checkout(choices, checkout, db_session):
    prompt, orders = choices
    checkout("3")
    row = checkout("non")
    assert cart(row) == cart(prompt) and refs(row) == refs(prompt)
    assert all(db_session.get(Order, order.id).status == OrderStatus.CONFIRMED for order in orders)


@pytest.mark.parametrize("failure", ["send", "expired"])
def test_selected_confirmation_requires_delivery_and_fresh_context(choices, checkout, db_session, failure):
    _, orders = choices
    if failure == "send":
        from app.integrations.whatsapp.client import WhatsAppAPIError
        original = checkout.sender.send_text_message.side_effect
        checkout.sender.send_text_message.side_effect = WhatsAppAPIError("simulated")
        with pytest.raises(NoResultFound):
            checkout("3")
        checkout.sender.send_text_message.side_effect = original
        origin = db_session.scalar(select(Message).where(Message.content == "3").order_by(Message.created_at.desc()))
        origin.created_at -= timedelta(seconds=.25)
    else:
        selected = checkout("3")
        origin = db_session.get(Message, UUID(selected.metadata_["in_reply_to"]))
        data = dict(origin.metadata_["cancellation_state"])
        data["offered_at"] = (datetime.fromisoformat(data["offered_at"]) - timedelta(minutes=16)).isoformat()
        origin.metadata_ = dict(origin.metadata_, cancellation_state=data)
    db_session.flush()
    checkout("oui")
    assert all(db_session.get(Order, order.id).status == OrderStatus.CONFIRMED for order in orders)
    assert all(not restored(db_session, order) for order in orders)


@pytest.mark.parametrize("question", ["kayn f coton?", "kayn f stock?"])
def test_normal_catalog_question_resumes_and_supersedes_selection(choices, checkout, db_session, question):
    _, orders = choices
    row = checkout(question)
    assert row.metadata_["commerce_state"]["operation"] != "order_cancel"
    assert row.metadata_["commerce_state"]["cancellation"]["status"] == "superseded"
    checkout("oui")
    assert all(db_session.get(Order, order.id).status == OrderStatus.CONFIRMED for order in orders)


@pytest.mark.parametrize("question", ["kayn f coton?", "kayn f stock?"])
def test_new_question_after_selection_supersedes_cancellation_confirmation(choices, checkout, db_session, question):
    _, orders = choices
    checkout("3")
    checkout(question)
    checkout("oui")
    assert all(db_session.get(Order, order.id).status == OrderStatus.CONFIRMED for order in orders)
    assert all(not restored(db_session, order) for order in orders)


def test_selection_consent_does_not_confirm_unrelated_cart_or_change_its_focus(choices, checkout, db_session, pants):
    _, orders = choices
    pending_cart_row = checkout("je veux commander pantalon bleu M")
    pending_cart, focus = cart(pending_cart_row), refs(pending_cart_row)
    prompt = checkout("non annulé l commande")
    selected_id = UUID(prompt.metadata_["commerce_state"]["cancellation"]["order_ids"][2])
    selected = checkout("third one")
    assert cart(selected) == pending_cart and refs(selected) == focus
    row = checkout("oui")
    assert cart(row) == pending_cart and refs(row) == focus
    assert db_session.get(Order, selected_id).status == OrderStatus.CANCELLED
    assert pants[3].stock_quantity == 3


def test_affirmative_to_choosing_list_is_not_cancellation_or_checkout_consent(choices, checkout, db_session):
    prompt, orders = choices
    row = checkout("oui")
    assert cart(row) == cart(prompt)
    assert all(db_session.get(Order, order.id).status == OrderStatus.CONFIRMED for order in orders)
    assert all(not restored(db_session, order) for order in orders)


@pytest.mark.parametrize("status", [OrderStatus.SHIPPED, OrderStatus.CANCELLED])
def test_selection_rechecks_changed_eligibility(choices, checkout, db_session, status):
    _, orders = choices
    orders[2].status = status
    db_session.flush()
    row = checkout("3")
    assert "confirmation_prompt" not in row.metadata_
    assert db_session.get(Order, orders[2].id).status == status and not restored(db_session, orders[2])


@pytest.mark.parametrize("source", ["image", "link"])
def test_image_link_cannot_confirm_selected_order(choices, multimodal, db_session, source):
    _, orders = choices
    multimodal.checkout("3")
    if source == "image":
        multimodal("oui", observation("oui", intent="purchase", speech_act="affirmative"))
    else:
        multimodal("oui " + URL, image=False)
    multimodal.checkout("oui")
    assert all(db_session.get(Order, order.id).status == OrderStatus.CONFIRMED for order in orders)
    assert all(not restored(db_session, order) for order in orders)
