import json
from contextlib import contextmanager
from unittest.mock import Mock
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import select
from app.ai.client import OpenAITextClient
from app.ai.service import AIService
from app.ai.dependencies import get_ai_service
from app.ai.schemas import ProviderResult
from app.integrations.whatsapp.client import WhatsAppAPIError
from app.models import Message
from app.models.enums import MessageDirection
from app.services.whatsapp_service import send_automatic_reply
from tests.test_catalog_orchestration import tool, plan


@pytest.mark.parametrize("send_fails", [False, True])
def test_real_admission_pipeline_persistence_sessions_and_duplicate(catalog_env, catalog_product, db_session, send_fails):
    catalog, inbound, active = catalog_env
    p, v = catalog_product()
    provider = Mock()
    responses = [tool(), plan(p)]
    def respond(*args, **kwargs):
        assert not active
        guard = inbound.metadata_["ai_guard"]
        assert any(a["status"] == "reserved" for a in guard["attempts"].values())
        return responses.pop(0)
    provider.respond.side_effect = respond
    service = AIService(provider, settings=catalog.settings, catalog_enabled=True)
    def send(*args):
        assert not active
        if send_fails:
            raise WhatsAppAPIError("failed")
        return uuid4().hex
    outbound = Mock(send_text_message=Mock(side_effect=send))
    send_automatic_reply(catalog.target, outbound, catalog.sessions, "bghit cadeau", service)
    rows = db_session.scalars(select(Message).where(Message.conversation_id == inbound.conversation_id,
                                                   Message.direction == MessageDirection.OUTBOUND)).all()
    assert len(rows) == (0 if send_fails else 1)
    if rows:
        assert rows[0].metadata_["catalog_refs"]["presented"][0]["product_id"] == str(p.id)
        assert "100.00 MAD" in rows[0].content
        assert "function_call" not in json.dumps(rows[0].metadata_)
    send_automatic_reply(catalog.target, outbound, catalog.sessions, "bghit cadeau", service)
    assert outbound.send_text_message.call_count == 1
    assert provider.respond.call_count == 2


def test_production_dependency_enables_catalog(catalog_env):
    assert get_ai_service(catalog_env[0].settings).catalog_enabled


def test_real_sdk_tool_contract_with_mock_transport(catalog_env, catalog_product, monkeypatch):
    import httpx2
    from openai import OpenAI
    from app.ai.catalog_orchestrator import run_catalog
    catalog, _, active = catalog_env
    p, _ = catalog_product()
    calls = []
    def handle(request):
        assert not active
        body = json.loads(request.content)
        calls.append(body)
        assert body["store"] is False and body["parallel_tool_calls"] is False
        assert len(body["tools"]) == 2
        if len(calls) == 1:
            output = [{"type": "function_call", "id": "fc1", "call_id": "call1", "name": "search_products",
                       "arguments": tool().items[-1]["arguments"], "status": "completed"}]
        else:
            assert any(item.get("type") == "function_call_output" and item["call_id"] == "call1" for item in body["input"])
            output = [{"type": "message", "id": "msg1", "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text", "text": plan(p).text, "annotations": []}]}]
        return httpx2.Response(200, json={"id": "resp1", "object": "response", "created_at": 1,
            "status": "completed", "model": "mock-model", "output": output})
    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        return OpenAI(**kwargs, http_client=httpx2.Client(transport=httpx2.MockTransport(handle)))
    monkeypatch.setattr("app.ai.client.OpenAI", factory)
    service = AIService(OpenAITextClient(catalog.settings), settings=catalog.settings, catalog_enabled=True)
    admission = Mock()
    admission.reserve.return_value = "allowed"
    admission.safe_record.return_value = True
    reply = run_catalog(service, "produits", [], "french", admission, catalog)
    assert "100.00 MAD" in reply.text and len(calls) == 2
