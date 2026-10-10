from sqlalchemy import text as sa_text

from app.access.grants import query_visible_datasets, resolve_grant
from app.access.scoped_query import build_count_sql, build_scoped_sql
from app.models import AuditLog, Dataset


def _grant_for(session, principal, ds: Dataset):
    """与 Web/API 通道同口径：管理员在无显式授权时获得全量权限。"""
    grant = resolve_grant(session, principal.user, ds.id)
    if grant is None:
        raise ValueError(f"无数据集授权: {ds.name}")
    return grant


def _dataset(session, name: str) -> Dataset:
    ds = session.query(Dataset).filter_by(name=name, enabled=True).first()
    if ds is None:
        raise ValueError(f"数据集不存在或未启用: {name}")
    return ds


def mcp_list_datasets(principal) -> list[dict]:
    from app.db import make_engine, make_session_factory
    from app.settings import get_settings

    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_session_factory(engine)
    with sf() as session:
        # 与 Web/API 通道共用同一可见性口径：管理员全量，其余为「直授 ∪ 角色授权」
        rows = query_visible_datasets(session, principal.user).all()
        return [
            {
                "name": d.name,
                "description": d.description,
                "columns": d.columns,
            }
            for d in rows
        ]


def mcp_describe_dataset(principal, name: str) -> dict:
    from app.db import make_engine, make_session_factory
    from app.settings import get_settings

    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_session_factory(engine)
    with sf() as session:
        ds = _dataset(session, name)
        grant = _grant_for(session, principal, ds)
        session.add(
            AuditLog(
                user_id=principal.user.id,
                api_key_id=getattr(principal, "api_key_id", None),
                channel="mcp",
                dataset=ds.name,
                action="describe",
                row_count=0,
                detail={},
            )
        )
        session.commit()
        return {
            "name": ds.name,
            "description": ds.description,
            "columns": grant.field_whitelist or list(ds.columns),
            "row_rules": grant.row_rules,
        }


def mcp_query_dataset(principal, name: str, filters: dict | None, limit: int = 50) -> dict:
    from app.db import make_engine, make_session_factory
    from app.settings import get_settings

    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_session_factory(engine)
    filters = filters or {}
    with sf() as session:
        ds = _dataset(session, name)
        grant = _grant_for(session, principal, ds)
        try:
            sql, params = build_scoped_sql(ds, grant, filters, min(limit, 200), 0)
            count_sql, count_params = build_count_sql(ds, grant, filters)
        except ValueError as exc:
            raise ValueError(str(exc))
        with engine.connect() as conn:
            total = conn.execute(sa_text(count_sql), count_params).scalar()
            rows = [dict(r._mapping) for r in conn.execute(sa_text(sql), params)]
        session.add(
            AuditLog(
                user_id=principal.user.id,
                api_key_id=getattr(principal, "api_key_id", None),
                channel="mcp",
                dataset=ds.name,
                action="query",
                row_count=len(rows),
                detail={"filters": filters, "limit": limit},
            )
        )
        session.commit()
        return {
            "dataset": ds.name,
            "columns": grant.field_whitelist or list(ds.columns),
            "rows": rows,
            "total": total,
        }
