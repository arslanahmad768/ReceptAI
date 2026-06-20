from functools import lru_cache
from typing import Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # JWT
    secret_key: str
    access_token_expire_minutes: int = 120
    refresh_token_expire_days: int = 7

    # Supabase
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str

    # LLM
    groq_api_key: str

    # Email delivery
    resend_api_key: str
    from_email: str
    from_name: str = "ReceptAI Outreach"

    # Voice agent demo links (injected into cold emails)
    demo_phone: str | None = None
    demo_url: str | None = None
    booking_url: str | None = None

    # Optional
    hunter_api_key: str | None = None
    snov_client_id: str | None = None
    snov_client_secret: str | None = None
    serpapi_key: str | None = None
    enable_us_medical_lookup: bool = True  # set False to disable US-specific NPI lookups
    allowed_origins: str = "http://localhost:3000"

    @field_validator("secret_key")
    @classmethod
    def secret_key_min_length(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SECRET_KEY must be at least 32 characters")
        return v

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
