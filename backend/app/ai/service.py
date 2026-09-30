import logging
from time import monotonic
from uuid import UUID

from pydantic import ValidationError

from app.ai.client import AITextClient
from app.ai.exceptions import AIError
from app.ai.prompts import FALLBACK_REPLY
from app.ai.schemas import AIHistoryMessage, AIRequest, AIResponse, ProviderResult

logger = logging.getLogger(__name__)


class AIService:
    def __init__(self, client: AITextClient, history_max_messages: int = 12, history_max_chars: int = 12000,
                 settings=None, catalog_enabled=False) -> None:
        self._client = client
        self.history_max_messages = history_max_messages
        self.history_max_chars = history_max_chars
        self.settings = settings
        # Production dependency enables the catalog path; standalone legacy clients
        # remain usable for transport tests without database dependencies.
        self.catalog_enabled = catalog_enabled

    def generate_reply(self, message_text: str, conversation_id: UUID | None = None,
        history: list[AIHistoryMessage] | None = None, telemetry: dict | None = None) -> str:
        stats = telemetry if telemetry is not None else {}
        started = monotonic()
        stats.update(input_tokens=None, output_tokens=None, cached_input_tokens=None)
        try:
            try:
                request = AIRequest(message_text=message_text, conversation_id=conversation_id)
                selected = []
                remaining = self.history_max_chars
                candidates = (history or [])[-self.history_max_messages:] if self.history_max_messages else []
                for message in reversed(candidates):
                    if len(message.content) > remaining:
                        break
                    selected.append(message)
                    remaining -= len(message.content)
                request.messages = list(reversed(selected)) + [
                    AIHistoryMessage(role="user", content=request.message_text)]
            except ValidationError:
                raise AIError("invalid_input") from None
            output = self._client.generate(request)
            if isinstance(output, ProviderResult):
                stats.update(output.usage)
                output = output.text
            try:
                text = AIResponse(text=output).text
                stats.update(status="completed", fallback=False, latency_ms=round((monotonic() - started) * 1000))
                return text
            except ValidationError:
                raise AIError("invalid_response") from None
        except AIError as exc:
            category, status = exc.category, exc.http_status
            stats.update(exc.usage)
        except Exception:
            # Boundary for SDK setup, decoding, and unexpected provider failures.
            # Exception text, bodies, headers, prompts, and output are never logged.
            category, status = "unexpected_error", None
        stats.update(status="failed", failure_category=category, fallback=True,
                     latency_ms=round((monotonic() - started) * 1000))
        logger.warning("ai.reply_failed failure_category=%s http_status=%s fallback_used=true",
            category, status, extra={"failure_category": category, "http_status": status, "fallback_used": True})
        return FALLBACK_REPLY
