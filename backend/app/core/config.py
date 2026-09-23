from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    app_name: str = "WhatsApp AI Commerce API"
    environment: str = "development"
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
