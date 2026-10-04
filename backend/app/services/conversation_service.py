from uuid import UUID
from sqlalchemy import and_, or_, select, tuple_, func, cast, DateTime
from pydantic import ValidationError
from sqlalchemy.orm import Session, selectinload
from app.ai.schemas import AIHistoryMessage
from app.models.enums import MessageDirection, MessageType, SenderType
from app.models import Customer, Conversation, Message
from app.schemas.conversation import ConversationCreate, MessageCreate
from app.services.common import get, listing, transaction

CONVERSATION_LOAD = (selectinload(Conversation.customer),)


def message_order_time():
    """Server receipt order for live WhatsApp messages; legacy rows keep their time."""
    return func.coalesce(cast(Message.metadata_["received_at"].astext, DateTime(timezone=True)), Message.created_at)

def get_conversation(db: Session, conversation_id: UUID) -> Conversation:
    return get(db, Conversation, conversation_id, CONVERSATION_LOAD)

def list_conversations(db: Session, limit: int = 50, offset: int = 0) -> list[Conversation]:
    return listing(db, Conversation, limit, offset, CONVERSATION_LOAD)

def create_conversation(db: Session, data: ConversationCreate) -> Conversation:
    with transaction(db):
        get(db, Customer, data.customer_id)
        obj = Conversation(**data.model_dump())
        db.add(obj)
        db.flush()
        resource_id = obj.id
    return get_conversation(db, resource_id)

def add_message(db: Session, conversation_id: UUID, data: MessageCreate) -> Message:
    if data.metadata and ({"ai_guard", "catalog_refs", "commerce_state", "turn_status", "received_at", "checkout_state", "checkout_private", "cancellation_state", "confirmation_prompt"} & data.metadata.keys()):
        from app.services.common import ServiceError
        raise ServiceError(422, "Conversation control metadata is application-owned")
    with transaction(db):
        get(db, Conversation, conversation_id)
        obj = stage_message(db, conversation_id, data)
    return obj

def list_messages(db: Session, conversation_id: UUID, limit: int = 50, offset: int = 0) -> list[Message]:
    get(db, Conversation, conversation_id)
    return listing(db, Message, limit, offset, filters=(Message.conversation_id == conversation_id,))


def recent_ai_history(db: Session, conversation_id: UUID, inbound_id: UUID,
    limit: int = 12) -> list[AIHistoryMessage]:
    """Read a bounded snapshot; never load the conversation.messages relationship."""
    if not 0 <= limit <= 100:
        raise ValueError("Invalid history limit")
    if limit == 0:
        return []
    ordered_time = message_order_time()
    anchor = db.execute(select(ordered_time.label("created_at"), Message.id).where(
        Message.id == inbound_id, Message.conversation_id == conversation_id,
        Message.direction == MessageDirection.INBOUND,
        Message.sender_type == SenderType.CUSTOMER,
    )).one()
    inbound = and_(Message.direction == MessageDirection.INBOUND,
                   Message.sender_type == SenderType.CUSTOMER)
    outbound = and_(
        Message.direction == MessageDirection.OUTBOUND,
        func.length(func.trim(Message.external_message_id)) > 0,
        Message.metadata_["provider"].astext == "whatsapp",
        or_(Message.sender_type.in_([SenderType.AI, SenderType.HUMAN]),
            and_(Message.sender_type == SenderType.SYSTEM,
                 Message.metadata_["in_reply_to"].astext.op("~")(
                     r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"))),
    )
    rows = db.scalars(select(Message).where(
        Message.conversation_id == conversation_id,
        func.coalesce(Message.metadata_["ai_guard"]["exclude_history"].astext, "false") != "true",
        func.coalesce(Message.metadata_["checkout_private"].astext, "false") != "true",
        Message.id != inbound_id,
        tuple_(ordered_time, Message.id) < tuple_(anchor.created_at, anchor.id),
        Message.message_type == MessageType.TEXT,
        Message.content.is_not(None), Message.content.op("~")(r"[^[:space:]]"),
        or_(inbound, outbound),
    ).order_by(ordered_time.desc(), Message.id.desc()).limit(limit)).all()
    history = []
    for row in reversed(rows):
        try:
            history.append(AIHistoryMessage(
                role="user" if row.direction == MessageDirection.INBOUND else "assistant",
                content=row.content))
        except ValidationError:
            # Invalid legacy text must not fail the entire reply or reach the provider.
            continue
    return history


def resolve_whatsapp_conversation(db: Session, customer_id: UUID) -> Conversation:
    """Caller holds the customer row lock to serialize conversation creation."""
    from sqlalchemy import select
    from app.models.enums import ConversationChannel, ConversationStatus

    conversation = db.scalar(select(Conversation).where(
        Conversation.customer_id == customer_id,
        Conversation.channel == ConversationChannel.WHATSAPP,
        Conversation.status.in_([ConversationStatus.ACTIVE, ConversationStatus.WAITING_HUMAN]),
    ).order_by(Conversation.created_at, Conversation.id).limit(1))
    if conversation is None:
        conversation = Conversation(customer_id=customer_id, channel=ConversationChannel.WHATSAPP,
                                    status=ConversationStatus.ACTIVE)
        db.add(conversation)
        db.flush()
    return conversation


def stage_message(db: Session, conversation_id: UUID, data: MessageCreate) -> Message:
    """Add a message to an already resolved conversation without committing."""
    obj = Message(conversation_id=conversation_id, metadata_=data.metadata,
                  **data.model_dump(exclude={"metadata"}))
    db.add(obj)
    return obj


def recent_catalog_refs(db, conversation_id, inbound_id):
    """Latest eligible outbound, never skip a newer clarification to reuse an old list.

    Two inbound messages since that presentation are ambiguous (overlapping turns).
    Text history is intentionally not used to reconstruct identities.
    """
    from app.ai.catalog_schemas import CatalogRefs
    ordered_time = message_order_time()
    anchor = db.execute(select(ordered_time.label("created_at"), Message.id).where(
        Message.id == inbound_id, Message.conversation_id == conversation_id)).one()
    row = db.scalars(select(Message).where(
        Message.conversation_id == conversation_id,
        Message.direction == MessageDirection.OUTBOUND,
        Message.metadata_["provider"].astext == "whatsapp",
        Message.metadata_["catalog_refs"].is_not(None),
        func.length(Message.external_message_id) > 0,
        func.coalesce(Message.metadata_["ai_guard"]["exclude_history"].astext, "false") != "true",
        tuple_(ordered_time, Message.id) < tuple_(anchor.created_at, anchor.id),
    ).order_by(ordered_time.desc(), Message.id.desc()).limit(1)).first()
    if row is None:
        return CatalogRefs(), None
    intervening = db.scalar(select(func.count()).select_from(Message).where(
        Message.conversation_id == conversation_id, Message.direction == MessageDirection.INBOUND,
        tuple_(ordered_time, Message.id) > tuple_(row.created_at, row.id),
        tuple_(ordered_time, Message.id) < tuple_(anchor.created_at, anchor.id)))
    if intervening:
        return CatalogRefs(), None
    try:
        import json
        return CatalogRefs.model_validate_json(json.dumps(row.metadata_["catalog_refs"])), row.id
    except (ValueError, TypeError):
        return CatalogRefs(), None
