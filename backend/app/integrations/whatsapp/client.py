import re
from typing import Protocol
import httpx
from app.core.config import Settings
from app.integrations.whatsapp.security import require_secret
from app.services.common import ServiceError

class WhatsAppAPIError(Exception):
    """Sanitized outbound failure; never contains provider bodies or credentials."""

class TextMessageClient(Protocol):
    def send_text_message(self, to: str, text: str) -> str: ...

class WhatsAppClient:
    def __init__(self, settings: Settings, http_client: httpx.Client) -> None:
        self._token = require_secret(settings.whatsapp_access_token, "WHATSAPP_ACCESS_TOKEN")
        version = settings.whatsapp_api_version or ""
        phone_id = settings.whatsapp_phone_number_id or ""
        if not re.fullmatch(r"v[0-9]+\.[0-9]+", version) or not re.fullmatch(r"[0-9]+", phone_id):
            raise ServiceError(503, "Configure WHATSAPP_API_VERSION and WHATSAPP_PHONE_NUMBER_ID")
        self._url = f"https://graph.facebook.com/{version}/{phone_id}/messages"
        self._http = http_client

    def send_text_message(self, to: str, text: str) -> str:
        try:
            response = self._http.post(self._url,
                headers={"Authorization": f"Bearer {self._token}"},
                json={"messaging_product": "whatsapp", "recipient_type": "individual",
                      "to": to, "type": "text", "text": {"preview_url": False, "body": text}},
                timeout=httpx.Timeout(5.0, connect=3.0), follow_redirects=False)
        except httpx.RequestError:
            raise WhatsAppAPIError("WhatsApp transport failure") from None
        if not 200 <= response.status_code < 300:
            raise WhatsAppAPIError(f"WhatsApp returned HTTP {response.status_code}")
        try:
            message_id = response.json()["messages"][0]["id"]
            if not isinstance(message_id, str) or not 1 <= len(message_id) <= 255:
                raise ValueError
        except (ValueError, KeyError, IndexError, TypeError):
            raise WhatsAppAPIError("WhatsApp returned an invalid message response") from None
        return message_id
