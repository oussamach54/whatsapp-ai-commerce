import json
import logging
from unittest.mock import Mock
from urllib.parse import quote
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations.whatsapp.client import WhatsAppAPIError, WhatsAppClient
from app.integrations.whatsapp.schemas import ReplyTarget
from app.services.whatsapp_service import send_automatic_reply


@pytest.fixture
def settings():
    # Entirely synthetic credentials: never read deployment values in these tests.
    return Settings(_env_file=None, database_host="localhost", database_name="test",
        database_user="diagnostic-db-user", database_password=SecretStr("diagnostic-db/password"),
        whatsapp_access_token=SecretStr("diagnostic-access/token"),
        whatsapp_app_secret=SecretStr("diagnostic-app-secret"),
        whatsapp_verify_token=SecretStr("diagnostic-verify-token"),
        whatsapp_phone_number_id="123456789", whatsapp_api_version="v99.0")


def failure(settings, response):
    with httpx.Client(transport=httpx.MockTransport(lambda request: response)) as http:
        with pytest.raises(WhatsAppAPIError) as caught:
            WhatsAppClient(settings, http).send_text_message("212600000001", "Hello")
    return caught.value


def test_meta_fields_and_plain_console_logging(settings, caplog):
    error = failure(settings, httpx.Response(400, json={"error": {
        "code": 131030, "error_subcode": 123, "message": "Recipient is not allowed",
        "error_data": {"details": "raw-secret-sentinel"}, "fbtrace_id": "not-logged",
    }}))
    assert error.diagnostics == {
        "failure_category": "http_error", "http_status": 400,
        "meta_error_code": 131030, "meta_error_subcode": 123,
        "meta_error_message": "Recipient is not allowed",
    }
    client, session_factory = Mock(), Mock()
    client.send_text_message.side_effect = error
    target = ReplyTarget(conversation_id=uuid4(), inbound_id=uuid4(), phone_number="212600000001")
    with caplog.at_level(logging.WARNING):
        assert send_automatic_reply(target, client, session_factory) is None
    client.send_text_message.assert_called_once()
    session_factory.assert_not_called()
    record = caplog.records[-1]
    assert record.getMessage().startswith("whatsapp.outbound_failed ")
    assert json.loads(record.getMessage().split(" ", 1)[1]) == error.diagnostics
    for key, value in error.diagnostics.items():
        assert getattr(record, key) == value
    assert record.inbound_id == str(target.inbound_id)
    assert record.exc_info is None
    assert "raw-secret-sentinel" not in caplog.text
    assert "not-logged" not in caplog.text


@pytest.mark.parametrize("message", [
    "diagnostic-access/token", "diagnostic-app-secret", "diagnostic-verify-token",
    "diagnostic-db/password", "diagnostic-db-user", quote("diagnostic-access/token", safe=""),
    "Authorization: Bearer unknown-secret", "access_token=unknown-secret",
    "app_secret=unknown-secret", "verify_token=unknown-secret", "password=unknown-secret",
    "https://example.test/?credential=unknown-secret", "postgresql://other:unknown-secret@host/db",
    "x" * 500 + "diagnostic-access/token", "x" * 4097,
])
def test_sensitive_messages_are_redacted(settings, message, caplog):
    error = failure(settings, httpx.Response(401, json={"error": {"code": 190, "message": message}}))
    assert error.diagnostics["meta_error_message"] == "[REDACTED]"
    client = Mock()
    client.send_text_message.side_effect = error
    send_automatic_reply(ReplyTarget(conversation_id=uuid4(), inbound_id=uuid4(), phone_number="212600000001"), client, Mock())
    assert message not in caplog.text
    assert message not in str(error)
    assert "[REDACTED]" in caplog.text


@pytest.mark.parametrize("payload", [None, [], {}, {"error": []}, {"error": "raw"},
    {"error": {"code": "secret", "error_subcode": True, "message": {"secret": "value"}}}])
def test_unexpected_error_shapes_are_not_logged(settings, payload):
    error = failure(settings, httpx.Response(400, json=payload))
    assert error.diagnostics == {"failure_category": "http_error", "http_status": 400,
        "meta_error_code": None, "meta_error_subcode": None, "meta_error_message": None}


def test_message_controls_and_length(settings):
    error = failure(settings, httpx.Response(400, json={"error": {"message": "Bad\r\ninput\x1b\u202e " + "x" * 600}}))
    message = error.diagnostics["meta_error_message"]
    assert message.startswith("Bad input ")
    assert len(message) == 512
    assert not any(char in message for char in "\r\n\x1b\u202e")


@pytest.mark.parametrize("status,category", [(400, "http_error"), (200, "invalid_response")])
def test_non_json_responses(settings, status, category):
    error = failure(settings, httpx.Response(status, text="raw-response-sentinel"))
    assert error.diagnostics["failure_category"] == category
    assert error.diagnostics["http_status"] == status
    assert "raw-response-sentinel" not in str(error.diagnostics)


def test_transport_error_does_not_log_exception_text(settings, caplog):
    def handler(request):
        raise httpx.ConnectError("Authorization: Bearer transport-secret", request=request)
    sessions = Mock()
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        send_automatic_reply(ReplyTarget(conversation_id=uuid4(), inbound_id=uuid4(), phone_number="212600000001"),
            WhatsAppClient(settings, http), sessions)
    assert '"failure_category": "transport_error"' in caplog.text
    assert '"http_status": null' in caplog.text
    assert '"transport_exception": "ConnectError"' in caplog.text
    assert "transport-secret" not in caplog.text
    assert "Authorization" not in caplog.text
    sessions.assert_not_called()


@pytest.mark.parametrize("kind", [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout,
    httpx.PoolTimeout, httpx.ConnectError, httpx.ReadError, httpx.WriteError,
    httpx.RemoteProtocolError, httpx.ProxyError])
def test_transport_exception_type_is_safe_and_specific(settings, kind):
    def handler(request):
        raise kind("Authorization: Bearer private-transport-sentinel", request=request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(WhatsAppAPIError) as caught:
            WhatsAppClient(settings, http).send_text_message("212600000001", "test")
    assert caught.value.diagnostics["transport_exception"] == kind.__name__
    assert caught.value.diagnostics["http_status"] is None
    assert "private-transport-sentinel" not in str(caught.value.diagnostics)


def test_transport_custom_subclass_name_is_not_logged(settings):
    private_type = type("private_transport_sentinel", (httpx.RequestError,), {})
    def handler(request):
        raise private_type("private body", request=request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(WhatsAppAPIError) as caught:
            WhatsAppClient(settings, http).send_text_message("212600000001", "test")
    assert caught.value.diagnostics["transport_exception"] == "RequestError"
