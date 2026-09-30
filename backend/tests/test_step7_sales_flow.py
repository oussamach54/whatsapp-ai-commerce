"""Real router/catalog/checkout with mocked semantics and WhatsApp transport."""
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select, func

from app.models import Order, InventoryMovement
from tests.test_cod_checkout import checkout, cart, item, plan, FIELDS, DETAILS, supply, order_count
from tests.test_commerce_conversations import dialogue
from tests.test_catalog_variant_followups import pants


@pytest.mark.parametrize("with_items", [False, True])
def test_incident_search_with_explicit_purchase_signal(checkout, pants, with_items):
    text = "salam bghet n commandé pantalon noir taille M"
    row = checkout(text, [plan(text, "product_search", purchase_intent=True, reference="none",
        terms=["pantalon"], attributes=item()["attributes"],
        items=[item(terms=["pantalon"])] if with_items else [])])
    assert cart(row) and cart(row)["status"] == "awaiting_confirmation"
    assert "249.00 MAD" in row.content and "?" in row.content
    assert cart(row)["items"][0]["target"]["variant_id"] == str(pants[1].id)
    row = checkout("oui")
    assert cart(row)["status"] == "confirmed"
    assert "adresse" in row.content and "téléphone" not in row.content


@pytest.mark.parametrize("text,style", [
    ("bghit n commandé pantalon noir M", "darija_latin"),
    ("bghit nchri pantalon noir M", "darija_latin"),
    ("nakhod pantalon noir M", "darija_latin"),
    ("je veux commander le pantalon noir M", "french"),
    ("je veux acheter le noir en M", "french"),
    ("je prends le noir M", "french"),
    ("I want to order the black one in M", "english"),
    ("I want to buy the black M", "english"),
    ("I'll take the black M", "english"),
    ("salam je veux acheter pantalon noir M", "mixed"),
])
def test_purchase_equivalents(checkout, pants, text, style):
    checkout("je cherche pantalon noir taille M")
    row = checkout(text, [plan(text, "product_search", purchase_intent=True,
        language=style, reference="focus", attributes=item()["attributes"])])
    assert cart(row) and cart(row)["status"] == "awaiting_confirmation"
    assert "249.00 MAD" in row.content and "?" in row.content


@pytest.mark.parametrize("speech", ["negative", "question", "hypothetical", "quoted"])
def test_purchase_signal_does_not_bypass_speech_guard(checkout, speech):
    text = "could I buy the black medium?"
    row = checkout(text, [plan(text, "product_search", purchase_intent=True, speech_act=speech,
        reference="none", terms=["pantalon"], attributes=item()["attributes"])])
    assert not cart(row)


def test_availability_stays_availability(checkout):
    text = "wach pantalon noir M kayn?"
    row = checkout(text, [plan(text, "availability", reference="none", terms=["pantalon"],
        attributes=item()["attributes"], speech_act="question")])
    assert "249.00 MAD" in row.content and not cart(row)


@pytest.mark.parametrize("size,color", [("L", "noir"), ("L", "bleu"), (None, None)])
def test_unpurchasable_or_ambiguous_target_never_offers(checkout, size, color):
    text = "I would like to buy these pants please"
    row = checkout(text, [plan(text, "product_search", purchase_intent=True,
        reference="none", terms=["pantalon"], attributes=item(color=color, size=size)["attributes"] if size else [])])
    assert not cart(row) or cart(row)["status"] != "awaiting_confirmation"
    assert "Confirmez-vous" not in row.content


WEBSITE_REQUESTS = [
    ("salam bghet n commandé f site ms makhdamch", "darija_latin", "directement hna"),
    ("site ma khdamch", "darija_latin", "directement hna"),
    ("ma9dertch ndwz commande f site", "darija_latin", "directement hna"),
    ("le site ne marche pas", "french", "directement ici"),
    ("j'arrive pas à commander sur le site", "french", "directement ici"),
    ("checkout kaybloqui", "mixed", "directement hna"),
    ("payment/commande page ma khdamach", "mixed", "directement hna"),
    ("the website isn't working", "english", "directly here"),
    ("I can't place my order online", "english", "directly here"),
    ("I would rather order here than on the website", "english", "directly here"),
]


