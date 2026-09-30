import pytest
from sqlalchemy import select
from app.models import AIBudget
from tests.test_ai_admission import admission_factory, settings


def test_continuations_are_atomic_counted_and_sequential(db_session, admission_factory):
    admission, message = admission_factory[0]()
    assert admission.begin() == "allowed"
    assert admission.reserve("generation_2", "test") == "suppressed"
    for stage in ("generation", "generation_2", "generation_3"):
        assert admission.reserve(stage, "test") == "allowed"
        assert admission.reserve(stage, "test") == "suppressed"
        admission.record(stage=stage, values={"status": "completed", "input_tokens": 10})
    with pytest.raises(ValueError):
        admission.reserve("generation_4", "test")
    counters = db_session.scalars(select(AIBudget).where(AIBudget.key.like("paid:%"))).all()
    assert all(c.count == 3 for c in counters)
    db_session.refresh(message)
    assert len(message.metadata_["ai_guard"]["attempts"]) == 3


def test_failed_call_stays_counted_and_stops_continuation(db_session, admission_factory):
    admission, _ = admission_factory[0]()
    admission.begin()
    admission.reserve("generation", "test")
    admission.record(stage="generation", values={"status": "failed"})
    assert admission.reserve("generation_2", "test") == "suppressed"
    assert all(c.count == 1 for c in db_session.scalars(select(AIBudget).where(AIBudget.key.like("paid:%"))))


def test_quota_applies_to_each_continuation(admission_factory, settings):
    settings.ai_customer_attempts_per_hour = 1
    admission, _ = admission_factory[0]()
    admission.begin()
    assert admission.reserve("generation", "test") == "allowed"
    admission.record(stage="generation", values={"status": "completed"})
    assert admission.reserve("generation_2", "test") == "notice"
