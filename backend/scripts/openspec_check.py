"""校验 OpenSpec change 的结构与场景格式。

不做完整实现（那是 npx openspec validate 的活），只检查本 change 用到的约定：
- change 目录含 proposal.md / tasks.md；
- 每个 spec.md 有至少一个 `### Requirement:`；
- 每个 Requirement 下至少一个 `#### Scenario:`；
- 场景用 GIVEN/WHEN/THEN（大写）；
- 涉及权限的 spec 至少有一个反向场景（越权/拒绝）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHANGE = ROOT / "openspec" / "changes" / "2026-10-12-governance-completeness"

REQ = re.compile(r"^### Requirement:", re.M)
SCEN = re.compile(r"^#### Scenario:", re.M)
# 判定性关键字（大写才算）
KW = re.compile(r"\*\*(GIVEN|WHEN|THEN|AND)\*\*")

NEGATIVE_HINTS = (
    "拒绝", "403", "404", "400", "409", "失败", "不可见", "不生效",
    "不能", "不应", "不含", "无权限", "failed", "anomaly",
)

required = ["proposal.md", "tasks.md"]
errors: list[str] = []

for name in required:
    if not (CHANGE / name).is_file():
        errors.append(f"缺少 {name}")

specs_dir = CHANGE / "specs"
if not specs_dir.is_dir():
    errors.append("缺少 specs/ 目录")
else:
    spec_files = sorted(specs_dir.glob("*/spec.md"))
    if not spec_files:
        errors.append("specs/ 下没有任何 spec.md")
    for spec in spec_files:
        text = spec.read_text(encoding="utf-8")
        rel = spec.relative_to(ROOT).as_posix()
        n_req = len(REQ.findall(text))
        n_scen = len(SCEN.findall(text))
        if n_req == 0:
            errors.append(f"{rel}: 没有 Requirement")
        if n_scen == 0:
            errors.append(f"{rel}: 没有 Scenario")
        # 每个场景都要有 GIVEN/WHEN/THEN
        blocks = re.split(r"^#### Scenario:", text, flags=re.M)[1:]
        for i, block in enumerate(blocks, start=1):
            title = block.splitlines()[0].strip()
            kws = {m.group(1) for m in KW.finditer(block)}
            missing = {"GIVEN", "WHEN", "THEN"} - kws
            if missing:
                errors.append(
                    f"{rel}: 场景 {i} ({title}) 缺少 {'/'.join(sorted(missing))}"
                )
        # 权限相关的能力要有反向场景
        if any(k in rel for k in ("access-control", "multi-tenancy", "grant-lifecycle")):
            if not any(h in text for h in NEGATIVE_HINTS):
                errors.append(f"{rel}: 权限相关但找不到反向场景（越权/拒绝）")
        print(f"  {rel:52s} Requirement={n_req:2d} Scenario={n_scen:2d}")

print()
if errors:
    print(f"FAILED（{len(errors)} 处）:")
    for e in errors:
        print("  -", e)
    sys.exit(1)
print("OK: change 结构完整")
