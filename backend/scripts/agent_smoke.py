"""验证 Agent 真实运行：单轮问数 + 多步 Agent，并打印轨迹。

用现有数据集（都是小表，足以验证链路正确性，不验证业务准确率）。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = "http://127.0.0.1:8000/api/v1"


def sse(path: str, body: dict, token: str) -> list[dict]:
    """发一个 SSE 请求，返回解析后的事件列表。"""
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(), method="POST"
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    events = []
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data: "):
                    continue
                try:
                    events.append(json.loads(line[6:]))
                except json.JSONDecodeError:
                    pass
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode()[:300])
    return events


def login() -> str:
    req = urllib.request.Request(
        BASE + "/auth/login",
        data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())["token"]


def main() -> int:
    tok = login()
    print("登录 OK\n")

    # ---------- 1. 单轮问数 ----------
    print("=" * 66)
    print("1. 单轮问数 /assistant/chat")
    print("=" * 66)
    q1 = "数据集 direct_ 开头的表里，region 字段有哪些不同的取值"
    print("问题:", q1)
    ev = sse("/assistant/chat", {"question": q1}, tok)
    for e in ev:
        t = e.get("type")
        if t == "sql":
            print("  生成的 SQL:", e["sql"][:160])
        elif t == "result":
            print(f"  结果: {len(e.get('rows', []))} 行, 列={e.get('columns')}")
            for row in e.get("rows", [])[:5]:
                print("        ", row)
        elif t == "confidence":
            print("  置信度:", e.get("level"), e.get("reasons"))
        elif t == "error":
            print("  ❌ 错误:", e.get("message"))

    # ---------- 2. Agent 多步 ----------
    print()
    print("=" * 66)
    print("2. Agent 多步 /assistant/ask")
    print("=" * 66)
    q2 = "对比 direct_t 表里华东和华北两个地区的金额合计，说明哪个更高"
    print("问题:", q2)
    t0 = time.perf_counter()
    ev = sse("/assistant/ask", {"goal": q2}, tok)
    elapsed = time.perf_counter() - t0
    plan = None
    answer = None
    for e in ev:
        t = e.get("type")
        if t == "run":
            print("  run_id:", e.get("run_id"))
        elif t == "plan":
            plan = e.get("sub_questions")
            print("  拆解出的子问题:")
            for i, s in enumerate(plan or [], 1):
                print(f"     {i}. {s}")
        elif t == "step":
            print(f"  步骤 [{e.get('node')}] {e.get('status')} {e.get('elapsed_ms')}ms")
        elif t == "answer":
            answer = e
            conf = e.get("confidence") or {}
            print("  置信度:", conf.get("level"), conf.get("reasons"))
            print("  子问题执行情况:")
            for s in e.get("plan") or []:
                mark = {"done": "✓", "failed": "✗"}.get(s.get("status"), "·")
                print(f"     {mark} {str(s.get('question'))[:50]}  rows={s.get('row_count')}")
            print()
            print("  ── 结论 ──")
            for line in (e.get("answer") or "").splitlines():
                print("   ", line)
        elif t == "error":
            print("  ❌ 错误:", e.get("message"))
    print(f"\n  总耗时: {elapsed:.1f}s")

    # ---------- 3. 落库校验 ----------
    print()
    print("=" * 66)
    print("3. 落库校验")
    print("=" * 66)
    from sqlalchemy import text

    from app.db import make_engine

    e2 = make_engine("postgresql+psycopg://bisql:bisql@localhost:55432/bisql")
    with e2.connect() as c:
        n = c.execute(text("SELECT count(*) FROM agent_runs")).scalar()
        print(f"  agent_runs 行数: {n}")
        row = c.execute(text(
            "SELECT run_id,status,confidence,tool_calls,step_count,elapsed_ms "
            "FROM agent_runs ORDER BY id DESC LIMIT 1"
        )).fetchone()
        if row:
            print("  最近一次运行:", dict(row._mapping))
        ns = c.execute(text("SELECT count(*) FROM agent_steps")).scalar()
        print(f"  agent_steps 行数: {ns}")
        steps = c.execute(text(
            "SELECT seq,node,tool,status,elapsed_ms FROM agent_steps "
            "ORDER BY id DESC LIMIT 8"
        )).fetchall()
        for s in reversed(steps):
            print("    ", dict(s._mapping))
    e2.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
