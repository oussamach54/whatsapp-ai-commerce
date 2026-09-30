"""COD through the real PostgreSQL/WhatsApp pipeline, with all network calls mocked."""
from decimal import Decimal
from uuid import UUID
from unittest.mock import patch

import pytest
from sqlalchemy import select, func, event

from app.ai.catalog_schemas import CustomerIntent, ResponsePlan
from app.ai.schemas import ProviderResult, ScopeDecision
from app.models import Order, OrderItem, InventoryMovement, Message
from tests.test_commerce_conversations import dialogue, refs
from tests.test_catalog_variant_followups import pants


@pytest.fixture
def checkout(dialogue, catalog_env, db_session, monkeypatch):
    from app.services.whatsapp_service import send_automatic_reply
    def after_inbound_commit(*args, **kwargs):
        # Real webhook persistence commits before background order processing.
        db_session.commit()
        return send_automatic_reply(*args, **kwargs)
    monkeypatch.setattr("tests.test_commerce_conversations.send_automatic_reply", after_inbound_commit)
    monkeypatch.setattr("app.ai.classifier.OpenAIClassifier.classify", lambda *args:
        ScopeDecision(scope="commerce", intent="purchase", language_style="french", security_action="none"))
    catalog_env[1].conversation.customer.phone_number = "212600001111"
    db_session.flush()
    return dialogue


def cart(row):
    return row.metadata_["commerce_state"]["cart"]


def item(color="noir", quantity=1, size="M", terms=None):
    return dict(reference="none", terms=terms or [], quantity=quantity,
                attributes=[{"name": "color", "value": color}, {"name": "size", "value": size}])


def plan(text, intent="purchase", items=None, checkout=None, **kwargs):
    data = dict(intent=intent, language="french", reference="focus", speech_act="affirmative",
                purchase_intent=intent == "purchase", evidence=text, items=items or [], checkout=checkout or [])
    data.update(kwargs)
    return ProviderResult(ResponsePlan(action="show", items=[], question=None, reference="none",
        customer_intent=CustomerIntent.model_validate(data)).model_dump_json())


DETAILS = "Oussama, Casablanca, 12 rue Test"
FIELDS = [{"name": "customer_name", "value": "Oussama"}, {"name": "city", "value": "Casablanca"},
          {"name": "address", "value": "12 rue Test"}]


def supply(checkout):
    return checkout(DETAILS, [plan(DETAILS, "checkout", checkout=FIELDS)])


def multi(checkout, text="bghit 2 pantalon noir M et 1 bleu M"):
    return checkout(text, [plan(text, items=[item(quantity=2, terms=["pantalon"]), item("bleu")])])


def order_count(db):
    return db.scalar(select(func.count()).select_from(Order))


def test_real_single_flow(checkout, pants, db_session):
    text = "salam bghet n commandé pantalon noir taille M"
    row = checkout(text, [plan(text, items=[item(terms=["pantalon"])])])
    assert "249.00 MAD" in row.content and "?" in row.content
    assert order_count(db_session) == 0 and pants[1].stock_quantity == 5
    row = checkout("oui")
    assert cart(row)["status"] == "confirmed"
    assert ("nom" in row.content or "smiya" in row.content) and "ville" in row.content and "adresse" in row.content
    assert "téléphone" not in row.content
    assert order_count(db_session) == 0
    row = supply(checkout)
    assert cart(row)["status"] == "completed" and "confirmée" in row.content
    assert "Step" not in row.content and "intent" not in row.content and "Mazal" not in row.content
    order = db_session.get(Order, UUID(cart(row)["order_id"]))
    assert order.shipping_phone_number == "212600001111"
    assert order.shipping_full_name == "Oussama" and order.shipping_city == "Casablanca"
    assert order.shipping_address_line == "12 rue Test" and order.subtotal == Decimal("249.00")
    assert order.shipping_cost is None and order.total is None
    assert order.payment_method == "cod" and order.status == "confirmed"
    assert pants[1].stock_quantity == 4
    assert db_session.scalar(select(func.count()).select_from(InventoryMovement)) == 1
    assert cart(checkout("oui"))["order_id"] == str(order.id)
    assert order_count(db_session) == 1 and pants[1].stock_quantity == 4


@pytest.mark.parametrize("text", ["ila khdet l pantalon noir 2 et 1 bleu taille M",
    "bghit 2 noir M w wahed bleu M", "ila khdet pantalon noir 2 et 1 bleu taille M",
    "je veux deux noirs M et un bleu M", "2 black medium and 1 blue medium",
    "give me two black ones and one blue", "2 noir et 1 bleu", "جوج كحلين و واحد زرق"])
