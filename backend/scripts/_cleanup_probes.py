"""删除本次会话为调试创建的临时探针脚本。

保留（有长期价值，已作为项目脚本）：
  scripts/governance_smoke.py  —— 治理能力端到端冒烟
  scripts/openspec_check.py    —— OpenSpec change 结构校验
"""
import os

BASE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(BASE)

TARGETS = [
    os.path.join(BASE, "_check_pg.py"),
    os.path.join(BASE, "_clean_partial.py"),
    os.path.join(BASE, "_clean_providers.py"),
    os.path.join(BASE, "_clean_tmp.py"),
    os.path.join(BASE, "_import_probe.py"),
    os.path.join(BASE, "_probe_dirs.py"),
    os.path.join(BASE, "_probe_erp.py"),
    os.path.join(BASE, "_probe_exports.py"),
    os.path.join(BASE, "_probe_fixture.py"),
    os.path.join(BASE, "_probe_fixture2.py"),
    os.path.join(BASE, "_probe_one.py"),
    os.path.join(BASE, "_probe_pg.py"),
    os.path.join(BASE, "_probe_prov.py"),
    os.path.join(BASE, "_probe_runs.py"),
    os.path.join(BASE, "_probe_settings.py"),
    os.path.join(BASE, "_probe_wd.py"),
    os.path.join(BASE, "_remove_probes.py"),
    os.path.join(BASE, "_reset_alembic.py"),
    os.path.join(BASE, "_routes.py"),
    os.path.join(BASE, "_routes2.py"),
    os.path.join(BASE, "_routes3.py"),
    os.path.join(BASE, "_verify_migration.py"),
    os.path.join(BACKEND, "_import_probe2.py"),
]

removed, failed = [], []
for path in TARGETS:
    if not os.path.exists(path):
        continue
    try:
        os.remove(path)
        removed.append(os.path.basename(path))
    except Exception as exc:  # noqa: BLE001
        failed.append(f"{os.path.basename(path)}: {type(exc).__name__}")

print(f"removed={len(removed)} failed={len(failed)}")
for f in failed:
    print("  FAILED:", f)

left = sorted(x for x in os.listdir(BASE) if x.startswith("_"))
print("剩余下划线开头的文件:", left)
