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
    read_only: bool = False
    authelia_url: str = "http://authelia:9091"
    authelia_verify_path: str = "/api/authz/forward-auth"
    forwarded_host: str = "ofin.mvr.ac"
    auth_cache_ttl: int = 30
    auth_portal: str = "https://auth.mvr.ac"


@lru_cache
def settings() -> Settings:
    return Settings()