def test_multi_equivalence(checkout, pants, db_session, text):
    checkout("je cherche pantalon noir taille M")
    row = multi(checkout, text)
    assert len(cart(row)["items"]) == 2
    assert "498.00 MAD" in row.content and "229.00 MAD" in row.content and "727.00 MAD" in row.content
    assert order_count(db_session) == 0
    checkout("oui")
    row = supply(checkout)
    order = db_session.get(Order, UUID(cart(row)["order_id"]))
    assert len(order.items) == 2
    assert {i.product_variant_id: i.quantity for i in order.items} == {pants[1].id: 2, pants[3].id: 1}
    assert order.subtotal == Decimal("727.00")
    assert pants[1].stock_quantity == 3 and pants[3].stock_quantity == 2


def test_same_message_checkout_and_only_missing_address(checkout, pants, db_session):
    text = "bghit 2 pantalon noir M, smiti Oussama, Casablanca"
    row = checkout(text, [plan(text, items=[item(quantity=2, terms=["pantalon"])], checkout=FIELDS[:2])])
    assert set(cart(row)["fields"]) == {"phone", "customer_name", "city"}
    row = checkout("oui")
    assert "adresse" in row.content and "ville" not in row.content and "nom" not in row.content
    calls = checkout.service._client.respond.call_count
    row = checkout("12 rue Test")
    assert cart(row)["status"] == "completed"
    assert checkout.service._client.respond.call_count == calls
    assert db_session.get(Order, UUID(cart(row)["order_id"])).shipping_address_line == "12 rue Test"


@pytest.mark.parametrize("required", [["phone"], ["customer_name", "phone", "city"],
    ["customer_name", "phone", "address", "delivery_note"]])
def test_configurable_fields_and_all_known(checkout, catalog_env, db_session, required):
    catalog_env[0].settings.checkout_required_fields = required
    text = "I want pantalon noir M, Oussama, Casablanca, 12 rue Test, sonnette 2"
    fields = FIELDS + [{"name": "delivery_note", "value": "sonnette 2"}]
    checkout(text, [plan(text, items=[item(terms=["pantalon"])], checkout=fields)])
    assert order_count(db_session) == 0
    row = checkout("oui")
    assert cart(row)["status"] == "completed" and order_count(db_session) == 1


def test_optional_shipping_fields_are_null(checkout, catalog_env, db_session):
    catalog_env[0].settings.checkout_required_fields = ["phone"]
    checkout("je veux commander pantalon noir M")
    row = checkout("oui")
    order = db_session.get(Order, UUID(cart(row)["order_id"]))
    assert order.shipping_full_name is None and order.shipping_city is None and order.shipping_address_line is None


def test_decimal_snapshots_and_verified_delivery(checkout, catalog_env, pants, db_session):
    catalog_env[0].settings.checkout_required_fields = ["phone"]
    catalog_env[0].settings.checkout_shipping_cost = Decimal("5.25")
    pants[1].price, pants[3].price = Decimal("20.10"), Decimal("0.10")
    db_session.flush()
    multi(checkout)
    row = checkout("oui")
    order = db_session.get(Order, UUID(cart(row)["order_id"]))
    assert order.subtotal == Decimal("40.30") and order.total == Decimal("45.55")
    pants[1].price, pants[1].name = Decimal("999"), "Changed"
    db_session.flush()
    assert {i.unit_price for i in order.items} == {Decimal("20.10"), Decimal("0.10")}
    assert any(i.variant_name_snapshot == "Noir / M" for i in order.items)


@pytest.mark.parametrize("change", ["price", "zero", "insufficient", "product", "variant"])
@pytest.mark.parametrize("when", ["confirmation", "last_field"])
def test_fresh_verification_at_both_boundaries(checkout, pants, db_session, change, when):
    multi(checkout)
    if when == "last_field":
        checkout("oui")
    if change == "price":
        pants[1].price = Decimal("259")
    elif change == "zero":
        pants[1].stock_quantity = 0
    elif change == "insufficient":
        pants[1].stock_quantity = 1
    elif change == "product":
        pants[0].is_active = False
    else:
        pants[1].is_active = False
    db_session.flush()
    row = checkout("oui") if when == "confirmation" else supply(checkout)
    assert order_count(db_session) == 0
    if change == "price":
        assert cart(row)["status"] == "awaiting_confirmation" and "518.00 MAD" in row.content
        row = checkout("oui")
        if when == "confirmation":
            row = supply(checkout)
        assert cart(row)["status"] == "completed"
    else:
        assert cart(row)["status"] == "blocked"


