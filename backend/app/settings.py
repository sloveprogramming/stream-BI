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
    # 模型路由：规划/反思用推理模型（更慢但更稳），SQL 生成用 llm_model（快、便宜）
    llm_planner_model: str = "deepseek-reasoner"
    llm_planner_timeout_seconds: int = 180
    assistant_max_rows: int = 1000
    # Agent 循环（Plan-and-Execute）。默认关闭，不影响现有 /assistant/chat 单轮问数。
    agent_enabled: bool = False
    agent_max_sub_questions: int = 5
    agent_max_replans: int = 1
    agent_max_tool_calls: int = 12
    agent_evidence_rows: int = 20  # 喂回模型的证据行数上限（控制上下文与成本）
    agent_max_sql_attempts: int = 2  # 单步「生成→执行→体检→改写」的循环上限
    agent_clarify_enabled: bool = True  # 口径不明时先反问，不瞎猜
    # 主动监控：定时跑指标 SQL，命中统计基线异常才启动 Agent 归因
    monitor_enabled: bool = True
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
    # 数据编排（flow）：画布产出的流程编译成 dbt model 后物化
    flow_enabled: bool = True
    flow_dbt_project_dir: str = "dbt"  # 相对 backend/ 的 dbt 项目目录
    flow_dbt_target: str = "prod"  # profiles.yml 里的 target
    flow_generated_subdir: str = "models/generated"  # 画布生成的 model 落这里
    flow_max_nodes: int = 50  # 单条流程的节点数上限（防画布失控）
    flow_max_share_users: int = 20  # 一条流程产物最多额外授权给多少人
    # AI 算子：中间结果落这个 schema；单次输入行数上限是成本安全阀
    flow_ai_schema: str = "ai_tmp"
    flow_ai_max_rows: int = 5000
    flow_ai_timeout_seconds: int = 120


@lru_cache
def get_settings() -> Settings:
    return Settings()


