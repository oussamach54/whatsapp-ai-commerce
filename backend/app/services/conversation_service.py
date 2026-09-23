from uuid import UUID
from sqlalchemy.orm import Session, selectinload
from app.models import Customer, Conversation, Message
from app.schemas.conversation import ConversationCreate, MessageCreate
from app.services.common import get, listing, transaction

CONVERSATION_LOAD = (selectinload(Conversation.customer),)

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
    with transaction(db):
        get(db, Conversation, conversation_id)
        obj = stage_message(db, conversation_id, data)
    return obj

def list_messages(db: Session, conversation_id: UUID, limit: int = 50, offset: int = 0) -> list[Message]:
    get(db, Conversation, conversation_id)
    return listing(db, Message, limit, offset, filters=(Message.conversation_id == conversation_id,))


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
