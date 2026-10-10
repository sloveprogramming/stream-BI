"""LLM 模型路由与 JSON 解析的测试（Step 1b）。"""
from app.assistant import llm
from app.settings import get_settings


class FakeClient:
    """记录实际请求到哪个模型，并回放固定内容。"""

    def __init__(self, content: str):
        self.content = content
        self.model_seen: str | None = None
        client = self

        class _Completions:
            def create(self, model, messages, temperature):
                client.model_seen = model
                message = type("M", (), {"content": client.content})()
                choice = type("C", (), {"message": message})()
                return type("R", (), {"choices": [choice]})()

        self.chat = type("Chat", (), {"completions": _Completions()})()


def test_model_for_routes_sql_role():
    assert llm.model_for(llm.ROLE_SQL) == get_settings().llm_model


def test_model_for_routes_planner_role():
    assert llm.model_for(llm.ROLE_PLANNER) == get_settings().llm_planner_model


def test_model_for_falls_back_when_planner_unset(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "llm_planner_model", "")
    assert llm.model_for(llm.ROLE_PLANNER) == settings.llm_model


def test_sql_role_uses_default_client_and_model(monkeypatch):
    fake = FakeClient("SELECT 1")
    monkeypatch.setattr(llm, "build_client", lambda: fake)
    llm.generate_sql("sys", "q")
    assert fake.model_seen == get_settings().llm_model


def test_planner_role_uses_planner_client_and_model(monkeypatch):
    fake = FakeClient('{"sub_questions": []}')
    monkeypatch.setattr(llm, "build_planner_client", lambda: fake)
    llm.generate_json("sys", "q")
    assert fake.model_seen == get_settings().llm_planner_model


def test_injected_client_wins_over_role_routing(monkeypatch):
    def _boom():
        raise AssertionError("不应创建默认客户端")

    monkeypatch.setattr(llm, "build_planner_client", _boom)
    fake = FakeClient('{"ok": true}')
    assert llm.generate_json("sys", "q", client=fake) == {"ok": True}


def test_parse_json_variants():
    assert llm.parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert llm.parse_json('{"b": 2}') == {"b": 2}
    assert llm.parse_json('这是我的计划：{"c": 3} 完毕') == {"c": 3}
    # 解析不出来时返回空 dict，绝不抛异常
    assert llm.parse_json("完全不是 JSON") == {}
    assert llm.parse_json("[1, 2, 3]") == {}
    assert llm.parse_json("") == {}


def test_generate_json_returns_empty_dict_on_garbage(monkeypatch):
    fake = FakeClient("抱歉我无法回答")
    monkeypatch.setattr(llm, "build_planner_client", lambda: fake)
    assert llm.generate_json("sys", "q") == {}
