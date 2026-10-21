from app.settings import get_settings
from app.sync.puller import make_erp_engine

s = get_settings()
print("erp_url:", s.erp_database_url.replace("123456", "***"))
e = make_erp_engine(s.erp_database_url, s.erp_query_timeout_seconds)
with e.connect() as c:
    tables = [
        r[0]
        for r in c.exec_driver_sql(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='erp' AND table_type='BASE TABLE' ORDER BY table_name"
        ).fetchall()
    ]
print("ERP tables:", len(tables))
print(tables)
e.dispose()
