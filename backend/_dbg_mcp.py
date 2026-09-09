import socket, threading, time, httpx, sys
sys.path.insert(0, ".")
from app.main import create_app
import uvicorn

app = create_app()
with socket.socket() as s:
    s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
server = uvicorn.Server(config)
threading.Thread(target=server.run, daemon=True).start()
deadline = time.time() + 15
while time.time() < deadline:
    try:
        httpx.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=1); break
    except Exception: time.sleep(0.2)

body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-03-26", "capabilities": {},
    "clientInfo": {"name": "dbg", "version": "0"}}}
r = httpx.post(f"http://127.0.0.1:{port}/mcp", json=body, headers={"X-API-Key": "x.y", "Accept": "application/json, text/event-stream"}, timeout=10)
print("status:", r.status_code)
print("headers:", dict(r.headers))
print("body:", r.text[:400])
server.should_exit = True
