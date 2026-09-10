from app.assistant.llm import generate_sql


def _client(content: str):
    message = type("M", (), {"content": content})()
    choice = type("C", (), {"message": message})()
    resp = type("R", (), {"choices": [choice]})()
    completions = type(
        "Completions",
        (),
        {"create": lambda self, model, messages, temperature: resp},
    )()
    chat = type("Chat", (), {"completions": completions})()
    return type("Client", (), {"chat": chat})()


def test_extracts_sql_from_fenced_response(monkeypatch):
    monkeypatch.setattr(
        "app.assistant.llm.build_client",
        lambda: _client("```sql\nSELECT 1\n```"),
    )
    assert generate_sql("sys", "q") == "SELECT 1"


def test_strips_plain_sql(monkeypatch):
    monkeypatch.setattr(
        "app.assistant.llm.build_client",
        lambda: _client("SELECT 2;"),
    )
    assert generate_sql("sys", "q") == "SELECT 2"


def test_uses_injected_client():
    assert generate_sql("sys", "q", client=_client("SELECT 3")) == "SELECT 3"
