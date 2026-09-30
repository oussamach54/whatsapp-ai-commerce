from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.ai.client import OpenAITextClient
from app.ai.prompts import FALLBACK_REPLY
from app.ai.schemas import AIHistoryMessage
from app.ai.service import AIService
from app.core.config import Settings
from app.integrations.whatsapp.schemas import ReplyTarget
from app.models import Conversation, Customer, Message
from app.models.enums import ConversationChannel, ConversationStatus, MessageDirection, MessageType, SenderType
from app.services.conversation_service import recent_ai_history
from app.services.whatsapp_service import send_automatic_reply
from tests.test_ai import settings, sdk


@pytest.fixture
def history_rows(db_session):
    customer = Customer(phone_number=uuid4().hex)
    conversation = Conversation(customer=customer, channel=ConversationChannel.WHATSAPP,
                                status=ConversationStatus.ACTIVE)
    db_session.add(conversation)
    db_session.flush()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def add(number, content="text", **kwargs):
        values = dict(id=UUID(int=number), conversation_id=conversation.id,
            created_at=base + timedelta(seconds=number), direction=MessageDirection.INBOUND,
            sender_type=SenderType.CUSTOMER, message_type=MessageType.TEXT, content=content)
        values.update(kwargs)
        row = Message(**values)
        db_session.add(row)
        db_session.flush()
        return row
    return conversation, add


def test_postgres_history_order_cutoff_roles_and_limit(db_session, history_rows):
    conversation, add = history_rows
    first = add(1, "je cherche un pantalon noir")
    add(2, FALLBACK_REPLY, direction=MessageDirection.OUTBOUND, sender_type=SenderType.SYSTEM,
        external_message_id="wamid." + uuid4().hex,
        metadata_={"provider": "whatsapp", "in_reply_to": str(first.id)})
    current = add(4, "et en taille M ?")
    add(3, "same", created_at=current.created_at)
    add(5, "later tie", created_at=current.created_at)
    add(6, "later")
    other = Conversation(customer_id=conversation.customer_id, channel=ConversationChannel.WHATSAPP,
                         status=ConversationStatus.ACTIVE)
    db_session.add(other)
    db_session.flush()
    add(7, "other conversation", conversation_id=other.id, created_at=first.created_at)
    history = recent_ai_history(db_session, conversation.id, current.id)
    assert [(m.role, m.content) for m in history] == [
        ("user", first.content), ("assistant", FALLBACK_REPLY), ("user", "same")]
    assert [m.content for m in recent_ai_history(db_session, conversation.id, current.id, 2)] == [FALLBACK_REPLY, "same"]
    assert recent_ai_history(db_session, conversation.id, first.id) == []
    assert recent_ai_history(db_session, conversation.id, current.id, 0) == []


@pytest.mark.parametrize("overrides", [
    {"message_type": MessageType.IMAGE}, {"content": None}, {"content": " \n\t "},
    {"content": "\u200b"}, {"content": "bad\x01text"},
    {"sender_type": SenderType.SYSTEM}, {"sender_type": SenderType.AI},
    {"direction": MessageDirection.OUTBOUND},
    {"direction": MessageDirection.OUTBOUND, "sender_type": SenderType.AI},
    {"direction": MessageDirection.OUTBOUND, "sender_type": SenderType.SYSTEM,
     "external_message_id": "internal", "metadata_": {"provider": "whatsapp"}},
    {"direction": MessageDirection.OUTBOUND, "sender_type": SenderType.HUMAN,
     "external_message_id": "unsent", "metadata_": {"provider": "internal"}},
])
def test_postgres_excludes_ineligible(db_session, history_rows, overrides):
    conversation, add = history_rows
    add(1, **overrides)
    current = add(2)
    assert recent_ai_history(db_session, conversation.id, current.id) == []


@pytest.mark.parametrize("sender", [SenderType.AI, SenderType.HUMAN])
def test_postgres_sent_business_text(db_session, history_rows, sender):
    conversation, add = history_rows
    add(1, "reply", direction=MessageDirection.OUTBOUND, sender_type=sender,
        external_message_id=uuid4().hex, metadata_={"provider": "whatsapp"})
    current = add(2)
    assert recent_ai_history(db_session, conversation.id, current.id) == [AIHistoryMessage(role="assistant", content="reply")]


@pytest.mark.parametrize("limit,budget,expected", [
    (12, 12000, ["old", "oversized", "same"]),
    (2, 12000, ["oversized", "same"]), (12, 7, ["same"]),
    (12, 3, []), (0, 12000, []), (12, 0, []), (12, 4, ["same"]),
])
def test_history_budget_and_current_once(settings, sdk, limit, budget, expected):
    history = [AIHistoryMessage(role="user", content=value) for value in ["old", "oversized", "same"]]
    AIService(OpenAITextClient(settings), limit, budget).generate_reply("same", history=history)
    sent = sdk[1].responses.create.call_args.kwargs["input"]
    assert sent == [{"role": "user", "content": value} for value in expected + ["same"]]


@pytest.mark.parametrize("message", ["salam, chno t9der t3awni fih?", "Bonjour", "Hello"])
def test_latest_language_instruction_with_history(settings, sdk, message):
    history = [AIHistoryMessage(role="assistant", content="Bonjour !")]
    AIService(OpenAITextClient(settings)).generate_reply(message, history=history)
    kwargs = sdk[1].responses.create.call_args.kwargs
    assert kwargs["input"] == [history[0].model_dump(), {"role": "user", "content": message}]
    assert "The latest customer message determines the response language and script." in " ".join(kwargs["instructions"].split())
    assert kwargs["store"] is False


@pytest.mark.parametrize("read_failed,ai_failed", [(False, False), (True, False), (False, True)])
def test_session_lifecycle_and_failures(monkeypatch, caplog, read_failed, ai_failed, settings, allowed_admission):
    events = []
    db = Mock()
    @contextmanager
    def sessions():
        events.append("open")
        try:
            yield db
        finally:
            events.append("close")
    history = [AIHistoryMessage(role="user", content="pantalon noir")]
    def read(*args):
        events.append("read")
        if read_failed:
            raise RuntimeError("private-db-details")
        return history
    def generate(request):
        assert events == ["open", "read", "close"]
        assert request.messages[:-1] == ([] if read_failed else history)
        events.append("ai")
        if ai_failed:
            raise RuntimeError("private-ai-details")
        return "reply"
    def send(*args):
        assert events[-1] == "ai"
        events.append("send")
        return "wamid.reply"
    monkeypatch.setattr("app.services.whatsapp_service.recent_ai_history", read)
    stage = Mock()
    monkeypatch.setattr("app.services.whatsapp_service.stage_message", stage)
    provider = Mock(generate=Mock(side_effect=generate))
    outbound = Mock(send_text_message=Mock(side_effect=send))
    target = ReplyTarget(conversation_id=uuid4(), inbound_id=uuid4(), phone_number="212600000001")
    send_automatic_reply(target, outbound, sessions, "taille M ?", AIService(provider, settings=settings))
    expected = FALLBACK_REPLY if ai_failed else "reply"
    outbound.send_text_message.assert_called_once_with(target.phone_number, expected)
    assert stage.call_args.args[2].content == expected
    assert events == ["open", "read", "close", "ai", "send", "open", "close"]
    assert "private-" not in caplog.text
    db.commit.assert_called_once()


@pytest.mark.parametrize("name,value", [("ai_history_max_messages", -1), ("ai_history_max_messages", 101),
    ("ai_history_max_chars", -1), ("ai_history_max_chars", 100001)])
def test_history_setting_bounds(settings, name, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **(settings.model_dump() | {name: value}))
