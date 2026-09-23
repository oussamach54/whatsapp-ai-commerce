import json
import logging
from typing import Annotated
from collections.abc import Callable
from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool
from app.api.dependencies import Database
from app.integrations.whatsapp.client import TextMessageClient
from app.integrations.whatsapp.dependencies import WhatsAppSettings, get_whatsapp_client_factory, get_reply_session_factory
from app.integrations.whatsapp.parser import parse_messages
from app.integrations.whatsapp.security import validate_signature, verify_subscription
from app.services.common import ServiceError
from app.services.whatsapp_service import persist_inbound, send_automatic_reply, SessionFactory

router = APIRouter(prefix="/webhooks/whatsapp", tags=["WhatsApp"])
logger = logging.getLogger(__name__)

@router.get("", response_class=PlainTextResponse)
def verify_webhook(settings: WhatsAppSettings,
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> str:
    return verify_subscription(mode, token, challenge, settings.whatsapp_verify_token)

@router.post("")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks,
    db: Database, settings: WhatsAppSettings,
    client_factory: Annotated[Callable[[], TextMessageClient], Depends(get_whatsapp_client_factory)],
    session_factory: Annotated[SessionFactory, Depends(get_reply_session_factory)],
) -> dict[str, str]:
    body = await request.body()
    validate_signature(body, request.headers.get("x-hub-signature-256"), settings.whatsapp_app_secret)
    logger.info("whatsapp.webhook_received")
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise ServiceError(400, "Invalid webhook JSON") from None
    phone_id = settings.whatsapp_phone_number_id
    if not phone_id:
        raise ServiceError(503, "WhatsApp configuration missing: WHATSAPP_PHONE_NUMBER_ID")
    messages = parse_messages(payload, phone_id)
    # Resolve client only for text events; statuses require no outbound token.
    if messages:
        client = client_factory()
        for incoming in messages:
            target = await run_in_threadpool(persist_inbound, db, incoming)
            if target is not None:
                background_tasks.add_task(send_automatic_reply, target, client, session_factory)
    return {"status": "ok"}
