from contextlib import contextmanager
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from uuid import uuid4

import httpx2
import pytest
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import SecretStr

from app.ai.client import OpenAITextClient
from app.ai.prompts import FALLBACK_REPLY, SYSTEM_PROMPT
from app.ai.service import AIService
from app.core.config import Settings
from app.integrations.whatsapp.schemas import ReplyTarget
from app.services.whatsapp_service import send_automatic_reply


@pytest.fixture
def settings():
    return Settings(_env_file=None, database_host="localhost", database_name="test",
        database_user="test", database_password=SecretStr("db-secret-sentinel"),
        openai_api_key=SecretStr("ai-secret-sentinel"), openai_model="test-model",
        whatsapp_access_token=SecretStr("wa-secret-sentinel"),
        whatsapp_app_secret=SecretStr("app-secret-sentinel"),
        whatsapp_verify_token=SecretStr("verify-secret-sentinel"))


@pytest.fixture
def sdk(monkeypatch):
    factory = MagicMock()
    instance = factory.return_value.__enter__.return_value
    instance.responses.create.return_value = SimpleNamespace(status="completed", output_text="Bonjour !")
    monkeypatch.setattr("app.ai.client.OpenAI", factory)
    return factory, instance


@pytest.mark.parametrize("message,reply,instruction", [
    ("salam, chno t9der t3awni fih?", "Salam! Chno bghiti t3ref?",
        "Moroccan Darija in Latin characters (including Arabizi digits): reply in natural Moroccan Darija using Latin characters."),
    ("سلام، شنو تقدر تعاوني فيه؟", "سلام! شنو بغيتي تعرف؟",
        "Moroccan Darija in Arabic script: reply in Moroccan Darija using Arabic script."),
    ("Bonjour, pouvez-vous m'aider ?", "Bonjour ! Comment je peux vous aider ?",
        "French: reply in French."),
    ("Hello, can you help me?", "Hello! How can I help?",
        "English: reply in English."),
    ("Salam, bghit des infos sur la livraison svp", "Salam! Ma 3ndich les infos sur la livraison daba.",
        "Mixed Darija Latin and French: reply naturally in the same Darija Latin/French style."),
])
def test_languages_and_request_boundaries(settings, sdk, message, reply, instruction):
    factory, client = sdk
    client.responses.create.return_value.output_text = "  " + reply + "  "
    conversation_id = uuid4()
    assert AIService(OpenAITextClient(settings)).generate_reply(message, conversation_id) == reply
    factory.assert_called_once_with(api_key="ai-secret-sentinel", base_url="https://api.openai.com/v1",
        timeout=20.0, max_retries=0)
    client.responses.create.assert_called_once_with(model="test-model", instructions=SYSTEM_PROMPT,
        input=[{"role": "user", "content": message}], max_output_tokens=512, store=False)
    # Check the instructions actually sent, not only the canned model response.
    instructions = " ".join(client.responses.create.call_args.kwargs["instructions"].split())
    assert instruction in instructions
    assert "Do not translate Darija Latin into Arabic script unless the customer switches to Arabic script." in instructions
    assert "Preserve the customer's language, script, and writing style." in instructions
    assert "Keep replies friendly and natural; avoid overly formal language." in instructions
    assert str(conversation_id) not in str(client.responses.create.call_args)
    factory.return_value.__exit__.assert_called_once()


@pytest.mark.parametrize("output", ["", " \n\t ", None, 42, [], {}, "x" * 4097, "\u200b", "bad\x00text"])
def test_invalid_output_uses_fallback(settings, sdk, output, caplog):
    sdk[1].responses.create.return_value.output_text = output
    assert AIService(OpenAITextClient(settings)).generate_reply("Bonjour") == FALLBACK_REPLY
    assert caplog.records[-1].failure_category == "invalid_response"


@pytest.mark.parametrize("status", ["incomplete", "failed", "queued", None])
def test_incomplete_response_not_sent(settings, sdk, status, caplog):
    sdk[1].responses.create.return_value.status = status
    assert AIService(OpenAITextClient(settings)).generate_reply("Bonjour") == FALLBACK_REPLY
    assert caplog.records[-1].failure_category == "invalid_response"


@pytest.mark.parametrize("kind,category,status", [
    ("timeout", "timeout", None), ("connection", "unavailable", None),
    ("api", "api_error", 429), ("unexpected", "unexpected_error", None),
])
def test_provider_failures_are_safe(settings, sdk, kind, category, status, caplog):
    secret_text = "ai-secret-sentinel wa-secret-sentinel app-secret-sentinel verify-secret-sentinel db-secret-sentinel Authorization"
    request = httpx2.Request("POST", "https://api.openai.com/v1/responses", headers={"Authorization": secret_text})
    errors = {
        "timeout": APITimeoutError(request=request),
        "connection": APIConnectionError(message=secret_text, request=request),
        "api": APIStatusError(secret_text, response=httpx2.Response(429, request=request), body={"secret": secret_text}),
        "unexpected": RuntimeError(secret_text),
    }
    sdk[1].responses.create.side_effect = errors[kind]
    assert AIService(OpenAITextClient(settings)).generate_reply("private-customer-message") == FALLBACK_REPLY
    record = caplog.records[-1]
    assert record.failure_category == category and record.http_status == status
    assert record.fallback_used is True and record.exc_info is None
    for forbidden in secret_text.split() + ["private-customer-message"]:
        assert forbidden not in caplog.text
    sdk[1].responses.create.assert_called_once()


