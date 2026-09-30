from contextlib import contextmanager
from unittest.mock import Mock
import pytest
from app.ai.dependencies import get_ai_service
from app.ai.service import AIService
from app.db.session import get_db
from app.main import app
from app.integrations.whatsapp.dependencies import get_reply_session_factory
from tests.test_whatsapp_webhook import wa_setup, wa_client, payload, signed_post
from tests.test_catalog_orchestration import tool, plan


@pytest.mark.parametrize("failure", [None, "provider", "catalog"])
def test_catalog_webhook_ack_duplicate_and_request_session_closed(wa_client, wa_setup, catalog_env,
        catalog_product, monkeypatch, failure):
    catalog, _, active = catalog_env
    settings, outbound = wa_setup
    # All persistence/network points are mocked except catalog PostgreSQL reads.
    p, _ = catalog_product()
    request_active = []
    def request_db():
        request_active.append(True)
        try:
            yield Mock()
        finally:
            request_active.pop()
    app.dependency_overrides[get_db] = request_db
    app.dependency_overrides[get_reply_session_factory] = lambda: catalog.sessions
    monkeypatch.setattr("app.api.routes.whatsapp.persist_inbound", Mock(side_effect=[catalog.target, None]))
    monkeypatch.setattr("app.services.whatsapp_service.AIAdmission", lambda *args: admission)
    admission = Mock()
    admission.begin.return_value = admission.reserve.return_value = "allowed"
    admission.safe_record.return_value = True
    provider = Mock()
    results = [tool(), plan(p)]
    def respond(*args, **kwargs):
        assert not request_active and not active
        if failure == "provider":
            raise RuntimeError("private provider error")
        return results.pop(0)
    provider.respond.side_effect = respond
    service = AIService(provider, settings=catalog.settings, catalog_enabled=True)
    app.dependency_overrides[get_ai_service] = lambda: service
    if failure == "catalog":
        monkeypatch.setattr("app.services.catalog_service.CatalogService.search", Mock(side_effect=RuntimeError("private db error")))
    original_send = outbound.send_text_message.side_effect
    def send(*args):
        assert not request_active and not active
        return original_send(*args)
    outbound.send_text_message.side_effect = send
    data = payload()
    data["entry"][0]["changes"][0]["value"]["messages"][0]["text"]["body"] = "bghit cadeau"
    assert signed_post(wa_client, settings, data).status_code == 200
    assert signed_post(wa_client, settings, data).status_code == 200
    assert outbound.send_text_message.call_count == 1
    reply = outbound.send_text_message.call_args.args[1]
    assert "private" not in reply
    assert ("100.00 MAD" in reply) == (failure is None)
