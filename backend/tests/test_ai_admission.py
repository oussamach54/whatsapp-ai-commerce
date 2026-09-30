import hashlib
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import sessionmaker

from app.ai.client import OpenAITextClient
from app.ai.service import AIService
from app.integrations.whatsapp.client import WhatsAppAPIError
from app.integrations.whatsapp.schemas import ReplyTarget
from app.models import AIBudget, Conversation, Customer, Message
from app.models.enums import ConversationChannel, ConversationStatus, MessageDirection, MessageType, SenderType
from app.services.ai_admission_service import AIAdmission
from app.services.conversation_service import recent_ai_history
from app.services.whatsapp_service import send_automatic_reply
from tests.conftest import _test_database_url
from tests.test_ai import settings, sdk


@pytest.fixture
def admission_factory(db_session, settings, monkeypatch):
    # Freeze a value read from PostgreSQL so tests cannot cross a real minute edge.
    now = db_session.scalar(select(func.clock_timestamp()))
    monkeypatch.setattr(AIAdmission, "_now", staticmethod(lambda db: now))
    business = uuid4().hex
    customer = Customer(phone_number=uuid4().hex)
    conversation = Conversation(customer=customer, channel=ConversationChannel.WHATSAPP, status=ConversationStatus.ACTIVE)
    db_session.add(conversation)
    db_session.flush()
    @contextmanager
    def sessions():
        yield db_session
    def make(text="bghit cadeau", **kwargs):
        message = Message(conversation_id=conversation.id, direction=MessageDirection.INBOUND,
            sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT, content=text,
            metadata_={"provider": "whatsapp", "phone_number_id": business}, **kwargs)
        db_session.add(message)
        db_session.flush()
        target = ReplyTarget(conversation_id=conversation.id, inbound_id=message.id, phone_number=customer.phone_number)
        return AIAdmission(sessions, settings, target), message
    return make, customer, conversation


def test_blocked_duplicate_and_rolling_spam(db_session, admission_factory, settings):
    make, customer, _ = admission_factory
    a, message = make("Salam !")
    assert a.begin() == "allowed"
    assert a.begin() == "duplicate"
    b, _ = make("  SALAM   ! ")
    assert b.begin() == "allowed"
    c, row = make("salam !")
    assert c.begin() == "notice"
    db_session.refresh(row)
    assert row.metadata_["ai_guard"]["category"] == "spam"
    d, _ = make("salam !")
    assert d.begin() == "suppressed"
    # Age rolling entries independently of WhatsApp event timestamps.
    for counter in db_session.scalars(select(AIBudget).where(AIBudget.key.like("spam:%"))):
        counter.state = {"times": [0, 1]}
    e, _ = make("salam !")
    assert e.begin() == "allowed"
    customer.is_blocked = True
    db_session.flush()
    f, _ = make("different")
    assert f.begin() == "blocked"


def test_minute_quota_notice_and_event_timestamp_ignored(db_session, admission_factory, settings):
    make, _, _ = admission_factory
    settings.ai_inbound_per_minute = 2
    for n, expected in enumerate(["allowed", "allowed", "notice", "suppressed"]):
        a, _ = make(f"message {n}", created_at=datetime(2000, 1, 1, tzinfo=timezone.utc))
        assert a.begin() == expected
    row = db_session.scalars(select(AIBudget).where(AIBudget.key.like("inbound:%"))).one()
    assert row.window_start.year >= 2026
    assert row.count == 4


@pytest.mark.parametrize("setting", ["ai_customer_attempts_per_hour", "ai_customer_attempts_per_day", "ai_business_attempts_per_day"])
def test_paid_quota_atomic_and_stages_separate(db_session, admission_factory, settings, setting):
    make, _, _ = admission_factory
    setattr(settings, setting, 2)
    first, message = make()
    assert first.begin() == "allowed"
    assert first.reserve("classifier", "small") == "allowed"
    assert first.reserve("generation", "sales") == "allowed"
    assert first.reserve("generation", "sales") == "suppressed"
    second, _ = make("another product")
    assert second.begin() == "allowed"
    assert second.reserve("classifier", "small") == "notice"
    counters = db_session.scalars(select(AIBudget).where(AIBudget.key.like("paid:%"))).all()
    assert len(counters) == 3 and all(c.count == 2 for c in counters)
    db_session.refresh(message)
    attempts = message.metadata_["ai_guard"]["attempts"]
    assert set(attempts) == {"classifier", "generation"}
    assert attempts["generation"]["output_tokens"] is None
    assert attempts["classifier"]["model"] == "small"


