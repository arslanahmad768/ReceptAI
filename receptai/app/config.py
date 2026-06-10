from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # JWT
    secret_key: str
    access_token_expire_minutes: int = 30
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
    demo_phone: str = ""
    demo_url: str = ""
    booking_url: str = ""

    # Optional
    apollo_api_key: str = ""
    allowed_origins: str = "http://localhost:3000"

    @property
    def origins_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",")]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
