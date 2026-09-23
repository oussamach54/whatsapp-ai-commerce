from uuid import UUID
from fastapi import APIRouter
from app.api.dependencies import Database, Limit, Offset
from app.schemas.conversation import ConversationCreate, ConversationRead, MessageCreate, MessageRead, UUID
from app.services import conversation_service as service

router = APIRouter(tags=["Conversations"])

@router.post("/conversations", response_model=ConversationRead, status_code=201)
def create_conversation(db: Database, data: ConversationCreate) -> ConversationRead:
    return service.create_conversation(db, data)

@router.get("/conversations", response_model=list[ConversationRead], status_code=200)
def list_conversations(db: Database, limit: Limit = 50, offset: Offset = 0) -> list[ConversationRead]:
    return service.list_conversations(db, limit, offset)

@router.get("/conversations/{conversation_id}", response_model=ConversationRead, status_code=200)
def get_conversation(db: Database, conversation_id: UUID) -> ConversationRead:
    return service.get_conversation(db, conversation_id)

@router.post("/conversations/{conversation_id}/messages", response_model=MessageRead, status_code=201)
def add_message(db: Database, conversation_id: UUID, data: MessageCreate) -> MessageRead:
    return service.add_message(db, conversation_id, data)

@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageRead], status_code=200)
def list_messages(db: Database, conversation_id: UUID, limit: Limit = 50, offset: Offset = 0) -> list[MessageRead]:
    return service.list_messages(db, conversation_id, limit, offset)
