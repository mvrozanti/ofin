from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    public_base_url: str = "https://ofin.mvr.ac"
    database_url: str = "postgresql+asyncpg://ofin:ofin@ofin-db:5432/ofin"
    default_user: str = "m"
    timezone: str = "America/Sao_Paulo"
    log_level: str = "info"
    upload_dir: str = "/var/lib/ofin/uploads"


@lru_cache
def settings() -> Settings:
    return Settings()
