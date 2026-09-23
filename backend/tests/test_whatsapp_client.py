import json
import secrets
import httpx
import pytest
from app.core.config import get_settings
from app.integrations.whatsapp.client import WhatsAppClient, WhatsAppAPIError
from app.services.common import ServiceError

@pytest.fixture
def whatsapp_settings():
    from pydantic import SecretStr
    return get_settings().model_copy(update={
        "whatsapp_access_token": SecretStr(secrets.token_hex(24)),
        "whatsapp_phone_number_id": "123456789", "whatsapp_api_version": "v99.0"})

def test_send_text_format(whatsapp_settings):
    def handler(request):
        assert str(request.url) == "https://graph.facebook.com/v99.0/123456789/messages"
        assert request.headers["Authorization"] == "Bearer " + whatsapp_settings.whatsapp_access_token.get_secret_value()
        assert json.loads(request.content) == {"messaging_product": "whatsapp", "recipient_type": "individual",
            "to": "212600000001", "type": "text", "text": {"body": "Bonjour", "preview_url": False}}
        assert request.extensions["timeout"]["read"] == 5.0
        return httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        assert WhatsAppClient(whatsapp_settings, http).send_text_message("212600000001", "Bonjour") == "wamid.out"

@pytest.mark.parametrize("status", [400, 401, 429, 500, 302])
def test_provider_error_sanitized(whatsapp_settings, status):
    secret = whatsapp_settings.whatsapp_access_token.get_secret_value()
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, text=secret))) as http:
        with pytest.raises(WhatsAppAPIError) as error:
            WhatsAppClient(whatsapp_settings, http).send_text_message("212600000001", "Hello")
        assert secret not in str(error.value)
        assert str(status) in str(error.value)

@pytest.mark.parametrize("body", [{}, {"messages": []}, {"messages": [{"id": None}]}, {"messages": [{"id": "x" * 256}]}, []])
def test_invalid_provider_response(whatsapp_settings, body):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body))) as http:
        with pytest.raises(WhatsAppAPIError):
            WhatsAppClient(whatsapp_settings, http).send_text_message("212600000001", "Hello")

def test_timeout_no_retry(whatsapp_settings):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("sensitive provider detail", request=request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(WhatsAppAPIError, match="transport failure") as error:
            WhatsAppClient(whatsapp_settings, http).send_text_message("212600000001", "Hello")
        assert "sensitive" not in str(error.value)
        assert len(calls) == 1

@pytest.mark.parametrize("field", ["whatsapp_access_token", "whatsapp_phone_number_id", "whatsapp_api_version"])
def test_required_outbound_configuration(whatsapp_settings, field):
    with httpx.Client(transport=httpx.MockTransport(lambda request: pytest.fail("Unexpected request"))) as http:
        with pytest.raises(ServiceError) as error:
            WhatsAppClient(whatsapp_settings.model_copy(update={field: None}), http)
        assert error.value.status_code == 503
