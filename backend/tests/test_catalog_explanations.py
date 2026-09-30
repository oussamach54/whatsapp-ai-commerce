from decimal import Decimal
from unittest.mock import Mock

import pytest
from sqlalchemy import select

from app.ai.catalog_orchestrator import run_catalog
from app.ai.catalog_schemas import CatalogRefs, ProductRef, ResponsePlan, Selection
from app.ai.router import SalesRouter
from app.ai.schemas import AIHistoryMessage, ProviderResult
from app.ai.scope import affirmative_selection
from app.ai.scope import explanation_topic
from app.ai.commerce_state import CommerceState
from app.models import Message
from app.models.enums import MessageDirection
from app.services.whatsapp_service import send_automatic_reply
from tests.test_catalog_orchestration import setup, plan
from tests.test_catalog_references import presentation
from tests.test_catalog_variant_followups import pants


PHRASES = ["mafhamtch", "mafhemtch", "ma fhemtch", "je n'ai pas compris", "j'ai pas compris",
           "chno kat3ni salat l quantité?", "ça veut dire quoi rupture de stock?",
           "qu'est-ce que ça veut dire?", "  JE N’AI   PAS COMPRIS!", "ما فهمتش"]


def focused(db, env, product, variant, selection=False):
    ref = ProductRef(product_id=product.id, variant_id=variant.id)
    refs = CatalogRefs(presented=[ref], focus=ref,
        selection=Selection(**ref.model_dump(), resolution="variant") if selection else None)
    row = presentation(db, env[1], refs, delta=-0.2)
    row.metadata_ = dict(row.metadata_, commerce_state=CommerceState(
        commercial_at=row.created_at, commercial_ref=ref, explanation_context="product").model_dump(mode="json"))
    db.flush()
    return refs


@pytest.mark.parametrize("phrase", PHRASES)
def test_common_explanations_local_and_preserve_refs(db_session, catalog_env, pants, phrase):
    p, _, l, _ = pants
    refs = focused(db_session, catalog_env, p, l)
    service, admission, catalog = setup(catalog_env, [plan(p, l, action="select", reference="focus")])
    admission.begin.return_value = "allowed"
    classifier = Mock()
    reply = SalesRouter(service, classifier, admission, catalog.settings, catalog).reply(
        phrase, catalog.target.conversation_id, lambda: [])
    if explanation_topic(phrase) == "out_of_stock":
        assert "MAD" not in reply.text and "Pantalon" not in reply.text
    else:
        assert "249.00 MAD" in reply.text and "Noir / L" in reply.text
    assert reply.catalog_refs == refs.model_dump(mode="json")
    assert "Hada l choix" not in reply.text and "1." not in reply.text
    service._client.respond.assert_not_called()
    classifier.classify.assert_not_called()


@pytest.mark.parametrize("stock", [0, 3])
@pytest.mark.parametrize("style", ["english", "french", "darija_latin", "darija_arabic", "mixed"])
def test_current_facts_ignore_history_and_preserve_selection(db_session, catalog_env, pants, stock, style):
    p, _, l, _ = pants
    refs = focused(db_session, catalog_env, p, l, selection=True)
    l.stock_quantity, l.price = stock, Decimal("275.00")
    db_session.flush()
    service, admission, catalog = setup(catalog_env, [])
    reply = run_catalog(service, "mafhamtch", [AIHistoryMessage(role="assistant", content="Price 1 MAD, stock 999")],
                        style, admission, catalog)
    assert "275.00 MAD" in reply.text and "999" not in reply.text and "249" not in reply.text
    assert reply.catalog_refs == refs.model_dump(mode="json")
    if style == "english":
        assert ("it is available" if stock else "no units available") in reply.text


def test_term_meaning_and_changed_stock(db_session, catalog_env, pants):
    p, _, l, _ = pants
    focused(db_session, catalog_env, p, l)
    l.stock_quantity = 3
    db_session.flush()
    service, admission, catalog = setup(catalog_env, [])
    reply = run_catalog(service, "chno kat3ni salat l quantité?", [], "english", admission, catalog)
    # A definition is generic even when a freshly focused variant exists.
    assert "means no units" in reply.text and "Right now" not in reply.text
    assert "MAD" not in reply.text and "Pantalon" not in reply.text


