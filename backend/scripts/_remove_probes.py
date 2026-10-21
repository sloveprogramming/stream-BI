import os
import sys

# 删掉本次会话为调试创建的探针脚本（都是我建的，项目原本没有）
TARGETS = [
    "_check_pg.py",
    "_clean_partial.py",
    "_clean_tmp.py",
    "_import_probe.py",
    "_probe_erp.py",
    "_probe_fixture.py",
    "_probe_fixture2.py",
    "_probe_pg.py",
    "_reset_alembic.py",
    "_verify_migration.py",
]

BASE = os.path.dirname(os.path.abspath(__file__))
removed, failed = [], []
for name in TARGETS:
    p = os.path.join(BASE, name)
    if not os.path.exists(p):
        continue
    try:
        os.remove(p)
        removed.append(name)
    except Exception as exc:  # noqa: BLE001
        failed.append(f"{name}: {type(exc).__name__}")

print(f"removed={len(removed)} failed={len(failed)}")
for f in failed:
    print("  FAILED:", f)

# backend/_import_probe2.py 也删掉
p2 = os.path.join(os.path.dirname(BASE), "_import_probe2.py")
if os.path.exists(p2):
    try:
        os.remove(p2)
        print("removed _import_probe2.py")
    except Exception as exc:  # noqa: BLE001
        print("FAILED _import_probe2.py:", type(exc).__name__)

print("remaining in scripts/:", sorted(os.listdir(BASE)))
