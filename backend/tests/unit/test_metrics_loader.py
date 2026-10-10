"""指标字典的加载与校验 —— 重点是**错误路径**。

为什么盯错误路径：口径字典真正可怕的不是"报错太多"，而是"写错了却静默生效"。
每一个 MUST 拒绝的场景，这里都写成硬断言。
"""
import dataclasses

import pytest
import yaml

from app.metrics import (
    MetricNotFoundError,
    MetricUnavailableError,
    MetricValidationError,
    ambiguous_metrics,
    clarification_for,
    extract_columns,
    load_dictionary,
)


def _write(workdir, entries):
    path = workdir / "metrics.yml"
    path.write_text(
        yaml.safe_dump(
            {"version": 1, "metrics_dictionary": entries}, allow_unicode=True, sort_keys=False
        ),
        encoding="utf-8",
    )
    return path


def _metric(**overrides):
    base = {
        "name": "gross_margin",
        "label": "毛利率",
        "expr": "(amount - cost) / nullif(amount, 0)",
        "dataset": "sales_order",
        "owner": "财务共享中心",
        "synonyms": ["毛利", "毛利率"],
        "grain": ["order_id"],
        "unit": "%",
        "description": "支付口径",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------- 正常路径


def test_loads_all_declared_fields(workdir):
    dictionary = load_dictionary(_write(workdir, [_metric()]))
    assert len(dictionary) == 1
    metric = dictionary.metrics[0]
    assert metric.name == "gross_margin"
    assert metric.label == "毛利率"
    assert metric.expr == "(amount - cost) / nullif(amount, 0)"
    assert metric.dataset == "sales_order"
    assert metric.owner == "财务共享中心"
    assert metric.synonyms == ("毛利", "毛利率")
    assert metric.grain == ("order_id",)
    assert metric.unit == "%"
    assert metric.description == "支付口径"


def test_loaded_structures_are_immutable(workdir):
    """字典是契约：任何人都不该就地改它。"""
    dictionary = load_dictionary(_write(workdir, [_metric()]))
    with pytest.raises(dataclasses.FrozenInstanceError):
        dictionary.metrics[0].expr = "1"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        dictionary.warnings = ()  # type: ignore[misc]


def test_reference_columns_come_from_the_formula(workdir):
    dictionary = load_dictionary(_write(workdir, [_metric()]))
    assert dictionary.metrics[0].columns == ("amount", "cost")


def test_extract_columns_ignores_functions_and_literals():
    """函数名和常量不是列名 —— 这是「引用的列必须存在」这条校验的基石。"""
    assert extract_columns("sold_qty / nullif(stock_qty + sold_qty, 0)") == (
        "sold_qty",
        "stock_qty",
    )
    assert extract_columns("amount * 1.13") == ("amount",)
    assert extract_columns("CAST(amount AS numeric)") == ("amount",)


def test_extract_columns_rejects_constant_only_formula():
    with pytest.raises(MetricValidationError, match="没有引用任何列"):
        extract_columns("1 + 1")


# ---------------------------------------------------------------- 结构错误


@pytest.mark.parametrize(
    "overrides, keyword",
    [
        ({"expr": ""}, "缺少 expr"),
        ({"expr": None}, "缺少 expr"),
        ({"owner": ""}, "缺少 owner"),
        ({"label": ""}, "缺少 label"),
        ({"dataset": ""}, "缺少 dataset"),
        ({"name": ""}, "缺少 name"),
        ({"name": "毛利率"}, "不合法"),
        ({"name": "2margin"}, "不合法"),
    ],
)
def test_incomplete_definition_is_rejected(workdir, overrides, keyword):
    """缺什么报什么，并且**不允许静默跳过该指标继续加载**。"""
    path = _write(workdir, [_metric(**overrides)])
    with pytest.raises(MetricValidationError, match=keyword):
        load_dictionary(path)


def test_duplicate_name_is_rejected(workdir):
    path = _write(workdir, [_metric(), _metric(label="毛利率（另一个口径）")])
    with pytest.raises(MetricValidationError, match="指标名重复"):
        load_dictionary(path)


def test_missing_file_is_rejected(workdir):
    with pytest.raises(MetricValidationError, match="不存在"):
        load_dictionary(workdir / "nope.yml")


def test_missing_dictionary_key_is_rejected(workdir):
    path = workdir / "metrics.yml"
    path.write_text("version: 1\n", encoding="utf-8")
    with pytest.raises(MetricValidationError, match="metrics_dictionary"):
        load_dictionary(path)


def test_unparseable_formula_is_rejected(workdir):
    path = _write(workdir, [_metric(expr="amount -")])
    with pytest.raises(MetricValidationError, match="不是合法的 SQL 表达式"):
        load_dictionary(path)


# ---------------------------------------------------------------- 语义校验


def test_column_not_in_dataset_is_rejected(workdir):
    """定位到「哪个指标、哪个列、数据集现有什么列」—— 否则排错要自己去翻。"""
    path = _write(workdir, [_metric(expr="(amount - discount) / nullif(amount, 0)")])
    with pytest.raises(MetricValidationError) as excinfo:
        load_dictionary(path, datasets={"sales_order": ["order_id", "amount", "cost"]})

    message = str(excinfo.value)
    assert "gross_margin" in message
    assert "discount" in message
    assert "order_id, amount, cost" in message


def test_column_check_skipped_without_dataset_input(workdir):
    """不给数据集清单时只做结构校验 —— 这与「静默跳过」不同：生产链路必定带清单。"""
    dictionary = load_dictionary(_write(workdir, [_metric(expr="nope_col + 1")]))
    assert len(dictionary) == 1
    assert dictionary.unavailable == frozenset()


def test_dataset_not_registered_marks_metric_unavailable(workdir):
    """数据集尚未上架 → 不抛错、不阻塞其它指标，只把它标记为不可用。

    数据集是本项目**运行时上架**的，让代码仓库强制"某个数据集必须已存在"
    会把指标字典变成一堆部署顺序陷阱。
    """
    path = _write(
        workdir,
        [
            _metric(),
            _metric(
                name="sell_through_rate",
                label="动销率",
                expr="sold_qty / nullif(stock_qty + sold_qty, 0)",
                dataset="inventory_snapshot",
                owner="商品运营",
            ),
        ],
    )
    dictionary = load_dictionary(
        path, datasets={"sales_order": ["order_id", "amount", "cost"]}
    )

    assert len(dictionary) == 2  # 另一个指标照样加载出来
    assert dictionary.unavailable == frozenset({"sell_through_rate"})
    assert any("inventory_snapshot" in w for w in dictionary.warnings)

    dictionary.get("gross_margin")  # 可用的照常用
    with pytest.raises(MetricUnavailableError, match="尚未上架"):
        dictionary.get("sell_through_rate")
    # 不可用的指标不参与歧义判定，免得把问题引到死路上
    assert ambiguous_metrics(dictionary, "动销率是多少") == ()


# ---------------------------------------------------------------- 检索


def test_search_by_synonym_hits_full_name(workdir):
    """业务方不会记得全称：搜「毛利」必须命中「毛利率」。"""
    dictionary = load_dictionary(_write(workdir, [_metric()]))
    assert [m.name for m in dictionary.search("毛利")] == ["gross_margin"]
    assert [m.name for m in dictionary.search("毛利率")] == ["gross_margin"]
    assert [m.name for m in dictionary.search("gross_margin")] == ["gross_margin"]
    assert dictionary.search("库存周转") == []


def test_unknown_metric_error_lists_available_names(workdir):
    dictionary = load_dictionary(
        _write(
            workdir,
            [
                _metric(),
                _metric(
                    name="sell_through_rate",
                    label="动销率",
                    expr="sold_qty / nullif(stock_qty + sold_qty, 0)",
                ),
            ],
        )
    )
    with pytest.raises(MetricNotFoundError) as excinfo:
        dictionary.get("gross_margin_typo")
    message = str(excinfo.value)
    assert "gross_margin" in message
    assert "sell_through_rate" in message


def test_render_injects_definition_and_synonyms(workdir):
    dictionary = load_dictionary(_write(workdir, [_metric()]))
    text = dictionary.render()
    assert "毛利率" in text
    assert "毛利" in text  # 同义词要让模型看到
    assert "(amount - cost) / nullif(amount, 0)" in text  # 口径本体
    assert "gross_margin" in text


def test_render_is_empty_when_nothing_usable(workdir):
    dictionary = load_dictionary(_write(workdir, [_metric()]), datasets={})
    assert dictionary.render() == ""


# ---------------------------------------------------------------- 口径歧义


def _two_quotas(workdir):
    return load_dictionary(
        _write(
            workdir,
            [
                _metric(
                    name="sales_amount_paid",
                    label="支付口径销售额",
                    expr="paid_amount",
                    synonyms=["销售额", "支付"],
                ),
                _metric(
                    name="sales_amount_collected",
                    label="回款口径销售额",
                    expr="collected_amount",
                    synonyms=["销售额", "回款"],
                ),
            ],
        )
    )


def test_ambiguous_when_two_metrics_share_the_alias(workdir):
    hits = ambiguous_metrics(_two_quotas(workdir), "上月销售额是多少")
    assert {m.name for m in hits} == {
        "sales_amount_paid",
        "sales_amount_collected",
    }


def test_not_ambiguous_when_a_disambiguating_word_is_present(workdir):
    """出现了只属于某一个口径的词 → 用户已经说清了，不该再反问。"""
    assert ambiguous_metrics(_two_quotas(workdir), "上月回款销售额") == ()
    assert ambiguous_metrics(_two_quotas(workdir), "上月支付销售额") == ()


def test_not_ambiguous_when_only_one_metric_matches(workdir):
    dictionary = load_dictionary(_write(workdir, [_metric()]))
    assert ambiguous_metrics(dictionary, "上月毛利率是多少") == ()


def test_clarification_lists_the_candidates(workdir):
    hits = ambiguous_metrics(_two_quotas(workdir), "上月销售额")
    clarification = clarification_for(hits)
    assert len(clarification["options"]) == 2
    assert any("支付" in o for o in clarification["options"])
    assert any("回款" in o for o in clarification["options"])
