"""Real incident and trusted-state regressions; both network boundaries are mocked."""
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest

from app.ai.attribute_adapter import CATALOG_ATTRIBUTES
from app.ai.catalog_schemas import CatalogRefs, ProductRef, SearchProducts
from app.ai.commerce_state import CommerceState
from app.models import ProductVariant
from app.services.catalog_service import CatalogService
from tests.test_catalog_orchestration import plan, tool
from tests.test_catalog_references import presentation
from tests.test_catalog_variant_followups import pants
from tests.test_commerce_conversations import dialogue, refs


def state(row):
    return row.metadata_["commerce_state"]


def seed_state(row, db, **changes):
    row.metadata_ = dict(row.metadata_, commerce_state=state(row) | changes)
    db.flush()


def focus_l(dialogue):
    dialogue("je cherche pantalon noir taille M")
    return dialogue("w taille L?")


def test_actual_failed_whatsapp_sequence(dialogue, pants, db_session, catalog_env):
    p, m, l, blue = pants
    ref = ProductRef(product_id=p.id, variant_id=l.id)
    old = presentation(db_session, catalog_env[1], CatalogRefs(focus=ref, presented=[ref]), delta=-86400)
    old.metadata_ = dict(old.metadata_, commerce_state=CommerceState(commercial_ref=ref,
        commercial_at=old.created_at).model_dump(mode="json"))
    db_session.flush()
    greeting = dialogue("salam")
    assert refs(greeting)["focus"]["variant_id"] == str(l.id)
    assert state(greeting)["commercial_at"] == old.metadata_["commerce_state"]["commercial_at"]
    definition = dialogue("chno kat3ni salat l quantité?")
    assert "Pantalon" not in definition.content and "MAD" not in definition.content
    first = dialogue("wach 3ndkom pantalon noir taille M?", [
        tool(args=SearchProducts(terms=["pantalon"], size="M", color="noir").model_dump_json()), plan(p, m)])
    assert refs(first)["focus"]["variant_id"] == str(m.id)
    second = dialogue("w taille L?")
    assert refs(second)["focus"]["variant_id"] == str(l.id) and "salat" in second.content
    explanation = dialogue("mafhamtch")
    assert "Noir / L" in explanation.content and "249.00 MAD" in explanation.content
    before = state(explanation)
    miss = dialogue("ah okay, w bleu?")
    assert "couleur bleu" in miss.content and "taille L" in miss.content
    assert "Bleu / M" in miss.content and "229.00 MAD" in miss.content
    assert "taille M, machi L" in miss.content
    assert refs(miss)["focus"] == refs(explanation)["focus"]
    assert refs(miss)["selection"] is None
    assert refs(miss)["presented"] == [{"product_id": str(p.id), "variant_id": str(blue.id)}]
    assert state(miss)["requested_attributes"] == before["requested_attributes"]
    assert state(miss)["verified_attributes"] == {"size": "L", "color": "noir"}
    assert state(miss)["attempted"]["changed"] == {"color": "bleu"}
    recommend = dialogue("chno katnsa7ni?")
    assert "Bleu / M" in recommend.content and "229.00 MAD" in recommend.content
    assert "taille M, machi L" in recommend.content and refs(recommend)["selection"] is None
    question = dialogue("ch7al taman?")
    assert state(question)["pending"]["operation"] == "price"
    assert "Noir / M" in question.content and "Noir / L" in question.content
    m.price = Decimal("259")
    db_session.flush()
    with patch.object(CatalogService, "search", side_effect=AssertionError("candidate recovery must not search")):
        answer = dialogue("Pantalon Classic — Noir / M")
    assert answer.content == "259.00 MAD."
    assert state(answer)["operation"] == "price" and state(answer)["pending"] is None
    assert refs(answer)["focus"]["variant_id"] == str(m.id)
    assert refs(answer)["selection"] is None
    assert dialogue.service._client.respond.call_count == 2


