"""清理上次被中断的测试运行留下的孤儿 model_providers 行。

这些是 test_provider_api 的 cleanup fixture 没来得及删掉的残留，
它们会让"该用途第一条自动成为默认"的测试失败。
只删名字形如 ds_<8hex> / mp_<8hex> 的测试残留，不动真实配置。
"""
from app.db import make_engine
from sqlalchemy import text

e = make_engine("postgresql+psycopg://bisql:bisql@localhost:55432/bisql")
with e.begin() as c:
    rows = c.execute(
        text("SELECT id, name, kind, is_default FROM model_providers ORDER BY id")
    ).fetchall()
    print("清理前:", [dict(r._mapping) for r in rows])
    # 测试生成的 provider 名形如 ds_xxxxxxxx（8 位十六进制）
    deleted = 0
    for r in rows:
        name = r._mapping["name"]
        parts = name.split("_")
        if len(parts) == 2 and len(parts[1]) == 8 and all(
            ch in "0123456789abcdef" for ch in parts[1]
        ):
            c.execute(
                text("DELETE FROM model_providers WHERE id=:i"), {"i": r._mapping["id"]}
            )
            deleted += 1
            print("  删除测试残留:", name)
    print("deleted:", deleted)
    left = c.execute(text("SELECT count(*) FROM model_providers")).scalar()
    print("清理后剩余:", left)
e.dispose()
