from dataclasses import dataclass

from fastapi import Request
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker


@dataclass
class Context:
    session_factory: sessionmaker
    erp_engine: Engine
    pg_engine: Engine
    settings: object


def get_ctx(request: Request) -> Context:
    return Context(
        session_factory=request.app.state.session_factory,
        erp_engine=request.app.state.erp_engine,
        pg_engine=request.app.state.pg_engine,
        settings=request.app.state.settings,
    )
