from typing import Literal


FailureCategory = Literal["not_configured", "timeout", "api_error", "unavailable", "invalid_response", "invalid_input", "unexpected_error"]


class AIError(Exception):
    """Only allowlisted diagnostics; never retain provider bodies or headers."""

    def __init__(self, category: FailureCategory, http_status: int | None = None, usage: dict | None = None) -> None:
        self.category = category
        self.http_status = http_status if type(http_status) is int else None
        self.usage = usage or {}
        super().__init__(category)