def test_counter_expiry_cleanup_is_bounded(db_session, admission_factory):
    make, _, _ = admission_factory
    now = db_session.scalar(select(func.clock_timestamp()))
    for i in range(105):
        db_session.add(AIBudget(key=f"expired:{i}", window_start=now - timedelta(days=2),
                               expires_at=now - timedelta(days=1), count=0))
    db_session.flush()
    a, _ = make()
    assert a.begin() == "allowed"
    assert db_session.scalar(select(func.count()).select_from(AIBudget).where(AIBudget.key.like("expired:%"))) == 5


def test_tagged_history_exclusion_and_fallback(db_session, admission_factory):
    make, _, conversation = admission_factory
    for text in ("irrelevant", "spam", "injection"):
        a, row = make(text)
        a.begin()
        a.record(category="unrelated", exclude_history=True)
    current, row = make("taille M ?")
    assert recent_ai_history(db_session, conversation.id, row.id) == []


@pytest.fixture
def committed_admissions(settings, monkeypatch):
    """Separate real connections/commits are required to test competing workers."""
    url = _test_database_url()
    if url is None:
        pytest.fail("Dedicated PostgreSQL test database required")
    engine = create_engine(url)
    sessions = sessionmaker(engine)
    with sessions() as db:
        now = db.scalar(select(func.clock_timestamp()))
    monkeypatch.setattr(AIAdmission, "_now", staticmethod(lambda db: now))
    business = uuid4().hex
    namespace = hashlib.sha256(business.encode()).hexdigest()[:32]
    customer_ids, conversation_ids = [], []
    targets = []
    try:
        with sessions.begin() as db:
            for i in range(6):
                customer = Customer(phone_number=uuid4().hex)
                conversation = Conversation(customer=customer, channel=ConversationChannel.WHATSAPP, status=ConversationStatus.ACTIVE)
                db.add(conversation)
                db.flush()
                message = Message(conversation_id=conversation.id, direction=MessageDirection.INBOUND,
                    sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT, content="bghit cadeau",
                    metadata_={"provider": "whatsapp", "phone_number_id": business})
                db.add(message)
                db.flush()
                customer_ids.append(customer.id)
                conversation_ids.append(conversation.id)
                targets.append(ReplyTarget(conversation_id=conversation.id, inbound_id=message.id, phone_number=customer.phone_number))
        yield sessions, targets
    finally:
        with sessions.begin() as db:
            db.execute(delete(Conversation).where(Conversation.id.in_(conversation_ids)))
            db.execute(delete(Customer).where(Customer.id.in_(customer_ids)))
            db.execute(delete(AIBudget).where(AIBudget.key.contains(namespace)))
        engine.dispose()


@pytest.mark.parametrize("same_customer", [False, True])
def test_concurrent_reservations_never_exceed_limit(committed_admissions, settings, same_customer):
    sessions, targets = committed_admissions
    settings.ai_business_attempts_per_day = 100 if same_customer else 2
    settings.ai_customer_attempts_per_hour = 2
    settings.ai_spam_threshold = 20
    if same_customer:
        with sessions.begin() as db:
            for target in targets[1:]:
                db.get(Message, target.inbound_id).conversation_id = targets[0].conversation_id
                target.conversation_id = targets[0].conversation_id
    admissions = [AIAdmission(sessions, settings, target) for target in targets]
    for a in admissions:
        assert a.begin() == "allowed"
    barrier = Barrier(len(admissions))
    def reserve(a):
        barrier.wait(timeout=10)
        return a.reserve("generation", "test-model")
    with ThreadPoolExecutor(max_workers=len(admissions)) as pool:
        results = list(pool.map(reserve, admissions))
    assert results.count("allowed") == 2
    with sessions() as db:
        row = db.scalars(select(AIBudget).where(AIBudget.key.like("paid:business:%"))).all()
        assert any(c.count == 2 for c in row)


