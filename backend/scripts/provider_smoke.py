"""端到端冒烟：平台内配置的模型提供方**真实生效**。

    cd backend
    python scripts/provider_smoke.py --cleanup

流程：

1. 起一个本地假 OpenAI 兼容服务（/chat/completions、/embeddings、/models），
   记下它收到的每一次请求；
2. 走**真实的 HTTP 管理接口**把它登记为 LLM 与 embedding 的默认提供方
   （key 加密落库）；
3. 发一次真实 chat、一次真实向量化 —— 断言请求打到了假服务
   （即 DB 配置生效，而不是回退环境变量）；
4. 删除配置 → 再解析 → 断言回退环境变量。
"""
import argparse
import json
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlalchemy as sa  # noqa: E402
from fastapi import Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import Context, get_ctx  # noqa: E402
from app.bootstrap import bootstrap_admin  # noqa: E402
from app.db import make_engine, make_session_factory  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import ApiKey, AuditLog, ModelProvider, User  # noqa: E402
from app.settings import get_settings  # noqa: E402

LLM_NAME = "smoke_llm_provider"
EMB_NAME = "smoke_embedding_provider"

HITS: list[str] = []
CHAT_REPLY = '{"ok": true, "via": "provider-smoke"}'
EMB_VECTOR = [0.25, 0.5, 0.75]


class _Handler(BaseHTTPRequestHandler):
    def _send(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        HITS.append("GET " + self.path)
        if self.path.endswith("/models"):
            self._send({"object": "list", "data": [{"id": "fake-chat", "object": "model"}]})
        else:
            self._send({})

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        HITS.append("POST " + self.path)
        if self.path.endswith("/chat/completions"):
            self._send(
                {
                    "id": "chatcmpl-smoke",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "fake-chat",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": CHAT_REPLY},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            )
        elif self.path.endswith("/embeddings"):
            inputs = payload.get("input") or []
            if isinstance(inputs, str):
                inputs = [inputs]
            self._send(
                {
                    "object": "list",
                    "model": "fake-embed",
                    "data": [
                        {"object": "embedding", "index": i, "embedding": EMB_VECTOR}
                        for i in range(len(inputs))
                    ],
                }
            )
        else:
            self._send({})

    def log_message(self, *args):
        pass


def start_fake_service() -> tuple[HTTPServer, str]:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/v1"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup", action="store_true", help="跑完清理登记的配置与临时账号")
    args = parser.parse_args()

    settings = get_settings()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)

    server, base_url = start_fake_service()
    print(f"假 OpenAI 兼容服务: {base_url}")

    app = create_app()

    def override_ctx(request: Request):
        return Context(
            session_factory=session_factory,
            erp_engine=object(),
            pg_engine=engine,
            settings=SimpleNamespaceSettings(),
        )

    app.dependency_overrides[get_ctx] = override_ctx
    client = TestClient(app)

    admin_name = f"provsmoke_{uuid.uuid4().hex[:8]}"
    _, admin_key = bootstrap_admin(admin_name, "pw123456")
    headers = {"X-API-Key": admin_key}

    failures: list[str] = []
    try:
        # ① 登记两个默认提供方（走真实 HTTP 接口，key 加密落库）
        for name, kind in ((LLM_NAME, "llm"), (EMB_NAME, "embedding")):
            r = client.post(
                "/api/v1/admin/providers",
                json={
                    "name": name,
                    "kind": kind,
                    "provider_kind": "api",
                    "base_url": base_url,
                    "api_key": "sk-smoke-key",
                    "model": "fake-chat" if kind == "llm" else "fake-embed",
                },
                headers=headers,
            )
            if r.status_code != 201:
                failures.append(f"登记 {name} 失败：{r.status_code} {r.text[:200]}")
        print(f"登记配置后 hits: {HITS}")

        # ② LLM 调用必须发往假服务
        from app.assistant.llm import chat, model_for

        HITS.clear()
        reply = chat("system", "ping")
        if "POST /v1/chat/completions" not in HITS:
            failures.append(f"LLM 请求没有走到平台配置的服务（hits={HITS}）")
        if reply != CHAT_REPLY:
            failures.append(f"chat 返回异常：{reply!r}")
        if model_for("sql") != "fake-chat":
            failures.append(f"model_for 未取到 DB 配置：{model_for('sql')!r}")
        print(f"chat 经由平台配置完成，reply={reply!r}")

        # ③ 向量化必须发往假服务
        from app.ai.embedding import embed_texts

        HITS.clear()
        outcome = embed_texts(["冒烟文本"])
        if "POST /v1/embeddings" not in HITS:
            failures.append(f"embedding 请求没有走到平台配置的服务（hits={HITS}）")
        if outcome.vectors != [EMB_VECTOR]:
            failures.append(f"向量化结果异常：{outcome.vectors}")
        print(f"embedding 经由平台配置完成，dim={outcome.dim}")

        # ④ 删除配置 → 回退环境变量
        r = client.get("/api/v1/admin/providers", headers=headers)
        for row in r.json():
            if row["name"] in (LLM_NAME, EMB_NAME):
                client.delete(f"/api/v1/admin/providers/{row['id']}", headers=headers)

        from app.ai.runtime import resolve_llm

        if not resolve_llm().source.startswith("env"):
            failures.append("删除配置后没有回退到环境变量")
        print(f"删除配置后回退：source={resolve_llm().source}")

        if args.cleanup:
            with session_factory() as session:
                session.query(ModelProvider).filter(
                    ModelProvider.name.in_([LLM_NAME, EMB_NAME])
                ).delete(synchronize_session=False)
                user = session.query(User).filter_by(username=admin_name).first()
                if user is not None:
                    session.query(ApiKey).filter_by(user_id=user.id).delete()
                    session.query(AuditLog).filter_by(user_id=user.id).delete()
                    session.delete(user)
                session.commit()
            print("cleaned up")
    finally:
        server.shutdown()
        engine.dispose()

    if failures:
        for item in failures:
            print(f"!! {item}")
        return 2
    print("PASS")
    return 0


class SimpleNamespaceSettings:
    """测试 ctx 需要的 settings 片段（限流用真实值即可）。"""

    rate_limit_per_minute = get_settings().rate_limit_per_minute
    rate_limit_per_day = get_settings().rate_limit_per_day


if __name__ == "__main__":
    raise SystemExit(main())
