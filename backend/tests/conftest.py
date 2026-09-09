import os

import pytest
from sqlalchemy import text

from app.db import make_engine, make_session_factory

PG_URL = os.environ.get(
    "BISQL_TEST_PG_URL", "postgresql+psycopg://bisql:bisql@localhost:55432/bisql"
)


def _reachable(url: str) -> bool:
    try:
        make_engine(url).connect().close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.usefixtures()


@pytest.fixture(scope="session")
def pg_engine():
    if not _reachable(PG_URL):
        pytest.skip("PostgreSQL(docker) 不可达：先 docker compose up -d postgres")
    engine = make_engine(PG_URL)
    from app.models import Base

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def pg_session_factory(pg_engine):
    return make_session_factory(pg_engine)
