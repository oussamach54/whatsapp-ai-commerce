import hashlib
import hmac
import re
from pydantic import SecretStr
from app.services.common import ServiceError

def require_secret(value: SecretStr | None, setting: str) -> str:
    if value is None or not value.get_secret_value().strip():
        raise ServiceError(503, f"WhatsApp configuration missing: {setting}")
    return value.get_secret_value()

def verify_subscription(mode: str | None, token: str | None, challenge: str | None, configured_token: SecretStr | None) -> str:
    expected = require_secret(configured_token, "WHATSAPP_VERIFY_TOKEN")
    if mode != "subscribe" or token is None or challenge is None or not hmac.compare_digest(token.encode(), expected.encode()):
        raise ServiceError(403, "Webhook verification rejected")
    return challenge

def validate_signature(body: bytes, signature: str | None, app_secret: SecretStr | None) -> None:
    secret = require_secret(app_secret, "WHATSAPP_APP_SECRET")
    if signature is None or re.fullmatch(r"sha256=[0-9a-fA-F]{64}", signature) is None:
        raise ServiceError(403, "Invalid webhook signature")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature[7:].lower(), expected):
        raise ServiceError(403, "Invalid webhook signature")
