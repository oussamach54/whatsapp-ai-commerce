import logging
from datetime import datetime, timezone
from pydantic import ValidationError
from app.integrations.whatsapp.schemas import IncomingText, IncomingImage

logger = logging.getLogger(__name__)

def _objects(value: object) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

def parse_messages(payload: object, phone_number_id: str) -> list[IncomingText]:
    """Ignore unknown event shapes without trusting nested provider input."""
    result = []
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        logger.info("whatsapp.unsupported_event")
        return result
    for entry in _objects(payload.get("entry")):
        for change in _objects(entry.get("changes")):
            value = change.get("value")
            if change.get("field") != "messages" or not isinstance(value, dict):
                logger.info("whatsapp.unsupported_event")
                continue
            metadata = value.get("metadata")
            if not isinstance(metadata, dict) or metadata.get("phone_number_id") != phone_number_id:
                logger.info("whatsapp.unmatched_phone_number_id")
                continue
            if value.get("statuses"):
                logger.info("whatsapp.status_event_ignored")
            for message in _objects(value.get("messages")):
                text = message.get("text")
                image = message.get("image") if message.get("type") == "image" else None
                if not isinstance(image, dict) and (message.get("type") != "text" or not isinstance(text, dict)):
                    logger.info("whatsapp.unsupported_message")
                    continue
                timestamp = None
                try:
                    if message.get("timestamp") is not None:
                        timestamp = datetime.fromtimestamp(int(message["timestamp"]), tz=timezone.utc)
                    result.append(IncomingText(external_message_id=message.get("id"),
                        phone_number=message.get("from"),
                        text=(image.get("caption") or "[image]") if image is not None else text.get("body"),
                        image=IncomingImage(id=image.get("id"), mime_type=image.get("mime_type")) if image is not None else None,
                        timestamp=timestamp, phone_number_id=phone_number_id))
                except (ValidationError, ValueError, TypeError, OverflowError, OSError):
                    logger.info("whatsapp.malformed_message_ignored")
    return result
