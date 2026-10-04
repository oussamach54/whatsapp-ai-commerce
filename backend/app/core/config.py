from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal
from decimal import Decimal
from sqlalchemy.engine import URL


class DeliveryPolicy(BaseModel):
    """Deployment-owned verified facts; never populated by customer/model input."""
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool = False
    eta_min_days: int | None = Field(default=None, ge=0, le=365)
    eta_max_days: int | None = Field(default=None, ge=0, le=365)
    contact_before_arrival: bool | None = None

    @model_validator(mode="after")
    def complete_range(self):
        if (self.eta_min_days is None) != (self.eta_max_days is None):
            raise ValueError("Delivery ETA requires both bounds")
        if self.eta_min_days is not None and self.eta_min_days > self.eta_max_days:
            raise ValueError("Delivery ETA minimum exceeds maximum")
        return self


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_name: str = "WhatsApp AI Commerce API"
    environment: str = "development"
    # One catalog per deployment. No inferred currency or currency conversion.
    catalog_currency: Literal["MAD"] = "MAD"
    checkout_required_fields: list[Literal["customer_name", "phone", "city", "address", "delivery_note", "postal_code"]] = Field(
        default_factory=lambda: ["customer_name", "phone", "city", "address"], min_length=1, max_length=6)
    checkout_country: str = Field(default="MA", pattern=r"^[A-Z]{2}$")
    checkout_shipping_cost: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    delivery_policy: DeliveryPolicy = Field(default_factory=DeliveryPolicy)
    # Fulfillment must explicitly opt in to cancelling processing orders.
    customer_cancellation_statuses: list[Literal["pending", "confirmed", "processing"]] = Field(
        default_factory=lambda: ["pending", "confirmed"], max_length=3)
    customer_cancellation_recent_days: int = Field(default=30, ge=1, le=365)
    customer_cancellation_confirmation_seconds: int = Field(default=900, ge=1, le=900)
    database_host: str
    database_port: int = 5432
    database_name: str
    database_user: str
    database_password: SecretStr = Field(min_length=1)

    # Optional at startup; each WhatsApp operation checks its required settings.
    whatsapp_verify_token: SecretStr | None = None
    whatsapp_access_token: SecretStr | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_api_version: str | None = None
    whatsapp_app_secret: SecretStr | None = None

    # Optional: missing AI configuration uses a safe reply in the background.
    openai_api_key: SecretStr | None = None
    openai_model: str | None = None
    openai_timeout_seconds: float = Field(default=20.0, gt=0, le=120, allow_inf_nan=False)
    openai_max_output_tokens: int = Field(default=512, ge=16, le=4096)
    ai_history_max_messages: int = Field(default=12, ge=0, le=100)
    ai_history_max_chars: int = Field(default=12000, ge=0, le=100000)
    ai_max_input_chars: int = Field(default=2000, ge=1, le=4096)
    ai_classifier_model: str | None = "gpt-4.1-nano"
    ai_classifier_timeout_seconds: float = Field(default=8.0, gt=0, le=30, allow_inf_nan=False)
    ai_classifier_max_output_tokens: int = Field(default=256, ge=64, le=512)
    ai_inbound_per_minute: int = Field(default=10, ge=1, le=1000)
    ai_customer_attempts_per_hour: int = Field(default=60, ge=1)
    ai_customer_attempts_per_day: int = Field(default=200, ge=1)
    ai_business_attempts_per_day: int = Field(default=1000, ge=1)
    ai_spam_threshold: int = Field(default=3, ge=2, le=20)
    ai_spam_window_seconds: int = Field(default=60, ge=1, le=3600)
    ai_notice_cooldown_seconds: int = Field(default=60, ge=1, le=3600)
    whatsapp_max_body_bytes: int = Field(default=1048576, ge=1024, le=10485760)
    whatsapp_image_max_bytes: int = Field(default=5242880, ge=1024, le=10485760)
    whatsapp_media_timeout_seconds: float = Field(default=10.0, gt=0, le=30, allow_inf_nan=False)
    storefront_base_url: str | None = None
    storefront_product_path_template: str = "/products/{slug}"

    @model_validator(mode="after")
    def storefront_configuration(self):
        from app.services.product_links import validate_configuration
        validate_configuration(self.storefront_base_url, self.storefront_product_path_template)
        return self

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def database_url(self) -> URL:
        """Build a PostgreSQL URL without exposing password encoding to callers."""
        return URL.create(
            "postgresql+psycopg",
            username=self.database_user,
            password=self.database_password.get_secret_value(),
            host=self.database_host,
            port=self.database_port,
            database=self.database_name,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