@pytest.mark.parametrize("mode", ["missing", "ambiguous", "product_only", "inactive", "deleted", "unknown"])
def test_explanation_clarifies_without_invention(db_session, catalog_env, pants, mode):
    p, m, l, _ = pants
    ref = ProductRef(product_id=p.id, variant_id=l.id)
    refs = CatalogRefs(presented=[ref], focus=ref)
    if mode == "missing":
        refs = CatalogRefs()
    if mode == "ambiguous":
        refs.focus = None
        refs.presented.append(ProductRef(product_id=p.id, variant_id=m.id))
    if mode == "product_only":
        refs.focus = ProductRef(product_id=p.id)
    if mode == "inactive":
        l.is_active = False
    if mode == "deleted":
        db_session.delete(l)
    db_session.flush()
    presentation(db_session, catalog_env[1], refs, delta=-0.1)
    service, admission, catalog = setup(catalog_env, [])
    question = "chno kat3ni livraison gratuite?" if mode == "unknown" else "mafhamtch"
    reply = run_catalog(service, question, [], "english", admission, catalog)
    assert "249" not in reply.text and "no units" not in reply.text
    assert reply.catalog_refs == refs.model_dump(mode="json")


@pytest.mark.parametrize("question", ["mafhamtch", "chno kat3ni?", "ah okay", "merci", "wach kayn?",
    "ch7al taman?", "je ne veux pas le deuxième", "tu as dit 'je prends le deuxième' ?",
    "for example je prends le deuxième", "could you rephrase that", "chno katnsa7ni?"])
def test_planner_cannot_authorize_selection(db_session, catalog_env, pants, question):
    p, _, l, _ = pants
    refs = focused(db_session, catalog_env, p, l)
    service, admission, catalog = setup(catalog_env, [plan(p, l, action="select", reference="focus")])
    reply = run_catalog(service, question, [], "english", admission, catalog)
    assert reply.catalog_refs == refs.model_dump(mode="json")
    assert "Your selection" not in reply.text


@pytest.mark.parametrize("question", ["nakhod hada", "je prends celui-là", "bghit deuxième", "khod lia noir M", "je choisis le premier"])
def test_affirmative_choice_still_selects(db_session, catalog_env, pants, question):
    p, m, _, _ = pants
    focused(db_session, catalog_env, p, m)
    reference = "focus"
    if "deuxième" in question:
        refs = CatalogRefs(presented=[ProductRef(product_id=p.id), ProductRef(product_id=p.id, variant_id=m.id)])
        presentation(db_session, catalog_env[1], refs, delta=-0.1)
        reference = "second"
    if "premier" in question:
        reference = "first"
    service, admission, catalog = setup(catalog_env, [plan(p, m, action="select", reference=reference)])
    reply = run_catalog(service, question, [], "english", admission, catalog)
    assert affirmative_selection(question)
    assert reply.catalog_refs["selection"]["variant_id"] == str(m.id)


def test_semantic_explanation_and_reject_foreign_identity(db_session, catalog_env, pants, catalog_product):
    p, _, l, _ = pants
    refs = focused(db_session, catalog_env, p, l)
    other, v = catalog_product()
    for items in ([], [ProductRef(product_id=other.id, variant_id=v.id)]):
        response = ResponsePlan(action="explain", items=items, question=None, reference="focus", explanation_topic="state")
        service, admission, catalog = setup(catalog_env, [ProviderResult(response.model_dump_json())])
        reply = run_catalog(service, "could you put that more simply", [], "english", admission, catalog)
        assert reply.catalog_refs == refs.model_dump(mode="json")
        assert ("249.00" in reply.text) == (not items)


def test_four_turn_pipeline_persistence(db_session, catalog_env, pants):
    from datetime import timedelta
    from uuid import uuid4
    from app.models.enums import MessageType, SenderType
    from app.integrations.whatsapp.schemas import ReplyTarget
    p, m, l, _ = pants
    catalog, inbound, _ = catalog_env
    service, _, _ = setup(catalog_env, [plan(p, m, reference="focus")])
    sender = Mock(send_text_message=Mock(side_effect=lambda *args: uuid4().hex))
    questions = ["wach 3ndkom pantalon noir taille M?", "w taille L?", "mafhamtch", "chno kat3ni salat l quantité?"]
    for index, question in enumerate(questions):
        if index:
            inbound = Message(conversation_id=inbound.conversation_id, direction=MessageDirection.INBOUND,
                sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT, content=question,
                created_at=out.created_at + timedelta(seconds=1),
                metadata_={"provider": "whatsapp", "phone_number_id": catalog.settings.whatsapp_phone_number_id})
            db_session.add(inbound)
            db_session.flush()
        target = ReplyTarget(conversation_id=inbound.conversation_id, inbound_id=inbound.id, phone_number="212600001111")
        send_automatic_reply(target, sender, catalog.sessions, question, service)
        out = db_session.scalars(select(Message).where(Message.metadata_["in_reply_to"].astext == str(inbound.id))).one()
        # Keep synthetic timestamps monotonic across transactions within the fixture.
        out.created_at = inbound.created_at + timedelta(seconds=0.5)
        db_session.flush()
        refs = out.metadata_["catalog_refs"]
        assert refs["focus"]["variant_id"] == str(m.id if index == 0 else l.id)
        assert refs["selection"] is None
        assert ("249.00 MAD" in out.content) == (index < 3)
        if index == 2:
            assert "ma b9at 7ta" in out.content
    assert sender.send_text_message.call_count == 4
    assert service._client.respond.call_count == 1


