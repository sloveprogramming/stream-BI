from app.db import make_engine
from sqlalchemy import text

e = make_engine("postgresql+psycopg://bisql:bisql@localhost:55432/bisql")
with e.connect() as c:
    for t in ["datasets", "dataset_grants", "sync_tasks", "users", "data_flows"]:
        n = c.execute(text(f"SELECT count(*) FROM {t}")).scalar()
        print(f"{t}: {n}")
    schemas = [
        r[0]
        for r in c.execute(
            text(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name NOT LIKE 'pg_%' AND schema_name <> 'information_schema'"
            )
        ).fetchall()
    ]
    print("schemas:", schemas)
    ods = [
        r[0]
        for r in c.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='ods' ORDER BY table_name"
            )
        ).fetchall()
    ]
    print("ods tables:", len(ods), ods[:10])
e.dispose()
