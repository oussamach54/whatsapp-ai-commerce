import hashlib
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.integrations.whatsapp.client import TextMessageClient, WhatsAppAPIError
from app.integrations.whatsapp.schemas import IncomingText, ReplyTarget
from app.models import Message
from app.models.enums import MessageDirection, MessageType, SenderType
from app.schemas.conversation import MessageCreate
from app.services.common import transaction
from app.services.customer_service import resolve_customer_for_update
from app.services.conversation_service import resolve_whatsapp_conversation, stage_message

logger = logging.getLogger(__name__)
AUTO_REPLY = "Bonjour, votre message a bien \u00e9t\u00e9 re\u00e7u."
SessionFactory = Callable[[], AbstractContextManager[Session]]

def persist_inbound(db: Session, incoming: IncomingText) -> ReplyTarget | None:
    with transaction(db):
        # Transaction-scoped lock serializes repeated IDs, including concurrent deliveries.
        lock_key = int.from_bytes(hashlib.sha256(incoming.external_message_id.encode()).digest()[:8], signed=True)
        db.execute(select(func.pg_advisory_xact_lock(lock_key)))
        if db.scalar(select(Message.id).where(Message.external_message_id == incoming.external_message_id)):
            logger.info("whatsapp.duplicate_ignored", extra={"message_id": incoming.external_message_id})
            return None
        customer = resolve_customer_for_update(db, incoming.phone_number)
        logger.info("whatsapp.customer_resolved", extra={"customer_id": str(customer.id)})
        conversation = resolve_whatsapp_conversation(db, customer.id)
        message = stage_message(db, conversation.id, MessageCreate(
            direction=MessageDirection.INBOUND, sender_type=SenderType.CUSTOMER,
            message_type=MessageType.TEXT, content=incoming.text,
            external_message_id=incoming.external_message_id,
            metadata={"provider": "whatsapp", "phone_number_id": incoming.phone_number_id}))
        if incoming.timestamp is not None:
            message.created_at = incoming.timestamp
        db.flush()
        target = ReplyTarget(conversation_id=conversation.id, inbound_id=message.id, phone_number=incoming.phone_number)
    logger.info("whatsapp.inbound_persisted", extra={"message_id": incoming.external_message_id})
    return target

def send_automatic_reply(target: ReplyTarget, client: TextMessageClient, session_factory: SessionFactory) -> None:
    """Runs after HTTP acknowledgement with a separate session and no inbound rollback."""
    try:
        external_id = client.send_text_message(target.phone_number, AUTO_REPLY)
    except WhatsAppAPIError:
        logger.warning("whatsapp.outbound_failed", extra={"inbound_id": str(target.inbound_id)})
        return
    try:
        with session_factory() as db:
            with transaction(db):
                stage_message(db, target.conversation_id, MessageCreate(
                    direction=MessageDirection.OUTBOUND, sender_type=SenderType.SYSTEM,
                    message_type=MessageType.TEXT, content=AUTO_REPLY, external_message_id=external_id,
                    metadata={"provider": "whatsapp", "in_reply_to": str(target.inbound_id)}))
    except Exception:
        # Do not expose database values or re-send a message already accepted by Meta.
        logger.error("whatsapp.outbound_persistence_failed", extra={"inbound_id": str(target.inbound_id)})
        return
    logger.info("whatsapp.outbound_sent", extra={"inbound_id": str(target.inbound_id)})
