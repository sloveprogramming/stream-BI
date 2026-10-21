"""指标血缘接口：一个指标被谁用、改了会砸到谁。

登录即可查（与数据血缘同一个取舍）：影响分析必须能看到"别人的"下游，
按权限裁剪会让结果静默漏掉看不见的节点，那比看不到更危险。
泄露面控制在"名字与结构"：无列清单、无数据。
"""
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import AnyPrincipal, Context, get_any_principal, get_ctx
from app.metrics import MetricNotFoundError
from app.metrics.lineage import metric_lineage

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/{name}/lineage")
def get_metric_lineage(
    name: str,
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """指标 → 引用它的流程 → 产物 → 产物的下游（影响分析）。"""
    with ctx.session_factory() as session:
        try:
            return metric_lineage(session, name)
        except MetricNotFoundError:
            raise HTTPException(status_code=404, detail=f"指标不存在: {name}")
