import httpx

from app.sync.alert import send_alert


def test_empty_url_returns_false():
    assert send_alert("", "t", "d") is False


def test_posts_json_payload():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read()
        return httpx.Response(200)

    ok = send_alert(
        "http://hook.local/x",
        "同步失败",
        "task 1",
        transport=httpx.MockTransport(handler),
    )
    assert ok is True
    assert seen["url"] == "http://hook.local/x"
    assert "同步失败" in seen["body"].decode("utf-8")


def test_transport_error_returns_false():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    assert (
        send_alert(
            "http://hook.local/x", "t", "d", transport=httpx.MockTransport(handler)
        )
        is False
    )
