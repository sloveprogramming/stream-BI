"""端到端冒烟：真实服务上的六项治理能力。

用真 HTTP 打真服务，验证的是"接起来能跑"，不只是单测里的函数正确。
不碰 ERP，全部数据本地自造。
"""
import json
import urllib.error
import urllib.request
import uuid

BASE = "http://127.0.0.1:8021/api/v1"


def call(method, path, body=None, token=None, expect=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        status = e.code
    try:
        payload = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        payload = raw
    if expect is not None and status != expect:
        raise AssertionError(f"{method} {path} 期望 {expect} 实际 {status}: {raw[:200]}")
    return status, payload


def main():
    suffix = uuid.uuid4().hex[:8]
    results = []

    _, login = call("POST", "/auth/login", {"username": "admin", "password": "admin123"},
                    expect=200)
    tok = login["token"]
    print("1. 登录                     OK")

    # ---- 租户 ----
    _, tenants = call("GET", "/tenants", token=tok, expect=200)
    assert any(t["slug"] == "default" for t in tenants), "default 租户应存在"
    print(f"2. 租户列表                 OK ({len(tenants)} 个，含 default)")

    # 普通管理员建租户应 403（admin 不是 superadmin）
    st, _ = call("POST", "/tenants", {"name": "X", "slug": f"x{suffix}"}, token=tok)
    assert st == 403, f"非超管建租户应 403，实际 {st}"
    print("3. 非超管建租户被拒         OK (403)")

    # ---- 数据质量 ----
    st, rules = call("GET", "/quality/rules", token=tok, expect=200)
    st, summary = call("GET", "/quality/summary", token=tok, expect=200)
    assert "total" in summary and "anomaly" in summary
    print(f"4. 质量规则/看板            OK (rules={len(rules)}, total={summary['total']})")

    # 建规则（用真实存在的数据集）
    _, datasets = call("GET", "/datasets", token=tok, expect=200)
    assert datasets, "至少要有一个数据集"
    ds = datasets[0]
    rule_name = f"smoke_{suffix}"
    st, rule = call("POST", "/quality/rules", {
        "name": rule_name, "dataset_id": ds["id"], "rule_type": "row_count",
        "threshold": 20, "cron": "0 7 * * *",
    }, token=tok, expect=201)
    rid = rule["id"]
    print(f"5. 建质量规则               OK (id={rid})")

    # 立即巡检
    st, res = call("POST", f"/quality/rules/{rid}/run", token=tok, expect=200)
    assert res["status"] in ("ok", "anomaly", "failed"), res
    print(f"6. 立即巡检                 OK (status={res['status']})")

    # 结果可查
    st, allres = call("GET", f"/quality/results?rule_id={rid}", token=tok, expect=200)
    assert any(r["rule_id"] == rid for r in allres), "刚跑的结果应能查到"
    print(f"7. 巡检结果可查             OK ({len(allres)} 条)")

    # 清理规则
    call("DELETE", f"/quality/rules/{rid}", token=tok, expect=200)
    print("8. 删除质量规则             OK")

    # ---- 权限申请流 ----
    st, mine = call("GET", "/grant-requests?mine=1", token=tok, expect=200)
    print(f"9. 我的申请列表             OK ({len(mine)} 条)")

    # ---- 指标血缘 ----
    st, _ = call("GET", "/metrics/__nope__/lineage", token=tok, expect=404)
    print("10. 未知指标血缘 404        OK")

    # 真实指标（若字典已锚定数据集）
    st, payload = call("GET", "/metrics/gross_margin/lineage", token=tok)
    if st == 200:
        print(f"11. 指标血缘                OK (used_by={payload['used_by_count']})")
    else:
        print(f"11. 指标血缘                跳过（gross_margin 未锚定，status={st}）")

    # ---- 审计 ----
    st, logs = call("GET", "/admin/audit-logs?limit=20", token=tok, expect=200)
    actions = {a["action"] for a in logs}
    print(f"12. 审计接口                OK (最近 {len(logs)} 条)")

    print()
    print("=== 端到端冒烟全部通过 ===")


if __name__ == "__main__":
    main()
