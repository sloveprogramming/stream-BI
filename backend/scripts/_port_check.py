import socket

for host, port in [("127.0.0.1", 55432), ("127.0.0.1", 3306)]:
    s = socket.socket()
    s.settimeout(2.0)
    try:
        s.connect((host, port))
        print(f"{port}: OPEN")
    except Exception as exc:
        print(f"{port}: CLOSED ({type(exc).__name__})")
    finally:
        s.close()

# 顺带确认能真的连上 PG
try:
    from app.db import make_engine
    from sqlalchemy import text

    e = make_engine("postgresql+psycopg://bisql:bisql@localhost:55432/bisql")
    with e.connect() as c:
        print("PG query ok, datasets =", c.execute(text("SELECT count(*) FROM datasets")).scalar())
    e.dispose()
except Exception as exc:
    print("PG connect FAIL:", type(exc).__name__, exc)
