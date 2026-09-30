"""Development migration mismatch through the real checkout pipeline, no external calls."""
import logging
from unittest.mock import patch

from sqlalchemy import text

from tests.test_cod_checkout import checkout, cart, item, plan, order_count
from tests.test_commerce_conversations import dialogue
from tests.test_catalog_variant_followups import pants


PURCHASE = "salam bghet n commandé pantalon noir taille M"


def legacy_search(checkout):
    row = checkout("je cherche pantalon noir taille M")
    state = row.metadata_["commerce_state"]
    row.metadata_ = dict(row.metadata_, commerce_state=dict(state, cart=None, purchase=None,
        customer_intent=dict(intent="product_search", purchase_intent=True,
            speech_act="affirmative", evidence=PURCHASE, language="darija_latin")))


def purchase(checkout):
    return checkout(PURCHASE, [plan(PURCHASE, "product_search", purchase_intent=True,
        language="darija_latin", reference="none", terms=["pantalon"], attributes=item()["attributes"])])


def test_missing_provenance_reproduces_runtime_without_false_status(checkout, db_session, caplog):
    legacy_search(checkout)
    # Isolated test transaction only; fixture rollback restores the schema.
    db_session.execute(text("ALTER TABLE orders DROP COLUMN source_cart_id"))
    db_session.commit()
    with caplog.at_level(logging.WARNING):
        row = purchase(checkout)
    failures = [r for r in caplog.records if r.getMessage().startswith("checkout.failed")]
    assert failures and failures[0].failure_type == "ProgrammingError"
    assert not cart(row)
    assert "nverifi statut" not in row.content
    assert "nwejjed" in row.content
    assert "sqlstate=42703" in caplog.text
    assert "212600001111" not in caplog.text and PURCHASE not in caplog.text


def test_migrated_legacy_conversation_proposes_real_cart(checkout, db_session):
    legacy_search(checkout)
    row = purchase(checkout)
    assert cart(row)["status"] == "awaiting_confirmation"
    assert "249.00 MAD" in row.content and "Nconfirmiw commande?" in row.content
    assert order_count(db_session) == 0


def test_failed_new_purchase_never_recovers_unrelated_completed_order(checkout, catalog_env, db_session):
    catalog_env[0].settings.checkout_required_fields = ["phone"]
    checkout("je veux commander pantalon noir M")
    completed = checkout("oui")
    assert cart(completed)["status"] == "completed"
    with patch("app.services.checkout_service.finalize", side_effect=RuntimeError("test preparation failure")):
        row = purchase(checkout)
    assert "nwejjed" in row.content and "t2ekkdat" not in row.content
    assert order_count(db_session) == 1


def test_preflight_accepts_current_schema_without_writing(db_session):
    from scripts.check_checkout_readiness import readiness_problems
    assert readiness_problems(db_session.connection()) == []


def test_preflight_rejects_old_revision(db_session):
    from scripts.check_checkout_readiness import readiness_problems
    db_session.execute(text("UPDATE alembic_version SET version_num='20260927_0004'"))
    assert any("revision" in problem for problem in readiness_problems(db_session.connection()))


def test_preflight_detects_missing_column_even_if_revision_claims_head(db_session):
    from scripts.check_checkout_readiness import readiness_problems
    db_session.execute(text("ALTER TABLE orders DROP COLUMN source_cart_id"))
    assert "orders.source_cart_id is missing" in readiness_problems(db_session.connection())


def test_preflight_rejects_incompatible_provenance_type(db_session):
    from scripts.check_checkout_readiness import readiness_problems
    db_session.execute(text("ALTER TABLE orders ALTER COLUMN source_cart_id TYPE TEXT USING source_cart_id::text"))
    assert "orders.source_cart_id has an incompatible type" in readiness_problems(db_session.connection())


def test_preflight_unreachable_database_is_sanitized(capsys):
    from sqlalchemy.exc import OperationalError
    from scripts.check_checkout_readiness import main
    with patch("scripts.check_checkout_readiness.create_engine") as engine:
        engine.return_value.connect.side_effect = OperationalError("private SQL", {}, Exception("private credentials"))
        assert main() == 1
        engine.return_value.dispose.assert_called_once()
    output = capsys.readouterr().out
    assert "database verification unavailable (OperationalError)" in output
    assert "private" not in output