@pytest.mark.parametrize("quantity,color,size", [(10, "bleu", "M"), (1, "noir", "L"), (1, "bleu", "L")])
def test_insufficient_and_nonexistent(checkout, db_session, quantity, color, size):
    checkout("je cherche pantalon noir taille M")
    text = "I want these pants please"
    row = checkout(text, [plan(text, items=[item(color, quantity, size)])])
    assert order_count(db_session) == 0
    if color == "bleu" and size == "L":
        assert "pas trouvé" in row.content
    else:
        assert cart(row)["status"] == "blocked"
        assert str(3 if color == "bleu" else 0) in row.content


@pytest.mark.parametrize("word", ["oui", "yes", "wakha", "ok", "confirme", "confirm", "nconfirmi", "iwa", "yallah"])
def test_confirmations_only_with_cart(checkout, catalog_env, db_session, word):
    catalog_env[0].settings.checkout_required_fields = ["phone"]
    checkout("je veux commander pantalon noir M")
    row = checkout(word)
    assert cart(row)["status"] == "completed"
    assert order_count(db_session) == 1
    checkout(word)
    assert order_count(db_session) == 1


@pytest.mark.parametrize("action,requests,quantities", [
    ("add", [item("bleu")], {"noir": 2, "bleu": 2}),
    ("remove", [item("bleu")], {"noir": 2}),
    ("replace", [item("bleu", 2)], {"bleu": 2}),
    ("set_quantity", [item("noir", 3)], {"noir": 3, "bleu": 1}),
    ("increase", [item("noir")], {"noir": 3, "bleu": 1}),
    ("decrease", [item("noir")], {"noir": 1, "bleu": 1}),
])
def test_cart_edits(checkout, db_session, action, requests, quantities):
    multi(checkout)
    checkout("oui")
    text = "please update my cart"
    row = checkout(text, [plan(text, "cart_edit", items=requests, cart_action=action)])
    assert cart(row)["status"] == "awaiting_confirmation"
    assert cart(row)["confirmed_version"] is None
    assert {line["variant_name"].split(" / ")[0].lower(): line["quantity"] for line in cart(row)["items"]} == quantities
    assert order_count(db_session) == 0


def test_ambiguous_edit_does_not_guess(checkout, db_session):
    multi(checkout)
    text = "make it two"
    row = checkout(text, [plan(text, "cart_edit", items=[dict(quantity=2)], cart_action="set_quantity")])
    assert "Quel article" in row.content
    assert len(cart(row)["items"]) == 2 and order_count(db_session) == 0


@pytest.mark.parametrize("text", ["cancel", "annule", "ma b9itch baghi", "khliha", "forget it"])
def test_pending_cancellation(checkout, db_session, text):
    multi(checkout)
    row = checkout(text)
    assert cart(row)["status"] == "cancelled"
    checkout("oui")
    assert order_count(db_session) == 0


@pytest.mark.parametrize("text", ["nakhdo", "okay hadak", "je le prends"])
def test_recommendation_purchase_without_repeat(checkout, pants, text):
    checkout("je cherche pantalon noir taille M")
    row = checkout("3ndi 240dh chno katnsa7ni?")
    assert "229.00 MAD" in row.content
    row = checkout(text, [plan(text, items=[dict(reference="focus", quantity=1)])])
    assert cart(row)["items"][0]["target"]["variant_id"] == str(pants[3].id)
    assert "?" in row.content


def test_real_discount_regression_with_quantity_attribute(checkout):
    checkout("je cherche pantalon noir taille M")
    text = "ila khdit 3 tn9ess lia taman?"
    row = checkout(text, [plan(text, "business_question", quantity=3, business_topic="discount",
        attributes=[{"name": "quantity", "value": "3"}])])
    assert "information confirmée" in row.content
    assert "variante" not in row.content and "%" not in row.content
    assert row.metadata_["commerce_state"]["pending"] is None


@pytest.mark.parametrize("topic", ["delivery_price", "delivery_time", "returns", "warranty"])
def test_unconfigured_policies(checkout, topic):
    checkout("je cherche pantalon noir taille M")
    text = "can you confirm your business policy?"
    row = checkout(text, [plan(text, "business_question", business_topic=topic)])
    assert "information confirmée" in row.content and "MAD" not in row.content


def test_checkout_pii_not_in_subsequent_prompt(checkout):
    text = "I want pantalon noir M, Oussama, Casablanca, 12 rue Test"
    checkout(text, [plan(text, items=[item(terms=["pantalon"])], checkout=FIELDS)])
    question = "can you confirm your business policy?"
    checkout(question, [plan(question, "business_question", business_topic="returns")])
    prompt = str(checkout.service._client.respond.call_args.args[0])
    assert "12 rue Test" not in prompt and "212600001111" not in prompt and "Oussama" not in prompt