@pytest.mark.parametrize("text,style,expected", WEBSITE_REQUESTS)
def test_website_without_product_offers_same_channel(checkout, db_session, text, style, expected):
    row = checkout(text, [plan(text, "website_ordering", reference="none", language=style)])
    assert expected in row.content and "?" in row.content
    assert not cart(row) and order_count(db_session) == 0
    assert all(word not in row.content.lower() for word in ("outage", "maintenance", "panne", "server", "réessayez", "try again"))
    # Product-only answer inherits the ordering invitation through normal Pending.
    row = checkout("pantalon noir M")
    assert cart(row)["status"] == "awaiting_confirmation"
    assert "249.00 MAD" in row.content and "?" in row.content


@pytest.mark.parametrize("quantity", [1, 2])
def test_website_with_product_quantity_and_details(checkout, pants, db_session, quantity):
    text = f"site ma khdamch, bghit {quantity} pantalon noir M, Oussama, Casablanca, 12 rue Test"
    row = checkout(text, [plan(text, "website_ordering", reference="none",
        items=[item(quantity=quantity, terms=["pantalon"])], checkout=FIELDS)])
    assert cart(row)["items"][0]["quantity"] == quantity
    assert set(cart(row)["fields"]) == {"customer_name", "phone", "city", "address"}
    assert "Que souhaitez" not in row.content and "?" in row.content
    created = checkout("oui")
    assert cart(created)["status"] == "completed"
    order = db_session.get(Order, UUID(cart(created)["order_id"]))
    assert order.shipping_phone_number == "212600001111" and order.shipping_address_line == "12 rue Test"
    assert order.subtotal == Decimal("249.00") * quantity
    assert pants[1].stock_quantity == 5 - quantity
    assert cart(checkout("oui"))["order_id"] == str(order.id)
    assert order_count(db_session) == 1
    assert db_session.scalar(select(func.count()).select_from(InventoryMovement)) == 1


def test_website_multi_item_uses_atomic_cod_engine(checkout, pants, db_session):
    text = "site ma khdamch, bghit 2 pantalon noir M et wahed bleu M"
    row = checkout(text, [plan(text, "website_ordering", reference="none",
        items=[item(quantity=2, terms=["pantalon"]), item("bleu")])])
    assert len(cart(row)["items"]) == 2 and "727.00 MAD" in row.content
    missing = checkout("oui")
    assert "adresse" in missing.content and "téléphone" not in missing.content
    row = supply(checkout)
    order = db_session.get(Order, UUID(cart(row)["order_id"]))
    assert len(order.items) == 2 and order.subtotal == Decimal("727")
    assert pants[1].stock_quantity == 3 and pants[3].stock_quantity == 2


def test_website_partial_fields_asks_only_address(checkout):
    text = "site ma khdamch pantalon noir M, Oussama, Casablanca"
    checkout(text, [plan(text, "website_ordering", reference="none", items=[item(terms=["pantalon"])], checkout=FIELDS[:2])])
    row = checkout("oui")
    assert "adresse" in row.content and "ville" not in row.content and "nom" not in row.content
    before = checkout.service._client.respond.call_count
    row = checkout("12 rue Test")
    assert cart(row)["status"] == "completed"
    assert checkout.service._client.respond.call_count == before


def policy_reply(checkout, topic="delivery_time", text="how long does delivery take?", **kwargs):
    return checkout(text, [plan(text, "business_question", business_topic=topic,
        attributes=[], language="english", **kwargs)])


@pytest.mark.parametrize("minimum,maximum", [(1, 2), (2, 4), (3, 3)])
def test_verified_eta_is_deployment_owned(checkout, catalog_env, minimum, maximum):
    from app.core.config import DeliveryPolicy
    catalog_env[0].settings.delivery_policy = DeliveryPolicy(enabled=True, eta_min_days=minimum, eta_max_days=maximum)
    row = policy_reply(checkout)
    expected = str(minimum) if minimum == maximum else f"{minimum}–{maximum}"
    assert f"generally takes {expected} day(s)" in row.content
    assert not cart(row)


@pytest.mark.parametrize("enabled", [False, True])
def test_unknown_delivery_does_not_invent_eta(checkout, catalog_env, enabled):
    from app.core.config import DeliveryPolicy
    catalog_env[0].settings.delivery_policy = DeliveryPolicy(enabled=enabled)
    row = policy_reply(checkout)
    assert "verified information" in row.content
    assert "day" not in row.content and "contacted" not in row.content


