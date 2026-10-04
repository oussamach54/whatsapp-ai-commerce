"""Consent belongs to the latest delivered application prompt, never history prose."""
from sqlalchemy import select, tuple_
from app.models import Message
from app.models.enums import MessageDirection, MessageType, SenderType
from app.services.conversation_service import message_order_time


def active_prompt(db, catalog, origin_id=None, lifetime=900):
    origin = db.get(Message, origin_id or catalog.target.inbound_id)
    if (not origin or origin.conversation_id != catalog.target.conversation_id
            or origin.direction != MessageDirection.INBOUND or origin.sender_type != SenderType.CUSTOMER
            or origin.message_type != MessageType.TEXT):
        return None
    ordered = message_order_time()
    anchor = db.scalar(select(ordered).where(Message.id == origin.id))
    prompt = db.scalar(select(Message).where(
        Message.conversation_id == origin.conversation_id,
        Message.direction == MessageDirection.OUTBOUND,
        tuple_(ordered, Message.id) < tuple_(anchor, origin.id),
    ).order_by(ordered.desc(), Message.id.desc()).limit(1))
    if (not prompt or not prompt.external_message_id
            or (prompt.metadata_ or {}).get("provider") != "whatsapp"
            or prompt.metadata_.get("turn_status") != "current"
            or prompt.metadata_.get("ai_guard", {}).get("exclude_history", False)):
        return None
    prompt_time = db.scalar(select(ordered).where(Message.id == prompt.id))
    if not 0 <= (anchor - prompt_time).total_seconds() <= lifetime:
        return None
    # Even an unanswered/failed intervening turn prevents borrowing older consent.
    intervening = db.scalar(select(Message.id).where(
        Message.conversation_id == origin.conversation_id,
        Message.direction == MessageDirection.INBOUND,
        tuple_(ordered, Message.id) > tuple_(prompt_time, prompt.id),
        tuple_(ordered, Message.id) < tuple_(anchor, origin.id)).limit(1))
    return None if intervening else prompt


def prompt_matches(db, catalog, marker, origin_id=None, lifetime=900):
    prompt = active_prompt(db, catalog, origin_id, lifetime)
    return bool(prompt and prompt.metadata_.get("confirmation_prompt") == marker)
