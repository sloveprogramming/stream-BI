"""测试用的可写 dbt 项目目录。

`backend/dbt/models/generated/` 在本机沙箱里是**只读**的（写文件被拒），
而流程执行链路必须把编译产物落盘。解决办法：

    在 `.testwork/dbt/` 造一份可写的"影子项目" —— 复制真实项目的配置
    （dbt_project.yml / profiles.yml / macros / models/marts），
    models/generated 用一份新的空目录。

这样 `resolve_project_dir()` 拿到的是绝对路径，落盘落在可写位置，
而 dbt 的配置语义与真实项目完全一致（配置是复制来的，不是另写的）。
"""
from __future__ import annotations

import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_DBT = REPO_ROOT / "backend" / "dbt"
SHADOW_ROOT = REPO_ROOT / ".testwork" / "dbt"

# 需要从真实项目复制过来的配置项（不含产物目录）
_COPY_FILES = ("dbt_project.yml", "profiles.yml")
_COPY_DIRS = ("macros", "models/marts", "models/staging")


def shadow_dbt_project() -> str:
    """返回可写的 dbt 项目目录（绝对路径）。已存在则复用。"""
    SHADOW_ROOT.mkdir(parents=True, exist_ok=True)

    for name in _COPY_FILES:
        src = REAL_DBT / name
        if src.exists():
            shutil.copy2(src, SHADOW_ROOT / name)

    for name in _COPY_DIRS:
        src = REAL_DBT / name
        dst = SHADOW_ROOT / name
        if src.is_dir() and not dst.exists():
            shutil.copytree(src, dst)

    # 产物目录必须是可写的空目录（每次调用确保存在）
    (SHADOW_ROOT / "models" / "generated").mkdir(parents=True, exist_ok=True)
    return str(SHADOW_ROOT)
