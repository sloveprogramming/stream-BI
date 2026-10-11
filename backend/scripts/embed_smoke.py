"""端到端冒烟：语义去重链路。

    cd backend
    python scripts/embed_smoke.py            # 跑一次，保留产物
    python scripts/embed_smoke.py --cleanup  # 跑完清理

它做三件事：

1. 起一个**本地假的 embeddings 服务**（返回确定性向量）；
2. 把 `BISQL_EMBEDDING_API_BASE` 指向它 —— 于是跑的是 **API 提供方的真实代码路径**
   （HTTP 调用、分批、解析、维度校验、序列化、缓存），只把"模型"换成了假的；
3. 建一条真实编排流程 `source → dedup.semantic → sink`，用真 dbt 物化，
   验证「写法不同但指向同一商品」的文本被分到同一组、且每组只有一个代表行。

⚠️ 这验证的是**链路**，不是语义准确度：假向量是按文本预先指定的。
真实效果需要配好模型或 API 后自行验证（先跑 `python scripts/check_embedding.py`）。
"""
import argparse
import hashlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlalchemy as sa  # noqa: E402

from app.db import make_engine, make_session_factory  # noqa: E402
from app.flow import service  # noqa: E402
from app.flow.dsl import FlowDSL, FlowEdge, FlowNode, FlowOutput  # noqa: E402
from app.flow.runner import run_flow  # noqa: E402
from app.models import (  # noqa: E402
    DataFlow,
    DataFlowVersion,
    Dataset,
    DatasetGrant,
    FlowRun,
    User,
)
from app.settings import get_settings  # noqa: E402

SOURCE_TABLE = "ods.e2e_products"
DATASET_NAME = "e2e_products"
FLOW_NAME = "e2e_semantic_dedup"
TARGET = "dwd.e2e_semantic_dedup"

DIM = 64
# 预先指定的向量：让「同一商品的不同写法」几乎同向，无关商品正交
_EXACT = {
    "新农哥碧根果": [1.0, 0.02, 0.0],
    "新农哥碧根果（袋装）": [1.0, 0.0, 0.0],
    "东北松子": [0.0, 0.0, 1.0],
}

ROWS = [
    (1, "新农哥碧根果"),
    (2, "新农哥碧根果（袋装）"),
    (3, "东北松子"),
]


def _fake_vector(text: str) -> list[float]:
    base = _EXACT.get(text)
    if base is not None:
        return (list(base) + [0.0] * DIM)[:DIM]
    vector = [0.0] * DIM
    padded = f"^{text}$"
    for i in range(len(padded) - 1):
        gram = padded[i : i + 2]
        bucket = int(hashlib.md5(gram.encode("utf-8")).hexdigest()[:8], 16) % DIM
        vector[bucket] += 1.0
    return vector