def test_order_rollback_keeps_stock(checkout, catalog_env, pants, db_session):
    catalog_env[0].settings.checkout_required_fields = ["phone"]
    checkout("je veux commander pantalon noir M")
    def fail(*args):
        raise RuntimeError("test rollback")
    event.listen(OrderItem, "before_insert", fail)
    try:
        row = checkout("oui")
    finally:
        event.remove(OrderItem, "before_insert", fail)
    assert "confirmée" not in row.content
    assert order_count(db_session) == 0 and pants[1].stock_quantity == 5


def test_multi_item_failure_rolls_back_every_line(checkout, catalog_env, pants, db_session):
    catalog_env[0].settings.checkout_required_fields = ["phone"]
    multi(checkout)
    def fail_second_line(mapper, connection, target):
        if target.product_variant_id == pants[3].id:
            raise RuntimeError("second order line failed")
    event.listen(OrderItem, "before_insert", fail_second_line)
    try:
        row = checkout("oui")
    finally:
        event.remove(OrderItem, "before_insert", fail_second_line)
    assert "confirmée" not in row.content
    assert order_count(db_session) == 0
    assert db_session.scalar(select(func.count()).select_from(OrderItem)) == 0
    assert db_session.scalar(select(func.count()).select_from(InventoryMovement)) == 0
    assert pants[1].stock_quantity == 5 and pants[3].stock_quantity == 3


def test_shipping_change_requires_new_confirmation(checkout, catalog_env, db_session):
    catalog_env[0].settings.checkout_required_fields = ["phone"]
    multi(checkout)
    catalog_env[0].settings.checkout_shipping_cost = Decimal("20.00")
    row = checkout("oui")
    assert cart(row)["status"] == "awaiting_confirmation"
    assert "747.00 MAD" in row.content and order_count(db_session) == 0
    assert cart(checkout("oui"))["status"] == "completed"


def test_send_failure_after_commit_cannot_duplicate_order(checkout, catalog_env, pants, db_session):
    from app.integrations.whatsapp.client import WhatsAppAPIError
    from sqlalchemy.exc import NoResultFound
    catalog_env[0].settings.checkout_required_fields = ["phone"]
    checkout("je veux commander pantalon noir M")
    original = checkout.sender.send_text_message.side_effect
    checkout.sender.send_text_message.side_effect = WhatsAppAPIError("simulated send failure")
    with pytest.raises(NoResultFound):
        checkout("oui")
    assert order_count(db_session) == 1 and pants[1].stock_quantity == 4
    checkout.sender.send_text_message.side_effect = original
    # Use a genuinely later receipt: the fixture's previous outbound wasn't sent.
    for row in db_session.scalars(select(Message).where(Message.content == "oui")):
        row.created_at = row.created_at.replace(microsecond=0)
    db_session.flush()
    row = checkout("oui")
    assert cart(row)["status"] == "completed"
    assert order_count(db_session) == 1 and pants[1].stock_quantity == 4


def test_no_order_without_confirmation_even_with_details(checkout, db_session):
    text = "pantalon noir M Oussama Casablanca 12 rue Test"
    checkout(text, [plan(text, items=[item(terms=["pantalon"])], checkout=FIELDS)])
    assert order_count(db_session) == 0


@pytest.mark.parametrize("value", ["", "  ", "not a phone", "+12", "9" * 40])
def test_invalid_phone_values(value):
    from app.services.checkout_service import clean_field
    with pytest.raises(ValueError):
        clean_field("phone", value)


@pytest.mark.parametrize("quantity", [0, -1, 1.5, True, 100])
def test_invalid_semantic_quantities(quantity):
    from app.ai.catalog_schemas import RequestedItem
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        RequestedItem(quantity=quantity)


def test_unprovided_checkout_values_are_rejected(checkout):
    text = "I want pantalon noir M"
    row = checkout(text, [plan(text, items=[item(terms=["pantalon"])], checkout=FIELDS)])
    assert set(cart(row)["fields"]) == {"phone"}


def test_cancellation_after_creation_does_not_cancel_order(checkout, catalog_env, db_session):
    catalog_env[0].settings.checkout_required_fields = ["phone"]
    checkout("je veux commander pantalon noir M")
    created = checkout("oui")
    row = checkout("annule")
    assert "annulée" not in row.content
    assert db_session.get(Order, UUID(cart(created)["order_id"])).status == "confirmed"


def test_checkout_control_metadata_cannot_be_forged(client, catalog_env):
    for key in ("checkout_state", "checkout_private"):
        response = client.post(f"/api/conversations/{catalog_env[1].conversation_id}/messages", json={
            "direction": "inbound", "sender_type": "customer", "message_type": "text", "content": "oui",
            "metadata": {key: {"status": "confirmed"}}})
        assert response.status_code == 422
