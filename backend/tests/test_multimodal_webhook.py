from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import json
import logging

import pytest
from pydantic import SecretStr
from sqlalchemy import select, func

from app.ai.client import OpenAITextClient
from app.ai.dependencies import get_ai_service
from app.ai.multimodal import interpret
from app.ai.router import SalesRouter
from app.ai.service import AIService
from app.integrations.whatsapp.schemas import IncomingImage, ReplyTarget
from app.integrations.whatsapp.media import MediaError
from app.main import app
from app.models import Message, AIBudget
from app.models.enums import MessageDirection, MessageType
from app.services.whatsapp_service import send_automatic_reply
from tests.test_ai import sdk, settings
from tests.test_multimodal_media import image_payload, DATA, SIGNED
from tests.test_multimodal_commerce import observation
from tests.test_whatsapp_webhook import wa_setup, wa_db_client, signed_post


@pytest.mark.parametrize("failure", [None, "metadata", "vision"])
def test_real_webhook_image_dedup_and_worker_retry(wa_db_client, wa_setup, catalog_product, db_session, failure):
    config, outbound = wa_setup
    config.openai_model, config.openai_api_key = "mock-vision", SecretStr("mock-key")
    catalog_product(name="Pantalon Classic", color="bleu", price=229)
    provider = Mock()
    provider.respond.return_value = observation("wach 3ndkom b7al hada?")
    outbound.download_image.return_value = (DATA["image/jpeg"], "image/jpeg")
    if failure == "metadata":
        outbound.download_image.side_effect = MediaError()
    if failure == "vision":
        provider.respond.side_effect = RuntimeError("private-vision-error")
    service = AIService(provider, settings=config, catalog_enabled=True)
    app.dependency_overrides[get_ai_service] = lambda: service
    data = image_payload("wach 3ndkom b7al hada?")
    assert signed_post(wa_db_client, config, data).status_code == 200
    assert signed_post(wa_db_client, config, data).status_code == 200
    assert outbound.download_image.call_count == 1
    assert provider.respond.call_count == (0 if failure == "metadata" else 1)
    assert outbound.send_text_message.call_count == 1
    assert ("229.00 MAD" in outbound.send_text_message.call_args.args[1]) == (failure is None)
    paid = list(db_session.scalars(select(AIBudget).where(AIBudget.key.like("paid:%"))))
    assert len(paid) == 3 and all(counter.count == 1 for counter in paid)
    inbound = db_session.scalar(select(Message).where(Message.message_type == MessageType.IMAGE))
    assert inbound.metadata_["image"] == {"id": "123", "mime_type": "image/jpeg"}
    assert "vision" in inbound.metadata_["ai_guard"]["attempts"]
    assert "data:image" not in json.dumps(inbound.metadata_) and SIGNED not in json.dumps(inbound.metadata_)
    target = ReplyTarget(conversation_id=inbound.conversation_id, inbound_id=inbound.id, phone_number="212600000001")
    from contextlib import contextmanager
    @contextmanager
    def sessions():
        yield db_session
    send_automatic_reply(target, outbound, sessions, inbound.content, service, image=IncomingImage(id="123"))
    assert outbound.download_image.call_count == 1 and outbound.send_text_message.call_count == 1
    assert db_session.scalar(select(func.count()).select_from(Message).where(Message.message_type == MessageType.IMAGE)) == 1


def test_responses_image_uses_existing_sdk_without_logs(settings, sdk, caplog):
    caplog.set_level(logging.DEBUG)
    captured = []
    def response(**kwargs):
        captured.append(deepcopy(kwargs))
        logging.getLogger("openai._base_client").debug("private request %s", kwargs)
        logging.getLogger("httpx2").info("private transport secret-token")
        return SimpleNamespace(status="completed", output_text=observation("have this?").text, output=[])
    sdk[1].responses.create.side_effect = response
    admission = Mock()
    admission.reserve.return_value, admission.safe_record.return_value = "allowed", True
    service = AIService(OpenAITextClient(settings), settings=settings, catalog_enabled=True)
    router = SalesRouter(service, Mock(), admission, settings)
    media = Mock(download_image=Mock(return_value=(DATA["image/png"], "image/png")))
    result = interpret(router, "have this?", IncomingImage(id="123"), media)
    assert result.terms == ["pantalon"]
    request = captured[0]
    assert request["model"] == settings.openai_model and request["store"] is False
    assert request["max_output_tokens"] == settings.openai_max_output_tokens
    assert request["input"][0]["content"][0] == {"type": "input_text", "text": "have this?"}
    image = request["input"][0]["content"][1]
    assert image["type"] == "input_image" and image["detail"] == "low"
    assert image["image_url"].startswith("data:image/png;base64,")
    assert "data:image" not in caplog.text and "secret-token" not in caplog.text
    assert "tools" not in request
    assert request["text"]["format"]["strict"] is True
    assert sdk[0].call_args.kwargs["max_retries"] == 0
    admission.reserve.assert_called_once_with("vision", settings.openai_model)


def test_vision_budget_denial_prevents_download_and_provider(settings):
    admission, provider, media = Mock(), Mock(), Mock()
    admission.reserve.return_value = "suppressed"
    router = SalesRouter(AIService(provider, settings=settings), Mock(), admission, settings)
    assert interpret(router, "hello", IncomingImage(id="123"), media) is None
    media.download_image.assert_not_called()
    provider.respond.assert_not_called()