@pytest.mark.parametrize("selection", [False, True])
@pytest.mark.parametrize("alternatives", [0, 1, 3])
def test_miss_preserves_trusted_state_and_only_presents(dialogue, pants, db_session, selection, alternatives):
    p, m, l, blue = pants
    if selection:
        dialogue("je veux commander pantalon noir M")
    else:
        dialogue("je cherche pantalon noir M")
    trusted = dialogue("w taille L?")
    if alternatives == 0:
        blue.is_active = False
    if alternatives == 3:
        for size, price, stock in (("S", "219", 0), ("XL", "239", 2)):
            db_session.add(ProductVariant(product=p, name=f"Bleu / {size}", sku=uuid4().hex,
                size=size, color="bleu", price=Decimal(price), stock_quantity=stock))
    db_session.flush()
    row = dialogue("ah okay, w bleu?")
    assert refs(row)["focus"] == refs(trusted)["focus"]
    assert refs(row)["selection"] == refs(trusted)["selection"]
    assert state(row)["requested_attributes"] == state(trusted)["requested_attributes"]
    assert len(state(row)["attempted"]["alternatives"]) == alternatives
    if alternatives:
        assert refs(row)["presented"] == state(row)["attempted"]["alternatives"]
        assert row.content.index("Bleu / M") < row.content.index("Bleu / S") if alternatives == 3 else "229" in row.content
    else:
        assert refs(row)["presented"] == refs(trusted)["presented"]
        assert "MAD" not in row.content


def test_alternative_explicitly_selected_and_browsed(dialogue, pants):
    dialogue("je veux commander pantalon noir M")
    dialogue("w taille L?")
    miss = dialogue("w bleu?")
    selected = dialogue("je prends le premier")
    assert refs(selected)["selection"]["variant_id"] == str(pants[3].id)
    assert refs(selected)["focus"]["variant_id"] == str(pants[3].id)
    assert state(selected)["attempted"] is None
    assert refs(miss)["selection"]["variant_id"] == str(pants[1].id)
    browse = dialogue("w noir?")
    assert refs(browse)["selection"] == refs(selected)["selection"]
    assert refs(browse)["focus"]["variant_id"] == str(pants[1].id)


def test_zero_stock_exact_is_not_replaced_by_alternative(dialogue, pants):
    row = focus_l(dialogue)
    assert state(row)["attempted"] is None
    assert "Noir / L" in row.content and ("salat" in row.content or "épuisé" in row.content)
    assert "Bleu" not in row.content


@pytest.mark.parametrize("question", ["salam", "merci", "ah okay"])
@pytest.mark.parametrize("old", [False, True])
def test_social_does_not_refresh_relevance(dialogue, db_session, question, old):
    row = focus_l(dialogue)
    if old:
        seed_state(row, db_session, commercial_at=(row.created_at - timedelta(days=1)).isoformat())
    timestamp = state(row)["commercial_at"]
    social = dialogue(question)
    assert datetime.fromisoformat(state(social)["commercial_at"]) == datetime.fromisoformat(timestamp)
    assert refs(social)["focus"] == refs(row)["focus"]
    followup = dialogue("mafhamtch")
    assert ("249.00 MAD" in followup.content) is (not old)


def test_generic_definition_without_context(dialogue):
    row = dialogue("chno kat3ni salat l quantité?")
    assert "quantité" in row.content and "Pantalon" not in row.content and "MAD" not in row.content
    assert state(row)["commercial_at"] is None


def test_legacy_relevance_uncertain_clarifies(dialogue, pants, catalog_env, db_session):
    ref = ProductRef(product_id=pants[0].id, variant_id=pants[2].id)
    presentation(db_session, catalog_env[1], CatalogRefs(focus=ref, presented=[ref]))
    row = dialogue("mafhamtch")
    assert "249" not in row.content
    assert state(row)["commercial_at"] is None


def test_explicit_explanation_resolves_named_target(dialogue, pants):
    row = dialogue("chno kat3ni salat quantité dial pantalon noir L?")
    assert "Noir / L" in row.content and "249.00 MAD" in row.content
    assert state(row)["operation"] == "explain"
    assert refs(row)["selection"] is None
    dialogue.service._client.respond.assert_not_called()


@pytest.mark.parametrize("operation,question", [("price", "ch7al taman?"), ("stock", "kayn?")])
@pytest.mark.parametrize("answer", ["l premier", "Pantalon Classic — Bleu / M"])
def test_pending_original_operation_and_fresh_facts(dialogue, pants, db_session, operation, question, answer):
    focus_l(dialogue)
    dialogue("w bleu?")
    prompt = dialogue(question)
    assert state(prompt)["pending"]["operation"] == operation
    assert "Bleu / M" in prompt.content
    blue = pants[3]
    blue.price, blue.stock_quantity = Decimal("235"), 0
    db_session.flush()
    with patch.object(CatalogService, "search", side_effect=AssertionError("unexpected search")):
        row = dialogue(answer)
    assert state(row)["operation"] == operation
    assert state(row)["pending"] is None
    assert "235.00 MAD" in row.content
    if operation == "stock":
        assert "salat" in row.content or "épuisé" in row.content
    assert refs(row)["selection"] is None


