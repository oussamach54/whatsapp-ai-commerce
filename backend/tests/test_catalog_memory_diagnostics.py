from unittest.mock import Mock

import pytest
from sqlalchemy.exc import OperationalError

from app.ai import conversation_engine
from app.ai.catalog_renderer import words


@pytest.mark.parametrize("database_error", [False, True])
def test_memory_failure_logs_only_code_metadata(monkeypatch, caplog, database_error):
    secret = "PRIVATE_TOKEN_SIGNED_URL_IMAGE_CUSTOMER_DATA"
    original = RuntimeError(secret)
    original.sqlstate = "57014"
    error = OperationalError(secret, {"private": secret}, original) if database_error else original

    def fail_memory(catalog):
        raise error

    monkeypatch.setattr(conversation_engine, "load_memory", fail_memory)
    admission = Mock()
    semantic = Mock()
    reply = conversation_engine.run_turn(None, "[image]", [], "french", admission, Mock(), semantic)

    assert reply.text == words("french")["unavailable"]
    admission.safe_record.assert_called_once_with(fallback=True, failure_category="catalog_memory_unavailable")
    semantic.assert_not_called()
    assert "catalog.memory_failed" in caplog.text
    assert "ai/conversation_engine.py:" in caplog.text
    assert f"failure_type={type(error).__name__}" in caplog.text
    assert f"sqlstate={'57014' if database_error else 'none'}" in caplog.text
    assert secret not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)