@pytest.mark.parametrize("send_fails", [False, True])
def test_real_pipeline_usage_sessions_and_outbound_failure(committed_admissions, settings, sdk, send_fails):
    sessions, targets = committed_admissions
    active = []
    @contextmanager
    def tracked():
        with sessions() as db:
            active.append(db)
            try:
                yield db
            finally:
                active.remove(db)
    def generate(**kwargs):
        assert not active
        return SimpleNamespace(status="completed", output_text="Quel budget ?",
            usage=SimpleNamespace(input_tokens=80, output_tokens=12, input_tokens_details=SimpleNamespace(cached_tokens=20)))
    sdk[1].responses.create.side_effect = generate
    def send(*args):
        assert not active
        if send_fails:
            raise WhatsAppAPIError("failed")
        return uuid4().hex
    outbound = Mock(send_text_message=Mock(side_effect=send))
    send_automatic_reply(targets[0], outbound, tracked, "bghit cadeau", AIService(OpenAITextClient(settings), settings=settings))
    with sessions() as db:
        guard = db.get(Message, targets[0].inbound_id).metadata_["ai_guard"]
        assert guard["category"] == "commerce" and guard["path"] == "generation"
        usage = guard["attempts"]["generation"]
        assert usage["input_tokens"] == 80 and usage["output_tokens"] == 12
        assert usage["cached_input_tokens"] == 20 and usage["model"] == "test-model"
        assert usage["status"] == "completed" and usage["fallback"] is False
        assert "started_at" in usage and "finished_at" in usage
        out = db.scalars(select(Message).where(Message.conversation_id == targets[0].conversation_id,
            Message.direction == MessageDirection.OUTBOUND)).all()
        assert len(out) == (0 if send_fails else 1)
    # A duplicate invocation doesn't call either provider again.
    send_automatic_reply(targets[0], outbound, tracked, "bghit cadeau", AIService(OpenAITextClient(settings), settings=settings))
    assert sdk[1].responses.create.call_count == 1 and outbound.send_text_message.call_count == 1


def test_quota_spans_conversations_and_rolls_over(db_session, admission_factory, settings, monkeypatch):
    make, customer, original = admission_factory
    settings.ai_customer_attempts_per_hour = 1
    first, _ = make()
    assert first.begin() == "allowed"
    assert first.reserve("generation", "model") == "allowed"
    second, row = make("a different product")
    new = Conversation(customer_id=customer.id, channel=ConversationChannel.WHATSAPP, status=ConversationStatus.ACTIVE)
    db_session.add(new)
    db_session.flush()
    row.conversation_id = new.id
    second.target.conversation_id = new.id
    db_session.flush()
    assert second.begin() == "allowed"
    assert second.reserve("generation", "model") == "notice"
    # Move the previous window into the past; new attempts use current DB time.
    hourly = db_session.scalars(select(AIBudget).where(AIBudget.key.like("paid:customer:hour:%"))).one()
    hourly.window_start -= timedelta(hours=1)
    hourly.expires_at -= timedelta(hours=1)
    db_session.flush()
    third, _ = make("another size")
    assert third.begin() == "allowed"
    assert third.reserve("generation", "model") == "allowed"


def test_notice_cooldown_survives_new_message(db_session, admission_factory, settings):
    make, _, _ = admission_factory
    settings.ai_inbound_per_minute = 1
    assert make("one")[0].begin() == "allowed"
    assert make("two")[0].begin() == "notice"
    assert make("three")[0].begin() == "suppressed"
    notice = db_session.scalars(select(AIBudget).where(AIBudget.key.like("notice:%"))).one()
    now = db_session.scalar(select(func.clock_timestamp()))
    notice.state = {"last": now.timestamp() - 61}
    db_session.flush()
    assert make("four")[0].begin() == "notice"


def test_spam_cannot_reserve_directly(admission_factory):
    make, _, _ = admission_factory
    for _ in range(2):
        assert make()[0].begin() == "allowed"
    denied, _ = make()
    assert denied.begin() == "notice"
    assert denied.reserve("generation", "model") == "suppressed"
