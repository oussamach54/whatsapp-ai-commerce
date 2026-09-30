from typing import Annotated

from fastapi import Depends

from app.ai.client import OpenAITextClient
from app.ai.service import AIService
from app.core.config import Settings, get_settings


def get_ai_service(settings: Annotated[Settings, Depends(get_settings)]) -> AIService:
    return AIService(OpenAITextClient(settings), settings.ai_history_max_messages, settings.ai_history_max_chars, settings,
                     catalog_enabled=True)
