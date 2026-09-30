import re
import json
import unicodedata
from typing import Literal, Protocol
from urllib.parse import quote, quote_plus
import httpx
from app.core.config import Settings
from app.integrations.whatsapp.security import require_secret
from app.services.common import ServiceError

class WhatsAppAPIError(Exception):
    """Sanitized outbound failure; never contains provider bodies or credentials."""

    def __init__(self, message: str, *, failure_category: Literal["transport_error", "http_error", "invalid_response"] = "transport_error",
        http_status: int | None = None, meta_error_code: int | None = None,
        meta_error_subcode: int | None = None, meta_error_message: str | None = None,
        transport_exception: str | None = None) -> None:
        super().__init__(message)
        self.diagnostics = {
            "failure_category": failure_category,
            "http_status": http_status,
            "meta_error_code": meta_error_code,
            "meta_error_subcode": meta_error_subcode,
            "meta_error_message": meta_error_message,
        }
        if transport_exception is not None:
            self.diagnostics["transport_exception"] = transport_exception

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
        # Used only for redaction, never included in diagnostics.
        self._sensitive_values = [settings.database_user, settings.database_password.get_secret_value()]
        for secret in (settings.whatsapp_access_token, settings.whatsapp_app_secret, settings.whatsapp_verify_token, settings.openai_api_key):
            if secret is not None:
                self._sensitive_values.append(secret.get_secret_value())

    def _safe_meta_message(self, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        # Fail closed on oversized or credential-bearing messages. Inspect before
        # truncation so a credential cannot be partially exposed at the boundary.
        if len(value) > 4096:
            return "[REDACTED]"
        for secret in self._sensitive_values:
            if secret and any(candidate in value for candidate in (
                secret, quote(secret, safe=""), quote_plus(secret, safe=""), json.dumps(secret)[1:-1],
            )):
                return "[REDACTED]"
        if re.search(r"authorization|bearer\s|access[_ -]?token|app[_ -]?secret|verify[_ -]?token|password|postgres(?:ql)?://|https?://", value, re.IGNORECASE):
            return "[REDACTED]"
        # Remove control/format characters, including newlines and terminal escapes.
        value = "".join(" " if unicodedata.category(char).startswith("C") else char for char in value)
        return " ".join(value.split())[:512]

    def _meta_diagnostics(self, response: httpx.Response) -> dict:
        try:
            payload = response.json()
        except (ValueError, UnicodeError):
            return {}
        error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error, dict):
            return {}
        # Only allow numeric codes; arbitrary values might contain credentials.
        code, subcode = error.get("code"), error.get("error_subcode")
        return {
            "meta_error_code": code if type(code) is int else None,
            "meta_error_subcode": subcode if type(subcode) is int else None,
            "meta_error_message": self._safe_meta_message(error.get("message")),
        }

    def send_text_message(self, to: str, text: str) -> str:
        try:
            response = self._http.post(self._url,
                headers={"Authorization": f"Bearer {self._token}"},
                json={"messaging_product": "whatsapp", "recipient_type": "individual",
                      "to": to, "type": "text", "text": {"preview_url": False, "body": text}},
                timeout=httpx.Timeout(5.0, connect=3.0), follow_redirects=False)
        except httpx.RequestError as exc:
            # Fixed library names only: never raw exception text, request data,
            # or an arbitrary subclass name supplied by a transport.
            kinds = (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout,
                httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.CloseError,
                httpx.RemoteProtocolError, httpx.LocalProtocolError, httpx.ProxyError,
                httpx.UnsupportedProtocol, httpx.DecodingError, httpx.TooManyRedirects)
            kind = next((cls.__name__ for cls in kinds if isinstance(exc, cls)), "RequestError")
            raise WhatsAppAPIError("WhatsApp transport failure", failure_category="transport_error",
                transport_exception=kind) from None
        if not 200 <= response.status_code < 300:
            raise WhatsAppAPIError(f"WhatsApp returned HTTP {response.status_code}",
                failure_category="http_error", http_status=response.status_code,
                **self._meta_diagnostics(response))
        try:
            message_id = response.json()["messages"][0]["id"]
            if not isinstance(message_id, str) or not 1 <= len(message_id) <= 255:
                raise ValueError
        except (ValueError, KeyError, IndexError, TypeError):
            raise WhatsAppAPIError("WhatsApp returned an invalid message response",
                failure_category="invalid_response", http_status=response.status_code,
                **self._meta_diagnostics(response)) from None
        return message_id
