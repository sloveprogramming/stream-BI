"""血缘查询接口：登录即可查，响应只有名字与结构。

为什么不做授权裁剪（design 决策 2）：血缘的价值恰恰是跨人协作 ——
影响分析要看到**看不见的人**的下游，按权限裁剪会让结果静默漏掉看不见的节点，
那比看不到更危险。泄露面控制在"名字与层级"：无列清单、无行数、无数据。
"""
from fastapi import APIRouter, Depends, Query

from app.api.deps import AnyPrincipal, Context, get_any_principal, get_ctx
from app.flow import lineage

router = APIRouter(prefix="/lineage", tags=["lineage"])


@router.get("/datasets/{name}/upstream")
def upstream(
    name: str,
    depth: int = Query(default=3, ge=1, le=lineage.MAX_DEPTH),
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """这个数据集由什么加工而来（来源追溯）。"""
    with ctx.session_factory() as session:
        return lineage.upstream(session, name, depth=depth)


@router.get("/datasets/{name}/downstream")
def downstream(
    name: str,
    depth: int = Query(default=3, ge=1, le=lineage.MAX_DEPTH),
    principal: AnyPrincipal = Depends(get_any_principal),
    ctx: Context = Depends(get_ctx),
):
    """这个数据集被谁消费（影响分析）。"""
    with ctx.session_factory() as session:
        return lineage.downstream(session, name, depth=depth)
