"""筛选逻辑单测 - 用构造数据,不依赖网络"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.strategy.ma_above import MAAboveStrategy


def _make_hist(closes):
    """用收盘价序列构造历史行情 DataFrame"""
    dates = pd.date_range(end="2026-06-22", periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1000] * len(closes),
        },
        index=dates,
    )


def test_above_ma_matched():
    """收盘价高于 MA20 -> 命中"""
    # 前 20 天在 10 元,最后一天涨到 20,MA20 必然低于 20
    closes = [10.0] * 20 + [20.0]
    strategy = MAAboveStrategy(ma_period=20)
    result = strategy.evaluate(_make_hist(closes))

    assert result is not None
    assert result.matched is True
    assert result.close == 20.0
    assert result.ref_value < 20.0
    assert result.distance_pct > 0


def test_below_ma_not_matched():
    """收盘价低于 MA20 -> 不命中"""
    # 前 20 天在 20 元,最后一天跌到 10,MA20 必然高于 10
    closes = [20.0] * 20 + [10.0]
    strategy = MAAboveStrategy(ma_period=20)
    result = strategy.evaluate(_make_hist(closes))

    assert result is not None
    assert result.matched is False
    assert result.close == 10.0
    assert result.ref_value > 10.0
    assert result.distance_pct < 0


def test_insufficient_data():
    """数据不足以算 MA20 -> 返回 None"""
    closes = [10.0] * 10
    strategy = MAAboveStrategy(ma_period=20)
    assert strategy.evaluate(_make_hist(closes)) is None


def test_change_pct():
    """当日涨跌幅计算正确"""
    closes = [10.0] * 19 + [10.0, 11.0]  # 最后一天从 10 -> 11,涨 10%
    strategy = MAAboveStrategy(ma_period=20)
    result = strategy.evaluate(_make_hist(closes))

    assert result is not None
    assert result.change_pct == pytest.approx(10.0, abs=0.01)


def test_ma_value_correct():
    """MA 值计算正确"""
    closes = [float(i) for i in range(1, 22)]  # 1..21
    strategy = MAAboveStrategy(ma_period=20)
    result = strategy.evaluate(_make_hist(closes))

    # 最后 20 个值是 2..21,均值 = (2+21)/2 = 11.5
    assert result is not None
    assert result.ref_value == pytest.approx(11.5, abs=0.01)
    assert result.matched is True  # 收盘 21 > MA 11.5


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
