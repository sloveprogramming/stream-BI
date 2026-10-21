from app.db import make_engine
from sqlalchemy import text

e = make_engine("postgresql+psycopg://bisql:bisql@localhost:55432/bisql")
with e.begin() as c:
    # 上一次不完整迁移留下的对象，全部清掉，让 upgrade 从干净状态重建
    c.execute(text("DROP TABLE IF EXISTS data_quality_results CASCADE"))
    c.execute(text("DROP TABLE IF EXISTS data_quality_rules CASCADE"))
    c.execute(text("DROP TABLE IF EXISTS grant_requests CASCADE"))

    for table in [
        "monitor_tasks",
        "report_tasks",
        "data_flows",
        "dataset_grants",
        "datasets",
        "users",
    ]:
        c.execute(text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS tenant_id CASCADE"))

    c.execute(text("ALTER TABLE dataset_grants DROP COLUMN IF EXISTS status"))
    c.execute(text("ALTER TABLE dataset_grants DROP COLUMN IF EXISTS expires_at"))
    c.execute(text("ALTER TABLE dataset_grants DROP COLUMN IF EXISTS created_at"))
    c.execute(text("ALTER TABLE agent_runs DROP COLUMN IF EXISTS confidence"))
    c.execute(text("ALTER TABLE agent_runs DROP COLUMN IF EXISTS confidence_reasons"))
    c.execute(text("ALTER TABLE users DROP COLUMN IF EXISTS is_superadmin"))
    c.execute(text("DROP TABLE IF EXISTS tenants CASCADE"))

    # 遗留的旧索引名（第一次迁移用的）
    c.execute(text("DROP INDEX IF EXISTS ix_dq_rule_tenant_id"))
    c.execute(text("DROP INDEX IF EXISTS ix_dq_rule_dataset_id"))
    c.execute(text("DROP INDEX IF EXISTS ix_dq_rule_name"))
    c.execute(text("DROP INDEX IF EXISTS ix_dq_result_rule_id"))
    c.execute(text("DROP INDEX IF EXISTS ix_grant_requests_applicant"))
print("cleaned")
e.dispose()
