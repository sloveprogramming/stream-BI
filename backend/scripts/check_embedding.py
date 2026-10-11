"""看当前环境的向量能力可用性。

一条命令回答「我这台机器能不能跑 `embed.text` / `dedup.semantic`」：

    cd backend
    python scripts/check_embedding.py          # 只看状态（不下载、不调用）
    python scripts/check_embedding.py --try    # 真的向量化一段文本

⚠️ `--try` 在走本地模型时**首次会下载约 2.2GB 权重**，请确认带宽与磁盘再跑。

本脚本只读配置 + 做 import 探测，默认不产生任何副作用。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ai.embedding import api_configured, local_available, resolve_status  # noqa: E402
from app.settings import get_settings  # noqa: E402


def describe() -> tuple[int, str]:
    """返回 (退出码, 报告文本)。"""
    settings = get_settings()
    status = resolve_status()
    local_ok, local_why = local_available()

    lines = [
        "=== 向量能力自检 ===",
        f"配置的 provider : {status.configured}",
        f"配置的 model    : {status.model or '(未设置)'}",
        f"API base        : {settings.embedding_api_base or '(未配置)'}",
        f"API key         : {'已设置' if settings.embedding_api_key else '(未设置)'}",
        f"本地依赖        : {'可用' if local_ok else local_why}",
        "",
        f"当前会使用      : {status.provider}",
        f"判定理由        : {status.reason}",
    ]
    if not status.usable:
        lines += ["", status.fix_hint]
    return (0 if status.usable else 1), "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="向量能力自检")
    # 注意 dest 不能叫 try —— `args.try` 是语法错误（try 是关键字）
    parser.add_argument(
        "--try",
        dest="try_run",
        action="store_true",
        help="真的向量化一段文本（本地模型首次会下载约 2.2GB 权重）",
    )
    args = parser.parse_args()

    code, report = describe()
    print(report)

    if not args.try_run:
        return code
    if code != 0:
        print("\n当前不可用，--try 无法执行。")
        return code

    print("\n=== 试跑一次 ===")
    from app.ai.embedding import embed_texts, parse_vector, serialize_vector

    outcome = embed_texts(["新农哥碧根果", "新农哥碧根果（袋装）", "东北松子"])
    print(f"provider : {outcome.provider}")
    print(f"model    : {outcome.model}")
    print(f"dim      : {outcome.dim}")
    for text, vector in zip(["碧根果", "碧根果（袋装）", "东北松子"], outcome.vectors):
        restored = parse_vector(serialize_vector(vector))
        print(f"  {text:<14} 前 3 维 {[round(v, 4) for v in vector[:3]]}  往返一致={len(restored) == len(vector)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
