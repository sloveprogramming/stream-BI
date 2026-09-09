import httpx


def send_alert(
    webhook_url: str,
    title: str,
    detail: str,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    """POST an alert to webhook_url. Never raises: alert failures must not
    affect the main sync flow. Returns True only on a 2xx response."""
    if not webhook_url:
        return False
    try:
        kwargs: dict = {"timeout": 10.0}
        if transport is not None:
            kwargs["transport"] = transport
        with httpx.Client(**kwargs) as client:
            resp = client.post(webhook_url, json={"title": title, "detail": detail})
            return resp.status_code < 300
    except Exception:  # noqa: BLE001 告警失败不影响主流程
        return False
