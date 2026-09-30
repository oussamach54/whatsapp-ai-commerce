from typing import Protocol

from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, OpenAI

from app.ai.exceptions import AIError
from app.ai.prompts import SYSTEM_PROMPT
from app.ai.schemas import AIRequest, ProviderResult
from app.core.config import Settings


class AITextClient(Protocol):
    def generate(self, request: AIRequest) -> object: ...


class OpenAITextClient:
    def __init__(self, settings: Settings) -> None:
        # SDK initialization is deliberately deferred to the background task.
        self._settings = settings

    def generate(self, request: AIRequest) -> object:
        return self.respond(request.messages, SYSTEM_PROMPT, self._settings.openai_model,
                            self._settings.openai_timeout_seconds, self._settings.openai_max_output_tokens)

    def respond(self, messages, instructions, model, timeout, max_tokens, **kwargs):
        settings = self._settings
        key = settings.openai_api_key
        model = (model or "").strip()
        if key is None or not key.get_secret_value().strip() or not model:
            raise AIError("not_configured")
        try:
            # Per-call lifetime keeps shutdown deterministic, with no connection
            # resources tied to the webhook request or database transaction.
            with OpenAI(api_key=key.get_secret_value(), base_url="https://api.openai.com/v1",
                timeout=timeout, max_retries=0) as client:
                response = client.responses.create(
                    model=model, instructions=instructions,
                    input=[message.model_dump() if hasattr(message, "model_dump") else message for message in messages],
                    max_output_tokens=max_tokens, store=False, **kwargs,
                )
                usage = response_usage(response)
                if getattr(response, "status", None) != "completed":
                    raise AIError("invalid_response", usage=usage)
                return ProviderResult(getattr(response, "output_text", None), usage,
                    [item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else item
                     for item in (getattr(response, "output", None) or [])])
        except APITimeoutError:
            raise AIError("timeout") from None
        except APIStatusError as exc:
            raise AIError("api_error", exc.status_code) from None
        except APIConnectionError:
            raise AIError("unavailable") from None
        except APIError:
            raise AIError("api_error") from None


def response_usage(response):
    usage = getattr(response, "usage", None)
    details = getattr(usage, "input_tokens_details", None)
    def number(value):
        return value if type(value) is int and value >= 0 else None
    return {"input_tokens": number(getattr(usage, "input_tokens", None)),
            "output_tokens": number(getattr(usage, "output_tokens", None)),
            "cached_input_tokens": number(getattr(details, "cached_tokens", None))}
