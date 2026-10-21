import os

os.environ["BISQL_EXPORT_DIR"] = "D:/test-export-dir"
from app.settings import Settings, get_settings

get_settings.cache_clear()
s = Settings()
print("export_dir from env:", repr(s.export_dir))
print("export_max_rows:", s.export_max_rows)

from app.reports.service import exports_dir

print("exports_dir(s):", exports_dir(s))
