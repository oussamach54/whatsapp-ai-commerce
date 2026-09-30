import hashlib
import json
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session
from app.ai.prompts import FALLBACK_REPLY
from app.ai.service import AIService
from app.ai.classifier import OpenAIClassifier
from app.ai.router import SalesRouter
from app.core.config import get_settings
from app.services.ai_admission_service import AIAdmission
from app.integrations.whatsapp.client import TextMessageClient, WhatsAppAPIError
from app.integrations.whatsapp.schemas import IncomingText, ReplyTarget
from app.models import Message, Conversation
from app.models.enums import MessageDirection, MessageType, SenderType
from app.schemas.conversation import MessageCreate
from app.services.common import transaction
from app.services.customer_service import resolve_customer_for_update
from app.services.conversation_service import recent_ai_history, resolve_whatsapp_conversation, stage_message

logger = logging.getLogger(__name__)
AUTO_REPLY = FALLBACK_REPLY
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
        db.execute(select(Conversation.id).where(Conversation.id == conversation.id).with_for_update())
        message = stage_message(db, conversation.id, MessageCreate(
            direction=MessageDirection.INBOUND, sender_type=SenderType.CUSTOMER,
            message_type=MessageType.TEXT, content=incoming.text,
            external_message_id=incoming.external_message_id,
            metadata={"provider": "whatsapp", "phone_number_id": incoming.phone_number_id,
                      "received_at": db.scalar(select(func.clock_timestamp())).isoformat()}))
        if incoming.timestamp is not None:
            message.created_at = incoming.timestamp
        db.flush()
        target = ReplyTarget(conversation_id=conversation.id, inbound_id=message.id, phone_number=incoming.phone_number)
    logger.info("whatsapp.inbound_persisted", extra={"message_id": incoming.external_message_id})
    return target

def send_automatic_reply(target: ReplyTarget, client: TextMessageClient, session_factory: SessionFactory,
    message_text: str = "", ai_service: AIService | None = None) -> None:
    """Runs after HTTP acknowledgement with a separate session and no inbound rollback."""
    def load_history():
        if ai_service is not None and ai_service.history_max_messages and ai_service.history_max_chars:
            with session_factory() as db:
                return recent_ai_history(db, target.conversation_id, target.inbound_id,
                                            ai_service.history_max_messages)
        return []
    # History is plain typed data; its session is closed before either network call.
    exclude_history = False
    catalog_refs = None
    commerce_state = None
    guard_ordering = False
    preserve_commerce = False
    if ai_service is not None:
        settings = ai_service.settings or get_settings()
        from app.services.catalog_service import CatalogService
        catalog = CatalogService(session_factory, settings, target) if ai_service.catalog_enabled else None
        result = SalesRouter(ai_service, OpenAIClassifier(settings),
            AIAdmission(session_factory, settings, target), settings, catalog).reply(message_text, target.conversation_id, load_history)
        reply_text, exclude_history = result.text, result.exclude_history
        catalog_refs = result.catalog_refs
        commerce_state = result.commerce_state
        preserve_commerce = result.preserve_commerce
        guard_ordering = catalog_refs is not None or commerce_state is not None
        if reply_text is None:
            return
    else:
        reply_text = AUTO_REPLY
    # Short DB check, closed before the network send. A newer inbound supersedes
    # this computation; do not send a known-obsolete reply.
    if guard_ordering:
        try:
            with session_factory() as db:
                if _superseded(db, target):
                    logger.info("whatsapp.reply_superseded", extra={"inbound_id": str(target.inbound_id)})
                    return
        except Exception:
            logger.warning("whatsapp.reply_order_unavailable", extra={"inbound_id": str(target.inbound_id)})
            return
    try:
        external_id = client.send_text_message(target.phone_number, reply_text)
    except WhatsAppAPIError as exc:
        # Include safe fields in the message for plain console formatters as well
        # as structured logging. Never log the raw exception or provider response.
        logger.warning("whatsapp.outbound_failed %s", json.dumps(exc.diagnostics, ensure_ascii=True),
            extra={"inbound_id": str(target.inbound_id), **exc.diagnostics})
        return
    # A social acknowledgement needs no pre-send history/catalog query. Carry
    # its trusted memory only after successful delivery, using the inbound anchor.
    if preserve_commerce:
        from app.ai.conversation_engine import carry_social
        from app.ai.scope import language_style
        try:
            result = carry_social(catalog, result, language_style(message_text))
            catalog_refs, commerce_state = result.catalog_refs, result.commerce_state
            guard_ordering = catalog_refs is not None or commerce_state is not None
        except Exception:
            logger.warning("ai.social_memory_unavailable")
    try:
        with session_factory() as db:
            with transaction(db):
                db.execute(select(Conversation.id).where(Conversation.id == target.conversation_id).with_for_update())
                stale = guard_ordering and _superseded(db, target)
                stage_message(db, target.conversation_id, MessageCreate(
                    direction=MessageDirection.OUTBOUND, sender_type=SenderType.SYSTEM,
                    message_type=MessageType.TEXT, content=reply_text, external_message_id=external_id,
                    metadata={"provider": "whatsapp", "in_reply_to": str(target.inbound_id),
                              "ai_guard": {"exclude_history": exclude_history or stale},
                              "turn_status": "superseded" if stale else "current",
                              **({"catalog_refs": catalog_refs} if catalog_refs is not None and not stale else {}),
                              **({"commerce_state": commerce_state} if commerce_state is not None and not stale else {})}))
    except Exception:
        # Do not expose database values or re-send a message already accepted by Meta.
        logger.error("whatsapp.outbound_persistence_failed", extra={"inbound_id": str(target.inbound_id)})
        return
    logger.info("whatsapp.outbound_sent", extra={"inbound_id": str(target.inbound_id)})


def _superseded(db, target):
    from app.services.conversation_service import message_order_time
    ordered_time = message_order_time()
    anchor = db.execute(select(ordered_time.label("created_at"), Message.id).where(Message.id == target.inbound_id)).one_or_none()
    if anchor is None:
        return True
    return bool(db.scalar(select(Message.id).where(
        Message.conversation_id == target.conversation_id,
        Message.direction == MessageDirection.INBOUND,
        tuple_(ordered_time, Message.id) > tuple_(anchor.created_at, anchor.id)).limit(1)))
