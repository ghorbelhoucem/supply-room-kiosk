from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg2://supply:supply@db:5432/supply"
    jwt_secret: str = "change-me-in-production-use-long-random-string"
    jwt_expire_minutes: int = 480
    cors_origins: str = "*"
    seed_on_startup: bool = True
    google_sheet_sync_enabled: bool = False
    google_sheet_id: str = ""
    google_service_account_json: str = ""
    legacy_webapp_url: str = ""
    sheet_sync_interval_minutes: int = 10
    slack_transactions_webhook_url: str = ""
    slack_purchase_webhook_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