@pytest.mark.parametrize("contact", [True, False, None])
def test_contact_policy_in_answer_and_committed_receipt(checkout, catalog_env, contact):
    from app.core.config import DeliveryPolicy
    catalog_env[0].settings.delivery_policy = DeliveryPolicy(enabled=True, eta_min_days=1, eta_max_days=2, contact_before_arrival=contact)
    row = policy_reply(checkout)
    assert ("contacted" in row.content) == (contact is True)
    catalog_env[0].settings.checkout_required_fields = ["phone"]
    offered = checkout("je veux commander pantalon noir M")
    assert "1–2" not in offered.content
    row = checkout("oui")
    assert cart(row)["status"] == "completed" and "1–2" in row.content
    assert ("contacté" in row.content) == (contact is True)
    assert str(cart(row)["order_id"]) not in row.content


@pytest.mark.parametrize("topic", ["delivery_time", "delivery_price", "discount", "payment"])
def test_policy_interrupt_preserves_pending_cart(checkout, catalog_env, topic):
    from app.core.config import DeliveryPolicy
    catalog_env[0].settings.delivery_policy = DeliveryPolicy(enabled=True, eta_min_days=1, eta_max_days=2)
    offered = checkout("je veux commander pantalon noir M")
    row = policy_reply(checkout, topic, "can you confirm this policy?", quantity=3)
    assert cart(row)["id"] == cart(offered)["id"]
    assert cart(row)["status"] == "awaiting_confirmation"
    row = checkout("wakha confirme")
    assert cart(row)["id"] == cart(offered)["id"] and cart(row)["status"] == "confirmed"
    assert "delivery address" in row.content and "phone" not in row.content
    assert cart(supply(checkout))["status"] == "completed"


def test_discount_quantity_attribute_preserves_cart(checkout):
    offered = checkout("je veux commander pantalon noir M")
    text = "ila khdit 3 tn9ess lia taman?"
    row = checkout(text, [plan(text, "business_question", business_topic="discount", quantity=3,
        attributes=[{"name": "quantity", "value": "3"}])])
    assert "information confirmée" in row.content and "%" not in row.content and "variante" not in row.content
    assert cart(row)["id"] == cart(offered)["id"]
    assert cart(checkout("oui"))["status"] == "confirmed"


def test_policy_preserves_selection(checkout):
    offered = checkout("je veux commander pantalon noir M")
    row = policy_reply(checkout)
    assert row.metadata_["catalog_refs"]["selection"] == offered.metadata_["catalog_refs"]["selection"]


def test_verified_delivery_fee_uses_checkout_amount(checkout, catalog_env):
    catalog_env[0].settings.checkout_shipping_cost = Decimal("25.50")
    assert "25.50 MAD" in policy_reply(checkout, "delivery_price").content


def test_website_advice_then_purchase(checkout, pants):
    text = "I can't order online"
    checkout(text, [plan(text, "website_ordering", reference="none", language="english")])
    checkout("je cherche pantalon noir taille M")
    row = checkout("3ndi 240dh chno katnsa7ni?")
    assert "229.00 MAD" in row.content
    text = "je le prends"
    row = checkout(text, [plan(text, items=[dict(reference="focus", quantity=1)])])
    assert cart(row)["items"][0]["target"]["variant_id"] == str(pants[3].id)


def test_website_checkout_pii_not_replayed(checkout):
    text = "site ma khdamch pantalon noir M, Oussama, Casablanca, 12 rue Test"
    checkout(text, [plan(text, "website_ordering", reference="none", items=[item(terms=["pantalon"])], checkout=FIELDS)])
    policy_reply(checkout)
    prompt = str(checkout.service._client.respond.call_args.args[0])
    assert all(secret not in prompt for secret in ("12 rue Test", "Oussama", "212600001111"))


@pytest.mark.parametrize("values", [dict(eta_min_days=1), dict(eta_max_days=2),
    dict(eta_min_days=4, eta_max_days=2), dict(eta_min_days=-1, eta_max_days=2),
    dict(eta_min_days=True, eta_max_days=2), dict(contact_before_arrival="true"), dict(outage=True)])
def test_invalid_delivery_configuration_rejected(values):
    from pydantic import ValidationError
    from app.core.config import DeliveryPolicy
    with pytest.raises(ValidationError):
        DeliveryPolicy(**values)


