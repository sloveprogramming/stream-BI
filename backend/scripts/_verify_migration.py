from app.db import make_engine
from sqlalchemy import text

e = make_engine("postgresql+psycopg://bisql:bisql@localhost:55432/bisql")
with e.connect() as c:
    for t in ["tenants", "grant_requests", "data_quality_rules", "data_quality_results"]:
        cols = [
            r[0]
            for r in c.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name=:t ORDER BY ordinal_position"
                ),
                {"t": t},
            ).fetchall()
        ]
        print(f"{t}: {cols}")
    print()
    print("tenants rows:", c.execute(text("SELECT id,name,slug,enabled FROM tenants")).fetchall())
    for t in [
        "users",
        "datasets",
        "dataset_grants",
        "data_flows",
        "report_tasks",
        "monitor_tasks",
    ]:
        n = c.execute(text(f"SELECT count(*) FROM {t} WHERE tenant_id IS NULL")).scalar()
        tot = c.execute(text(f"SELECT count(*) FROM {t}")).scalar()
        print(f"{t}: tenant_id NULL={n} / total={tot}")
    print()
    print(
        "agent_runs confidence cols:",
        c.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='agent_runs' AND column_name LIKE 'confid%'"
            )
        ).fetchall(),
    )
    print(
        "grants status/expires cols:",
        c.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='dataset_grants' AND column_name IN "
                "('status','expires_at','created_at')"
            )
        ).fetchall(),
    )
e.dispose()