@pytest.mark.parametrize("question", ["ah okay", "merci", "safi fhemtk merci"])
def test_acknowledgements_are_local(catalog_env, question):
    service, admission, catalog = setup(catalog_env, [])
    admission.begin.return_value = "allowed"
    classifier = Mock()
    reply = SalesRouter(service, classifier, admission, catalog.settings, catalog).reply(
        question, catalog.target.conversation_id, lambda: [])
    assert reply.text and "selection" not in reply.text
    assert reply.catalog_refs is None
    if question == "ah okay":
        assert reply.text == "👍"
    classifier.classify.assert_not_called()
    service._client.respond.assert_not_called()


@pytest.mark.parametrize("question", ["je ne veux pas le deuxième", "tu as dit 'je prends le deuxième' ?"])
def test_negated_and_quoted_selection_rejected_even_with_valid_ordinal(db_session, catalog_env, pants, question):
    p, m, l, _ = pants
    refs = CatalogRefs(presented=[ProductRef(product_id=p.id, variant_id=m.id),
                                 ProductRef(product_id=p.id, variant_id=l.id)])
    presentation(db_session, catalog_env[1], refs, delta=-0.1)
    service, admission, catalog = setup(catalog_env, [plan(p, l, action="select", reference="second")])
    reply = run_catalog(service, question, [], "english", admission, catalog)
    assert reply.catalog_refs == refs.model_dump(mode="json")
    service._client.respond.assert_called_once()


@pytest.mark.parametrize("wrong", [False, True])
def test_named_variant_selection_cannot_choose_different_size(db_session, catalog_env, pants, wrong):
    p, m, l, _ = pants
    focused(db_session, catalog_env, p, l)
    service, admission, catalog = setup(catalog_env, [plan(p, l if wrong else m, action="select", reference="focus")])
    reply = run_catalog(service, "khod lia noir M", [], "english", admission, catalog)
    if wrong:
        assert reply.catalog_refs["selection"] is None
    else:
        assert reply.catalog_refs["selection"]["variant_id"] == str(m.id)


@pytest.mark.parametrize("question", ["chno katnsa7ni?", "show that product", "ch7al taman dyalo?"])
def test_show_recommendation_and_factual_questions_verify_without_selecting(db_session, catalog_env, pants, question):
    p, m, _, _ = pants
    focused(db_session, catalog_env, p, m)
    def updated_plan():
        m.price = Decimal("280.00")
        db_session.flush()
        return plan(p, m, reference="focus")
    service, admission, catalog = setup(catalog_env, [updated_plan])
    reply = run_catalog(service, question, [], "english", admission, catalog)
    assert "280.00 MAD" in reply.text
    assert reply.catalog_refs["selection"] is None


def test_unknown_explanation_preserves_existing_selection(db_session, catalog_env, pants):
    p, _, l, _ = pants
    refs = focused(db_session, catalog_env, p, l, selection=True)
    service, admission, catalog = setup(catalog_env, [])
    admission.begin.return_value = "allowed"
    classifier = Mock()
    reply = SalesRouter(service, classifier, admission, catalog.settings, catalog).reply(
        "chno kat3ni promotion exclusive?", catalog.target.conversation_id, lambda: [])
    assert reply.catalog_refs == refs.model_dump(mode="json")
    assert "249" not in reply.text
    classifier.classify.assert_not_called()


def test_named_attributes_cannot_select_arbitrary_discovered_product(catalog_env, catalog_product):
    from tests.test_catalog_orchestration import tool
    p, m = catalog_product()
    service, admission, catalog = setup(catalog_env, [tool(), plan(p, m, action="select")])
    reply = run_catalog(service, "khod lia noir M", [], "english", admission, catalog)
    assert reply.catalog_refs["selection"] is None
    assert "Your selection" not in reply.text
