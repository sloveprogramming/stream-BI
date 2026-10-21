"""Agent 业务问数验证：有标准答案可对照。

四个问题，从单表聚合到多表关联，检验：
1. 单轮问数答得对不对
2. Agent 多步拆解质量
3. 多表关联（库存 + 在途）能不能算
4. 置信度是否与正确性相符
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

QUESTIONS = [
    ("单表聚合", "chat", "demo_sales 里各大区的销售金额合计分别是多少，从高到低排序"),
    ("分组+过滤", "chat", "德清仓在坚果炒货类目上的销售额是多少"),
    ("多步分析", "ask", "哪个大区的销售表现最好？请先算出各大区销售额，再说明我的判断依据"),
    ("多表关联", "ask", "哪些商品的库存偏低、同时采购在途还没到货？请说明风险"),
]


def sse(path: str, body: dict, token: str, timeout: int = 300) -> list[dict]:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    events = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if line.startswith("data: "):
                    try:
                        events.append(json.loads(line[6:]))
                    except json.JSONDecodeError:
                        pass
    except urllib.error.HTTPError as e:
        print("   HTTP", e.code, e.read().decode()[:200])
    except Exception as e:  # noqa: BLE001
        print("   ERR", type(e).__name__, str(e)[:150])
    return events


def login() -> str:
    req = urllib.request.Request(
        BASE + "/auth/login",
        data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
        method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())["token"]


def run_chat(q: str, tok: str) -> None:
    ev = sse("/assistant/chat", {"question": q}, tok)
    for e in ev:
        t = e.get("type")
        if t == "sql":
            print("   SQL:", " ".join(e["sql"].split())[:200])
        elif t == "result":
            rows = e.get("rows", [])
            print(f"   结果 {len(rows)} 行 / 列 {e.get('columns')}")
            for r in rows[:8]:
                print("      ", r)
        elif t == "confidence":
            print("   置信度:", e.get("level"), e.get("reasons"))
        elif t == "error":
            print("   ❌", e.get("message"))


def run_agent(q: str, tok: str) -> None:
    t0 = time.perf_counter()
    ev = sse("/assistant/ask", {"goal": q}, tok)
    plan_shown = 0
    for e in ev:
        t = e.get("type")
        if t == "plan":
            plan_shown += 1
            subs = e.get("sub_questions") or []
            print(f"   第 {plan_shown} 次规划: {len(subs)} 个子问题")
            for s in subs:
                print(f"      - {s}")
        elif t == "step" and e.get("node") == "act":
            print(f"   act: {e.get('status')} {e.get('elapsed_ms')}ms")
        elif t == "answer":
            conf = e.get("confidence") or {}
            print("   子问题结果:")
            for s in e.get("plan") or []:
                mark = {"done": "✓", "failed": "✗"}.get(s.get("status"), "·")
                print(f"      {mark} {str(s.get('question'))[:56]} rows={s.get('row_count')}")
            print("   置信度:", conf.get("level"), conf.get("reasons"))
            print("   ── 结论 ──")
            for line in (e.get("answer") or "").splitlines():
                print("     ", line)
        elif t == "error":
            print("   ❌", e.get("message"))
    print(f"   耗时 {time.perf_counter() - t0:.1f}s")


def main() -> int:
    tok = login()
    for i, (tag, mode, q) in enumerate(QUESTIONS, 1):
        print("=" * 70)
        print(f"[{i}/{len(QUESTIONS)}] {tag} —— {mode}")
        print("问:", q)
        print("-" * 70)
        try:
            if mode == "chat":
                run_chat(q, tok)
            else:
                run_agent(q, tok)
        except Exception as e:  # noqa: BLE001
            print("   FAIL:", type(e).__name__, str(e)[:200])
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
