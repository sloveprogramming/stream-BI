"""动态 few-shot：从「人工验证过的问答对」里检索最相似的例子，注入 prompt。

检索用**字符 bigram 相似度**，不引入向量库：短中文问句上够用，且零新增基础设施。
语料上千条后再换成 BGE-M3 + Milvus（接口不变，只换 `retrieve` 内部实现）。

权限：只返回「用户可见数据集能完全覆盖」的样例。否则 A 部门的历史问法与口径
会顺着 prompt 漏给 B 部门。

命令行：
    python -m app.assistant.fewshot --add "上周华东的销售额" "SELECT ..." --datasets sales_order
    python -m app.assistant.fewshot --list
    python -m app.assistant.fewshot --search "华东销售额"
"""
import argparse
import re

from app.access.grants import query_visible_datasets
from app.models import VerifiedQa

DEFAULT_LIMIT = 5

_PUNCT = re.compile(r"[\s,，。、？?！!：:；;（）()【】\[\]\"'“”‘’]+")


def normalize_question(text: str) -> str:
    return _PUNCT.sub("", (text or "").lower())


def bigrams(text: str) -> set[str]:
    normalized = normalize_question(text)
    if len(normalized) < 2:
        return {normalized} if normalized else set()
    return {normalized[i : i + 2] for i in range(len(normalized) - 1)}


def similarity(left: str, right: str) -> float:
    """字符 bigram 的 Jaccard 相似度，取值 0~1。"""
    a, b = bigrams(left), bigrams(right)
    if not a or not b:
        return 0.0
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def visible_dataset_names(session, user) -> set[str]:
    return {d.name for d in query_visible_datasets(session, user).all()}


def retrieve(session_factory, question: str, user, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """取最相似的若干样例；顺带累加命中次数（低频率调用，写入成本可忽略）。"""
    with session_factory() as session:
        allowed = visible_dataset_names(session, user)
        scored = []
        for row in session.query(VerifiedQa).all():
            keys = set(row.dataset_keys or [])
            if keys - allowed:
                continue
            score = similarity(question, row.question)
            if score <= 0:
                continue
            scored.append((score, row.hits or 0, row.id, row.question, row.sql))
        scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
        picked = scored[:limit]
        result = [
            {"id": row_id, "question": q, "sql": sql, "score": round(score, 4)}
            for score, _hits, row_id, q, sql in picked
        ]
        if result:
            session.query(VerifiedQa).filter(
                VerifiedQa.id.in_([item["id"] for item in result])
            ).update({VerifiedQa.hits: VerifiedQa.hits + 1}, synchronize_session=False)
            session.commit()
    return result


def remember(
    session_factory,
    question: str,
    sql: str,
    dataset_keys=None,
    tags=None,
    source: str = "manual",
) -> bool:
    """录入/覆盖一条验证过的问答对（同一问法只保留最新）。返回是否新建。"""
    question = (question or "").strip()
    sql = (sql or "").strip()
    if not question or not sql:
        raise ValueError("question 与 sql 都不能为空")
    with session_factory() as session:
        row = session.query(VerifiedQa).filter_by(question=question).first()
        created = row is None
        if row is None:
            row = VerifiedQa(question=question, sql=sql)
            session.add(row)
        row.sql = sql
        row.dataset_keys = list(dataset_keys or [])
        row.tags = list(tags or [])
        row.source = source
        session.commit()
    return created


def render(examples: list[dict]) -> str:
    """把样例渲染成 prompt 片段；没有样例返回空串。"""
    if not examples:
        return ""
    lines = ["", "", "以下是人工验证过的问法示例（参考写法与口径，不要照抄其中的筛选条件）："]
    for item in examples:
        lines.append(f"- 问：{item['question']}")
        lines.append(f"  参考 SQL：{item['sql']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="few-shot 样例库维护")
    parser.add_argument("--add", nargs=2, metavar=("QUESTION", "SQL"))
    parser.add_argument("--datasets", default="", help="逗号分隔的数据集名（决定谁能看到这条样例）")
    parser.add_argument("--tags", default="")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--search")
    args = parser.parse_args(argv)

    from app.db import make_engine, make_session_factory
    from app.settings import get_settings

    settings = get_settings()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    try:
        if args.add:
            question, sql = args.add
            created = remember(
                session_factory,
                question,
                sql,
                dataset_keys=[d for d in args.datasets.split(",") if d.strip()],
                tags=[t for t in args.tags.split(",") if t.strip()],
            )
            print(("已新建" if created else "已覆盖") + f"样例：{question}")
            return 0
        with session_factory() as session:
            rows = session.query(VerifiedQa).order_by(VerifiedQa.id).all()
            if args.search:
                rows = sorted(rows, key=lambda r: -similarity(args.search, r.question))
            for row in rows:
                print(f"[{row.id}] {row.question}")
                print(f"      datasets={row.dataset_keys} hits={row.hits}")
                print(f"      SQL: {row.sql}")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