def test_new_named_target_deliberately_searches_preserving_price(dialogue, catalog_product):
    p, v = catalog_product(name="Lampe Atelier", size=None, color=None)
    dialogue("ch7al taman?")
    row = dialogue("Lampe Atelier")
    assert state(row)["operation"] == "price" and row.content == "100.00 MAD."
    assert refs(row)["focus"]["variant_id"] == str(v.id)


def test_new_topic_clears_pending_price(dialogue):
    dialogue("ch7al taman?")
    row = dialogue("je cherche pantalon noir M")
    assert state(row)["operation"] == "search"
    assert state(row)["pending"] is None or state(row)["pending"]["operation"] == "search"


def test_pending_wall_time_expiry_cannot_authorize_selection(dialogue, pants, db_session):
    row = dialogue("je veux celui-là")
    pending = state(row)["pending"] | {"created_at": (row.created_at - timedelta(hours=1)).isoformat()}
    seed_state(row, db_session, pending=pending)
    row = dialogue("Pantalon Classic noir M", [tool(args=SearchProducts(terms=["pantalon"]).model_dump_json()), plan(pants[0], pants[1], action="select")])
    assert refs(row)["selection"] is None


@pytest.mark.parametrize("deleted", [False, True])
def test_inactive_or_deleted_focus_selection_invalidated(dialogue, pants, db_session, deleted):
    dialogue("je veux commander pantalon noir M")
    if deleted:
        db_session.delete(pants[1])
    else:
        pants[1].is_active = False
    db_session.flush()
    row = dialogue("ch7al?")
    assert refs(row)["focus"] is None and refs(row)["selection"] is None
    assert not refs(row)["presented"]
    assert "249" not in row.content


def test_lookup_failure_rolls_back_candidate_state(dialogue):
    trusted = focus_l(dialogue)
    with patch.object(CatalogService, "get", side_effect=RuntimeError("database unavailable")):
        row = dialogue("w bleu?")
    assert refs(row) == refs(trusted)
    assert state(row) == state(trusted)


def test_hard_constraint_and_budget_not_relaxed(dialogue, db_session):
    row = focus_l(dialogue)
    seed_state(row, db_session, hard_attributes={"size": "L"})
    row = dialogue("w bleu?")
    assert not state(row)["attempted"]["alternatives"] and "229" not in row.content
    row = focus_l(dialogue)
    seed_state(row, db_session, constraints=state(row)["constraints"] | {"max_price": "220"})
    row = dialogue("w bleu?")
    assert not state(row)["attempted"]["alternatives"] and "229" not in row.content


def test_generic_relaxation_never_relaxes_changed_or_hard_attributes():
    adapter = CATALOG_ATTRIBUTES
    assert adapter.relaxation_filters({"color": "bleu"}, {"size": "L"}, {}) == [{"color": "bleu"}]
    assert adapter.relaxation_filters({"size": "L"}, {"color": "noir"}, {}) == [{"size": "L"}]
    assert adapter.relaxation_filters({"color": "bleu"}, {"size": "L"}, {"size": "L"}) == []
    with pytest.raises(ValueError):
        adapter.relaxation_filters({"scent": "rose"}, {"volume": "100ml"}, {})


def test_definition_then_confusion_does_not_reintroduce_product(dialogue):
    focus_l(dialogue)
    definition = dialogue("chno kat3ni salat l quantité?")
    dialogue("merci")
    row = dialogue("mafhamtch")
    assert "Pantalon" not in row.content and "MAD" not in row.content
    assert state(row)["commercial_at"] == state(definition)["commercial_at"]


def test_new_named_price_target_does_not_inherit_old_variant_constraints(dialogue, catalog_product):
    p, v = catalog_product(name="Lampe Atelier", size=None, color=None)
    focus_l(dialogue)
    dialogue("w bleu?")
    dialogue("ch7al taman?")
    row = dialogue("Lampe Atelier")
    assert state(row)["operation"] == "price" and row.content == "100.00 MAD."
    assert refs(row)["focus"]["variant_id"] == str(v.id)


def test_explicit_hard_attribute_is_never_relaxed(dialogue):
    focus_l(dialogue)
    row = dialogue("taille L uniquement")
    assert state(row)["hard_attributes"] == {"size": "L"}
    row = dialogue("w bleu?")
    assert not state(row)["attempted"]["alternatives"]
    assert "229" not in row.content and state(row)["hard_attributes"] == {"size": "L"}


def test_plain_ordinal_focuses_alternative_without_selecting(dialogue, pants):
    dialogue("je veux commander pantalon noir M")
    dialogue("w taille L?")
    dialogue("w bleu?")
    row = dialogue("l premier")
    assert refs(row)["focus"]["variant_id"] == str(pants[3].id)
    assert refs(row)["selection"]["variant_id"] == str(pants[1].id)
    assert state(row)["attempted"] is None


