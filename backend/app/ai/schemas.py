import unicodedata
from dataclasses import dataclass, field
from uuid import UUID
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

LanguageStyle = Literal["darija_latin", "darija_arabic", "french", "english", "mixed"]


class ScopeDecision(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    scope: Literal["commerce", "social", "unrelated", "unresolved"]
    intent: Literal["discovery", "product", "purchase", "support", "social", "unrelated", "unknown"]
    language_style: LanguageStyle
    security_action: Literal["none", "ignore_injection", "redirect"]


@dataclass
class ProviderResult:
    text: object
    usage: dict = field(default_factory=dict)
    items: list = field(default_factory=list)


@dataclass
class SalesReply:
    text: str | None
    exclude_history: bool = False
    catalog_refs: dict | None = None
    commerce_state: dict | None = None
    preserve_commerce: bool = False


class AIHistoryMessage(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not any(not c.isspace() and not unicodedata.category(c).startswith("C") for c in value):
            raise ValueError("History must contain visible text")
        if any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in value):
            raise ValueError("History contains control characters")
        return value


class AIRequest(BaseModel):
    model_config = ConfigDict(strict=True, str_strip_whitespace=True)

    message_text: str = Field(min_length=1, max_length=4096)
    # Local context only; identifiers are not sent to OpenAI.
    conversation_id: UUID | None = None
    messages: list[AIHistoryMessage] = Field(default_factory=list)


class AIResponse(BaseModel):
    model_config = ConfigDict(strict=True, str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=4096)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not any(not char.isspace() and not unicodedata.category(char).startswith("C") for char in value):
            raise ValueError("Response must contain visible text")
        if any(unicodedata.category(char) == "Cc" and char not in "\n\t" for char in value):
            raise ValueError("Response contains control characters")
        return value