@pytest.mark.parametrize("text,style", [
    ("chhal katb9a livraison?", "darija_latin"),
    ("f chhal twsl?", "darija_latin"),
    ("livraison l Casa chhal katakhod?", "mixed"),
    ("combien de temps pour la livraison?", "french"),
    ("quand je vais recevoir ma commande?", "french"),
    ("how long does delivery take?", "english"),
])
def test_delivery_language_understanding_uses_configured_facts(checkout, catalog_env, text, style):
    from app.core.config import DeliveryPolicy
    catalog_env[0].settings.delivery_policy = DeliveryPolicy(enabled=True, eta_min_days=2, eta_max_days=4)
    row = checkout(text, [plan(text, "business_question", business_topic="delivery_time", language=style)])
    assert "2–4" in row.content and "1–2" not in row.content


def test_disabled_delivery_policy_cannot_promise_configured_values(checkout, catalog_env):
    from app.core.config import DeliveryPolicy
    catalog_env[0].settings.delivery_policy = DeliveryPolicy(enabled=False, eta_min_days=1, eta_max_days=2, contact_before_arrival=True)
    row = policy_reply(checkout)
    assert "1–2" not in row.content and "contacted" not in row.content


def test_contact_only_configuration_does_not_imply_eta(checkout, catalog_env):
    from app.core.config import DeliveryPolicy
    catalog_env[0].settings.delivery_policy = DeliveryPolicy(enabled=True, contact_before_arrival=True)
    row = policy_reply(checkout)
    assert "time is not confirmed" in row.content and "will be contacted" in row.content
    assert "day" not in row.content


@pytest.mark.parametrize("closed", ["cancelled", "blocked"])
def test_website_entry_does_not_reoffer_closed_cart(checkout, db_session, pants, closed):
    checkout("je veux commander pantalon noir M")
    if closed == "cancelled":
        checkout("annule")
    else:
        pants[1].stock_quantity = 0
        db_session.flush()
        checkout("oui")
    text = "the website isn't working"
    row = checkout(text, [plan(text, "website_ordering", reference="none", language="english")])
    assert "order directly here" in row.content and "What would you like" in row.content
    assert cart(row)["status"] == closed and order_count(db_session) == 0


def test_website_order_redelivery_is_deduplicated(checkout, catalog_env, db_session):
    from uuid import uuid4
    from app.models import Message
    from app.services.whatsapp_service import persist_inbound
    from app.integrations.whatsapp.schemas import IncomingText
    catalog_env[0].settings.checkout_required_fields = ["phone"]
    text = "website checkout is blocked, I want the black medium pants"
    checkout(text, [plan(text, "website_ordering", reference="none", items=[item(terms=["pantalon"])])])
    row = checkout("oui")
    inbound = db_session.get(Message, UUID(row.metadata_["in_reply_to"]))
    external = uuid4().hex
    inbound.external_message_id = external
    db_session.commit()
    assert persist_inbound(db_session, IncomingText(external_message_id=external,
        phone_number="212600001111", text="oui", phone_number_id=catalog_env[0].settings.whatsapp_phone_number_id)) is None
    assert order_count(db_session) == 1


def test_delivery_configuration_loads_from_environment(monkeypatch, catalog_env):
    import json
    from app.core.config import Settings
    monkeypatch.setenv("DELIVERY_POLICY", json.dumps(dict(enabled=True, eta_min_days=2, eta_max_days=4, contact_before_arrival=True)))
    settings = Settings(_env_file=None, database_host="unused", database_name="unused",
        database_user="unused", database_password="unused")
    assert settings.delivery_policy.eta_max_days == 4 and settings.delivery_policy.contact_before_arrival is True


def test_legacy_choice_upgrades_to_cart_without_internal_confirmation(checkout, db_session):
    from app.models import Message
    offered = checkout("je veux commander pantalon noir M")
    inbound = db_session.get(Message, UUID(offered.metadata_["in_reply_to"]))
    inbound.metadata_ = {k: v for k, v in inbound.metadata_.items() if k != "checkout_state"}
    offered.metadata_ = dict(offered.metadata_, commerce_state=dict(offered.metadata_["commerce_state"], cart=None))
    db_session.flush()
    row = checkout("oui")
    assert cart(row)["status"] == "awaiting_confirmation" and order_count(db_session) == 0
    assert "Confirmez-vous la commande" in row.content
    assert "choix" not in row.content and "Mazal" not in row.content
    assert cart(checkout("oui"))["status"] == "confirmed"