def test_recommendation_rechecks_new_exact_variant(dialogue, pants, db_session):
    focus_l(dialogue)
    dialogue("w bleu?")
    exact = ProductVariant(product=pants[0], name="Bleu / L", sku=uuid4().hex,
        size="L", color="bleu", price=Decimal("219"), stock_quantity=2)
    db_session.add(exact)
    db_session.flush()
    row = dialogue("chno katnsa7ni?")
    assert "Bleu / L" in row.content and "219.00 MAD" in row.content
    line = next(line for line in row.content.splitlines() if "Bleu / L" in line)
    assert "Alternative" not in line and "L → M" not in line
    assert refs(row)["focus"]["variant_id"] == str(pants[2].id)
    assert refs(row)["selection"] is None


def test_inactive_parent_invalidates_all_its_references(dialogue, pants, db_session):
    dialogue("je veux commander pantalon noir M")
    dialogue("w taille L?")
    pants[0].is_active = False
    db_session.flush()
    row = dialogue("ch7al?")
    assert refs(row)["focus"] is None and refs(row)["selection"] is None
    assert not refs(row)["presented"] and not state(row)["discussed"]


def test_inactive_pending_candidate_cannot_be_selected(dialogue, pants, db_session):
    focus_l(dialogue)
    dialogue("w bleu?")
    dialogue("ch7al taman?")
    pants[3].is_active = False
    db_session.flush()
    row = dialogue("l premier")
    assert "229" not in row.content
    assert refs(row)["selection"] is None
    assert all(r["variant_id"] != str(pants[3].id) for r in refs(row)["presented"])


def test_bounded_alternatives_and_deterministic_tie_break(dialogue, pants, db_session):
    focus_l(dialogue)
    for size in ("XS", "S", "XL", "XXL", "XXXL", "XXS", "4XL"):
        db_session.add(ProductVariant(product=pants[0], name=f"Bleu / {size}", sku=uuid4().hex,
            size=size, color="bleu", price=Decimal("229"), stock_quantity=3))
    db_session.flush()
    calls = []
    original = CatalogService.get
    def read(catalog, args):
        calls.append(args)
        return original(catalog, args)
    with patch.object(CatalogService, "get", read):
        row = dialogue("w bleu?")
    assert len(calls) == 3  # current variant, exact combination, one relaxation
    assert len(refs(row)["presented"]) == 3
    ids = [ref["variant_id"] for ref in refs(row)["presented"]]
    assert ids == sorted(ids)
    assert "Autres" in row.content or "autres" in row.content or "okhrin" in row.content


def test_semantic_failure_restores_trusted_state(dialogue, pants):
    trusted = focus_l(dialogue)
    # Invalid provider output is caught by the semantic orchestrator itself.
    from app.ai.schemas import ProviderResult
    row = dialogue("show that product", [ProviderResult("not a plan")])
    assert refs(row) == refs(trusted) and state(row) == state(trusted)


@pytest.mark.parametrize("count", [3, 4])
def test_intervening_turn_relevance_bound(dialogue, count):
    focus_l(dialogue)
    for _ in range(count):
        dialogue("merci")
    row = dialogue("mafhamtch")
    assert ("249.00 MAD" in row.content) is (count == 3)


def test_no_match_new_topic_does_not_refresh_old_relevance(dialogue):
    trusted = focus_l(dialogue)
    row = dialogue("je cherche ProduitInexistant")
    assert refs(row)["focus"] == refs(trusted)["focus"]
    assert state(row)["commercial_at"] is None
    row = dialogue("mafhamtch")
    assert "249" not in row.content


def test_existing_exact_variant_over_budget_is_not_called_nonexistent(dialogue, db_session):
    row = dialogue("je cherche pantalon noir M")
    seed_state(row, db_session, constraints=state(row)["constraints"] | {"max_price": "240"})
    row = dialogue("w taille L?")
    assert state(row)["attempted"]["outcome"] == "constraint_miss"
    assert not state(row)["attempted"]["alternatives"]
    assert "Combinaison" not in row.content and "249" not in row.content


def test_invalid_focus_does_not_erase_different_valid_selection(dialogue, pants, db_session):
    chosen = dialogue("je veux commander pantalon noir M")
    dialogue("w taille L?")
    pants[2].is_active = False
    db_session.flush()
    row = dialogue("ch7al?")
    assert refs(row)["focus"] is None
    assert refs(row)["selection"] == refs(chosen)["selection"]
