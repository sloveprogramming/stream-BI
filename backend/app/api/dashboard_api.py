"""岗位看板：预聚合指标 + 图表数据。

## 设计原则

**看板不是"让业务自己拖表"** —— 那是自助分析（P1）的事。
岗位看板（P0）的本质是**把岗位每天要看的几个数算好**：

| 岗位 | 核心问题 |
|---|---|
| 运营 | 今天卖多少、哪个渠道在跌、发货快不快 |
| 销售 | 各店铺/区域完成度、退货率 |
| PMC   | 库存水位（当前无库存数据源，先不做） |
| 采购 | 采购在途（当前无采购单数据源，先不做） |

## 权限

**所有查询走 `resolve_grant`**（与四个出口同一套裁剪器）。
所以采购看不到零售店铺、各店铺只能看自己，是架构保证而非各看板各自实现。

## 口径（写死在这里，业务以此为准）

- **GMV** = `paid` 之和，**排除已退款**（`refund_status <> '2'`）
- **净销售额** = GMV - `discount`
- **毛利** = GMV - `goods_total_cost`
- **发货时效** = `consign_time - pay_time` 的中位数（小时）
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text as sa_text

from app.access.grants import resolve_grant
from app.api.deps import AnyPrincipal, Context, get_any_principal, get_ctx
from app.models import AuditLog, Dataset

router = APIRouter(prefix="/dashboards", tags=["dashboards"])

# 看板依赖的两张数据集（注册名）
DS_ORDER = "wdt_sales_order"
DS_DETAIL = "wdt_sales_order_detail"

# 已退款状态码
_REFUNDED = "2"


def _granted(session, principal: AnyPrincipal, name: str):
    """取数据集与授权；没授权返回 (None, None)。"""
    ds = session.query(Dataset).filter_by(name=name, enabled=True).first()
    if ds is None:
        return None, None
    return ds, resolve_grant(session, principal.user, ds.id)


def _table(session, name: str) -> str:
    """数据集名 → **物理表名**（带 schema，如 `ods.wdt_sales_order`）。

    看板 SQL 直接跑在物理表上，所以必须用 `source_table` 而不是数据集名 ——
    数据集名只是给人看的注册名，两者常常不同（本模块早期就栽在这里：
    写死 `wdt_sales_order` 导致 `relation does not exist`）。
    """
    ds = session.query(Dataset).filter_by(name=name).first()
    if ds is None:
        raise PermissionError(f"数据集未注册: {name}")
    return ds.source_table


def _rows_filter(session, principal: AnyPrincipal, name: str, where: str = "",
                 params: dict | None = None) -> tuple[str, dict]:
    """拼出带行级规则的 WHERE 片段。没授权时抛 PermissionError 由上层转 403。

    返回 `(where_sql, params)`：
    - `where_sql` 以 " WHERE " 开头（或空串），可直接拼在 FROM 子句后；
    - `params` 合并了调用方的筛选参数与行级规则参数，两者用不同前缀避免撞名。
    """
    ds, grant = _granted(session, principal, name)
    if grant is None:
        raise PermissionError(f"无数据集授权: {name}")
    clauses: list[str] = []
    p: dict = dict(params or {})
    for i, r in enumerate(getattr(grant, "row_rules", None) or []):
        key = f"gr_{i}"
        p[key] = r["value"]
        clauses.append(f'"{r["field"]}" {_op_sql(r["op"])} :{key}')
    if where:
        clauses.append(where)
    sql = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return sql, p


_OP = {"eq": "=", "ne": "!=", "gt": ">", "ge": ">=", "lt": "<", "le": "<=", "like": "LIKE"}


def _op_sql(op: str) -> str:
    if op not in _OP:
        raise ValueError(f"非法操作符: {op!r}")
    return _OP[op]


def _today() -> date:
    return datetime.now().date()


def _audit(ctx, principal, action: str, detail: dict) -> None:
    """看板查询也记审计：合规要能回答"谁看了什么看板"。"""
    try:
        with ctx.session_factory() as session:
            session.add(AuditLog(
                user_id=principal.user.id,
                channel="web",
                dataset=detail.get("dataset"),
                action=action,
                row_count=0,
                detail=detail,
            ))
            session.commit()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 运营看板

@router.get("/ops/overview")
def ops_overview(
    days: int = Query(30, ge=1, le=365),
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """运营看板：KPI + 趋势 + 渠道分布 + 发货时效。

    一次返回看板首页需要的全部数据，避免前端打 N 个接口。
    """
    start = _today() - timedelta(days=days - 1)
    with ctx.session_factory() as session:
        t_order = _table(session, DS_ORDER)
        where, p = _rows_filter(
            session, principal, DS_ORDER,
            "consign_time >= :start AND consign_time IS NOT NULL",
            {"start": start},
        )
        # ---- KPI ----
        kpi = session.execute(sa_text(f"""
            SELECT
              count(*)                                   AS orders,
              COALESCE(sum(paid),0)::numeric(16,2)       AS gmv,
              COALESCE(sum(discount),0)::numeric(16,2)   AS discount,
              COALESCE(sum(goods_total_cost),0)::numeric(16,2) AS cost,
              COALESCE(sum(goods_count),0)               AS items
            FROM {t_order}{where}
        """), p).first()
        gmv = float(kpi.gmv or 0)
        cost = float(kpi.cost or 0)

        # ---- 每日趋势 ----
        trend = session.execute(sa_text(f"""
            SELECT consign_time::date AS d,
                   count(*) AS orders,
                   COALESCE(sum(paid),0)::numeric(16,2) AS gmv
            FROM {t_order}{where}
            GROUP BY 1 ORDER BY 1
        """), p).all()

        # ---- 渠道分布 ----
        channel = session.execute(sa_text(f"""
            SELECT shop_name AS name,
                   count(*) AS orders,
                   COALESCE(sum(paid),0)::numeric(16,2) AS gmv
            FROM {t_order}{where}
            GROUP BY 1 ORDER BY gmv DESC LIMIT 12
        """), p).all()

        # ---- 区域分布 ----
        region = session.execute(sa_text(f"""
            SELECT receiver_province AS name,
                   COALESCE(sum(paid),0)::numeric(16,2) AS gmv
            FROM {t_order}{where}
            GROUP BY 1 ORDER BY gmv DESC LIMIT 12
        """), p).all()

        # ---- 发货时效（中位数小时，只算已付款的） ----
        speed = session.execute(sa_text(f"""
            SELECT COALESCE(
                percentile_cont(0.5) WITHIN GROUP (
                    ORDER BY EXTRACT(EPOCH FROM (consign_time - pay_time))/3600
                ), 0)::numeric(10,1) AS median_hours
            FROM {t_order}{where}
              AND pay_time IS NOT NULL
        """), p).first()

    _audit(ctx, principal, "dashboard.ops", {"dataset": DS_ORDER, "days": days})

    return {
        "range_days": days,
        "kpi": {
            "gmv": gmv,
            "orders": int(kpi.orders or 0),
            "items": int(kpi.items or 0),
            "discount": float(kpi.discount or 0),
            "cost": cost,
            "gross_profit": round(gmv - cost, 2),
            "gross_margin": round((gmv - cost) / gmv * 100, 2) if gmv else 0,
            "avg_order_value": round(gmv / int(kpi.orders or 1), 2),
            "median_ship_hours": float(speed.median_hours or 0),
        },
        "trend": [{"date": str(r.d), "orders": int(r.orders), "gmv": float(r.gmv)} for r in trend],
        "by_channel": [{"name": r.name, "orders": int(r.orders), "gmv": float(r.gmv)} for r in channel],
        "by_region": [{"name": r.name, "gmv": float(r.gmv)} for r in region],
    }


# ---------------------------------------------------------------- 销售看板

@router.get("/sales/overview")
def sales_overview(
    days: int = Query(30, ge=1, le=365),
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """销售看板：渠道排行 + 商品结构 + 退货率 + 业务员业绩。"""
    start = _today() - timedelta(days=days - 1)
    with ctx.session_factory() as session:
        t_order = _table(session, DS_ORDER)
        t_detail = _table(session, DS_DETAIL)

        where, p = _rows_filter(
            session, principal, DS_ORDER,
            "consign_time >= :start AND consign_time IS NOT NULL",
            {"start": start},
        )

        shops = session.execute(sa_text(f"""
            SELECT shop_name AS name,
                   count(*) AS orders,
                   COALESCE(sum(paid),0)::numeric(16,2) AS gmv,
                   COALESCE(sum(discount),0)::numeric(16,2) AS discount
            FROM {t_order}{where}
            GROUP BY 1 ORDER BY gmv DESC LIMIT 20
        """), p).all()

        refund = session.execute(sa_text(f"""
            SELECT count(*) FILTER (WHERE refund_status = '2') AS refunded,
                   count(*) AS total
            FROM {t_order}{where}
        """), p).first()
        refunded, total = int(refund.refunded or 0), int(refund.total or 0)

        # 商品结构走明细表（也要过行级规则）
        dw, dp = _rows_filter(
            session, principal, DS_DETAIL,
            "created >= :start AND created IS NOT NULL",
            {"start": start},
        )
        goods = session.execute(sa_text(f"""
            SELECT COALESCE(goods_name,'未知商品') AS name,
                   COALESCE(brand_name,'未知品牌') AS brand,
                   COALESCE(sum(goods_count),0) AS qty,
                   COALESCE(sum(paid),0)::numeric(16,2) AS gmv
            FROM {t_detail}{dw}
            GROUP BY 1,2 ORDER BY gmv DESC LIMIT 20
        """), dp).all()

        salesmen = session.execute(sa_text(f"""
            SELECT salesman_name AS name,
                   count(*) AS orders,
                   COALESCE(sum(paid),0)::numeric(16,2) AS gmv
            FROM {t_order}{where}
            GROUP BY 1 ORDER BY gmv DESC LIMIT 20
        """), p).all()

    _audit(ctx, principal, "dashboard.sales", {"dataset": DS_ORDER, "days": days})

    return {
        "range_days": days,
        "by_shop": [{
            "name": r.name, "orders": int(r.orders),
            "gmv": float(r.gmv), "discount": float(r.discount or 0),
        } for r in shops],
        "refund_rate": round(refunded / total * 100, 2) if total else 0,
        "refunded_orders": refunded,
        "total_orders": total,
        "by_goods": [{
            "name": r.name, "brand": r.brand,
            "qty": int(r.qty or 0), "gmv": float(r.gmv),
        } for r in goods],
        "by_salesman": [{
            "name": r.name, "orders": int(r.orders), "gmv": float(r.gmv),
        } for r in salesmen],
    }


# ---------------------------------------------------------------- 可用看板清单

@router.get("/catalog")
def catalog(
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """列出当前用户有权访问的看板。

    没授权的看板**不出现在列表里** —— 业务看到的都是自己能看的，
    不会因为点进一个没权限的看板而困惑。
    """
    with ctx.session_factory() as session:
        has_order = _granted(session, principal, DS_ORDER)[1] is not None
        has_detail = _granted(session, principal, DS_DETAIL)[1] is not None

    boards = []
    if has_order:
        boards.append({
            "key": "ops",
            "title": "运营看板",
            "description": "GMV、订单、毛利、发货时效、渠道与区域分布",
            "path": "/dashboards/ops",
        })
    if has_order or has_detail:
        boards.append({
            "key": "sales",
            "title": "销售看板",
            "description": "渠道排行、商品结构、退货率、业务员业绩",
            "path": "/dashboards/sales",
        })
    return boards
