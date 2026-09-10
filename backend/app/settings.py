from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="BISQL_", extra="ignore")

    database_url: str = "postgresql+psycopg://bisql:bisql@localhost:55432/bisql"
    erp_database_url: str = "mysql+pymysql://root:root@localhost:53306/erp?charset=utf8mb4"
    erp_query_timeout_seconds: int = 30
    sync_batch_size: int = 5000
    sync_retry_attempts: int = 3
    sync_retry_base_delay: float = 5.0
    alert_webhook_url: str = ""
    rate_limit_per_minute: int = 120
    rate_limit_per_day: int = 10000
    jwt_secret: str = "dev-secret-change-me"
    jwt_expire_hours: int = 12
    export_max_rows: int = 50000
    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-chat"
    llm_timeout_seconds: int = 60
    assistant_max_rows: int = 1000


@lru_cache
def get_settings() -> Settings:
    return Settings()


