import os
from pathlib import Path

# app/reports/service.py -> app -> backend
p = Path("D:/工作仓/BIsql/backend/exports")
print("exports dir:", p)
print("exists:", p.exists())
try:
    p.mkdir(parents=True, exist_ok=True)
    print("mkdir OK")
except Exception as exc:  # noqa: BLE001
    print("mkdir FAIL:", type(exc).__name__, exc)

f = p / "_probe_write.txt"
try:
    f.write_text("x", encoding="utf-8")
    print("write OK")
    os.remove(f)
    print("remove OK")
except Exception as exc:  # noqa: BLE001
    print("write FAIL:", type(exc).__name__, exc)
