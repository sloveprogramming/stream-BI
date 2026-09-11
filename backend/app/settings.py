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
    # 自动发现：定时扫描 ods 模式新表并注册为数据集
    discovery_enabled: bool = True
    discovery_interval_seconds: int = 10
    discovery_schema: str = "ods"
    # ERP 自动同步：定时扫描 ERP 库的新表，自动创建同步任务（拉进 ods 后再由上面那步上架）
    erp_autosync_enabled: bool = True
    erp_autosync_interval_seconds: int = 300
    erp_autosync_cron: str = "*/30 * * * *"
    erp_autosync_include: str = ""  # 逗号分隔 glob，空 = 全部表
    erp_autosync_exclude: str = ""  # 逗号分隔 glob，优先于 include
    erp_autosync_max_tables: int = 100  # 安全阀：单次最多自动创建多少个任务
    erp_autosync_run_now: bool = True  # 新建任务后立即跑一次，不等 cron


@lru_cache
def get_settings() -> Settings:
    return Settings()