@pytest.mark.parametrize("field,value", [("openai_api_key", None), ("openai_api_key", SecretStr("  ")),
    ("openai_model", None), ("openai_model", " ")])
def test_unconfigured_ai_does_not_initialize_sdk(settings, sdk, field, value, caplog):
    setattr(settings, field, value)
    assert AIService(OpenAITextClient(settings)).generate_reply("Bonjour") == FALLBACK_REPLY
    sdk[0].assert_not_called()
    assert caplog.records[-1].failure_category == "not_configured"


def test_sdk_initialization_failure_falls_back(settings, sdk):
    sdk[0].side_effect = RuntimeError("sensitive setup detail")
    assert AIService(OpenAITextClient(settings)).generate_reply("Bonjour") == FALLBACK_REPLY


def test_invalid_input_does_not_call_provider():
    client = Mock()
    assert AIService(client).generate_reply(" ") == FALLBACK_REPLY
    client.generate.assert_not_called()


@pytest.mark.parametrize("failed", [False, True])
def test_generated_or_fallback_text_is_sent_and_persisted(monkeypatch, failed, settings, allowed_admission):
    provider = Mock()
    provider.generate.return_value = "Salam! Kifach n3awnk?"
    if failed:
        provider.generate.side_effect = RuntimeError("provider-secret")
    expected = FALLBACK_REPLY if failed else provider.generate.return_value
    target = ReplyTarget(conversation_id=uuid4(), inbound_id=uuid4(), phone_number="212600000001")
    outbound, db = Mock(), Mock()
    outbound.send_text_message.return_value = "wamid.ai-reply"
    stage = Mock()
    monkeypatch.setattr("app.services.whatsapp_service.stage_message", stage)

    @contextmanager
    def session_factory():
        # Only the outbound write session opens after network calls.
        yield db

    monkeypatch.setattr("app.services.whatsapp_service.recent_ai_history", Mock(return_value=[]))

    send_automatic_reply(target, outbound, session_factory, "Salam, bghit cadeau", AIService(provider, settings=settings))
    request = provider.generate.call_args.args[0]
    assert request.message_text == "Salam, bghit cadeau" and request.conversation_id == target.conversation_id
    saved = stage.call_args.args[2]
    assert saved.content == expected and saved.external_message_id == "wamid.ai-reply"
    assert saved.metadata["in_reply_to"] == str(target.inbound_id)
    db.commit.assert_called_once()


def test_prompt_business_limits():
    for instruction in ["French", "English", "Moroccan Darija", "Latin", "customer's language",
        "Never invent", "prices", "stock", "delivery", "discounts", "company policies",
        "Never pretend an order", "untrusted", "You have no verified catalog",
        "Clearly say when information is unavailable", "one to three short sentences"]:
        assert instruction in SYSTEM_PROMPT


@pytest.mark.parametrize("status", [200, 429])
def test_real_sdk_with_mock_transport(settings, monkeypatch, status):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/v1/responses"
        body = json.loads(request.content)
        assert body["input"] == [{"role": "user", "content": "Hello"}]
        assert body["store"] is False
        if status == 429:
            return httpx2.Response(429, json={"error": {"message": "provider-private-detail", "type": "rate_limit_error"}})
        return httpx2.Response(200, json={"id": "resp_test", "object": "response", "created_at": 1,
            "status": "completed", "model": "test-model", "output": [{"type": "message",
                "id": "msg_test", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": "Hello!", "annotations": []}]}]})

    def factory(**kwargs):
        return OpenAI(**kwargs, http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))

    monkeypatch.setattr("app.ai.client.OpenAI", factory)
    result = AIService(OpenAITextClient(settings)).generate_reply("Hello")
    assert result == ("Hello!" if status == 200 else FALLBACK_REPLY)
    assert len(calls) == 1


def test_whatsapp_diagnostics_redact_openai_key(settings):
    import httpx
    from app.integrations.whatsapp.client import WhatsAppAPIError, WhatsAppClient

    settings.whatsapp_phone_number_id = "123456789"
    settings.whatsapp_api_version = "v99.0"
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(400,
        json={"error": {"message": "ai-secret-sentinel"}}))) as http:
        with pytest.raises(WhatsAppAPIError) as caught:
            WhatsAppClient(settings, http).send_text_message("212600000001", "Hello")
    assert caught.value.diagnostics["meta_error_message"] == "[REDACTED]"
