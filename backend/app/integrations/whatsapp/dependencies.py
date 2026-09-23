from typing import Annotated
from collections.abc import Callable
from fastapi import Depends, Request
from app.core.config import Settings, get_settings
from app.db.session import SessionLocal
from app.integrations.whatsapp.client import WhatsAppClient, TextMessageClient
from app.services.whatsapp_service import SessionFactory

WhatsAppSettings = Annotated[Settings, Depends(get_settings)]

def get_whatsapp_client_factory(request: Request, settings: WhatsAppSettings) -> Callable[[], TextMessageClient]:
    return lambda: WhatsAppClient(settings, request.app.state.whatsapp_http)

def get_reply_session_factory() -> SessionFactory:
    return SessionLocal