class _EmbeddingsHandler(BaseHTTPRequestHandler):
    """最小可用的 OpenAI 兼容 /embeddings 端点。"""

    def do_POST(self):  # noqa: N802 —— BaseHTTPRequestHandler 的约定命名
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        inputs = payload.get("input") or []
        if isinstance(inputs, str):
            inputs = [inputs]
        body = json.dumps(
            {
                "object": "list",
                "model": payload.get("model", "fake"),
                "data": [
                    {"object": "embedding", "index": i, "embedding": _fake_vector(text)}
                    for i, text in enumerate(inputs)
                ],
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 别把每条请求打到 stderr
        pass


def start_fake_service() -> tuple[HTTPServer, str]:
    server = HTTPServer(("127.0.0.1", 0), _EmbeddingsHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"


def build_dsl(dataset_id: int) -> FlowDSL:
    return FlowDSL(
        name=FLOW_NAME,
        title="语义去重冒烟",
        description="按语义相似度标记商品分组",
        nodes=[
            FlowNode(id="src", type="source.dataset", params={"dataset_id": dataset_id}),
            FlowNode(
                id="dedup",
                type="dedup.semantic",
                params={"input_column": "product_name"},
            ),
            FlowNode(id="out", type="sink.materialize", params={}),
        ],
        edges=[
            FlowEdge(source="src", target="dedup"),
            FlowEdge(source="dedup", target="out"),
        ],
        output=FlowOutput(
            target_name=FLOW_NAME, target_schema="dwd", pk_columns=["id"]
        ),
    )


def prepare(engine, session_factory) -> tuple[int, int]:
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ods"))
        conn.execute(sa.text(f"DROP TABLE IF EXISTS {SOURCE_TABLE}"))
        conn.execute(
            sa.text(f"CREATE TABLE {SOURCE_TABLE} (id int primary key, product_name text)")
        )
        conn.execute(
            sa.text(f"INSERT INTO {SOURCE_TABLE} VALUES (:a, :b)"),
            [{"a": r[0], "b": r[1]} for r in ROWS],
        )

    with session_factory() as session:
        admin = session.query(User).filter_by(username="admin").first()
        if admin is None:
            raise SystemExit("本地库里没有 admin 用户，先跑 python -m app.bootstrap admin <密码>")

        dataset = session.query(Dataset).filter_by(name=DATASET_NAME).first()
        if dataset is None:
            dataset = Dataset(name=DATASET_NAME, enabled=True)
            session.add(dataset)
        dataset.source_table = SOURCE_TABLE
        dataset.pk_columns = ["id"]
        dataset.columns = ["id", "product_name"]
        session.flush()

        session.query(DatasetGrant).filter_by(dataset_id=dataset.id).delete()
        session.add(
            DatasetGrant(
                user_id=admin.id,
                dataset_id=dataset.id,
                row_rules=[],
                field_whitelist=None,
            )
        )
        session.commit()
        return dataset.id, admin.id


def cleanup(engine, session_factory) -> None:
    with session_factory() as session:
        flow = session.query(DataFlow).filter_by(name=FLOW_NAME).first()
        if flow is not None:
            session.query(FlowRun).filter(FlowRun.flow_id == flow.id).delete()
            session.query(DataFlowVersion).filter(
                DataFlowVersion.flow_id == flow.id
            ).delete()
            session.delete(flow)
        dataset = session.query(Dataset).filter_by(name=DATASET_NAME).first()
        if dataset is not None:
            session.query(DatasetGrant).filter_by(dataset_id=dataset.id).delete()
            session.delete(dataset)
        session.commit()
    with engine.begin() as conn:
        conn.execute(sa.text(f"DROP TABLE IF EXISTS {TARGET}"))
        conn.execute(sa.text(f"DROP TABLE IF EXISTS {SOURCE_TABLE}"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup", action="store_true", help="跑完清理源表与产物")
    args = parser.parse_args()

    settings = get_settings()
    server, base_url = start_fake_service()
    # 指向本地假服务：于是走的是 API 提供方的真实代码路径
    settings.embedding_provider = "api"
    settings.embedding_api_base = base_url
    settings.embedding_model = "fake-bge-m3"
    print(f"假 embeddings 服务: {base_url}（仅验证链路，非真实语义）")

    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    failures: list[str] = []
    try:
        dataset_id, admin_id = prepare(engine, session_factory)

        with session_factory() as session:
            admin = session.get(User, admin_id)
            stale = session.query(DataFlow).filter_by(name=FLOW_NAME).first()
            if stale is not None:
                session.query(FlowRun).filter(FlowRun.flow_id == stale.id).delete()
                session.query(DataFlowVersion).filter(
                    DataFlowVersion.flow_id == stale.id
                ).delete()
                session.delete(stale)
                session.commit()

            flow = service.create_flow(session, dsl=build_dsl(dataset_id), owner=admin)
            run = run_flow(
                session,
                flow,
                pg_engine=engine,
                settings=settings,
                triggered_by="manual",
                trigger_user_id=admin_id,
            )

        print(f"status  : {run.status}")
        print(f"rows    : {run.rows_out}")
        print(f"error   : {run.error}")
        if run.status != "ok":
            failures.append(f"流程失败：{run.error}")
        else:
            schema, table = TARGET.split(".")
            with engine.connect() as conn:
                rows = conn.execute(
                    sa.text(
                        f'SELECT id, product_name, semantic_group, is_representative '
                        f'FROM "{schema}"."{table}" ORDER BY id'
                    )
                ).fetchall()
            for row in rows:
                print(f"  id={row[0]}  group={row[2]}  代表={row[3]}  {row[1]}")

            group_of = {row[0]: row[2] for row in rows}
            if group_of.get(1) != group_of.get(2):
                failures.append("「碧根果」与「碧根果（袋装）」没被分到同一组")
            if group_of.get(1) == group_of.get(3):
                failures.append("无关商品「东北松子」被误合并了")
            reps = [row for row in rows if row[3]]
            if len(reps) != 2:
                failures.append(f"代表行数量应为 2（两组各一个），实际 {len(reps)}")

        if args.cleanup:
            cleanup(engine, session_factory)
            print("cleaned up")
    finally:
        server.shutdown()
        engine.dispose()

    if failures:
        for item in failures:
            print(f"!! {item}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
