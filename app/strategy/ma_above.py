"""内置策略:筛选当日收盘价在 N 日均线之上的股票"""

from typing import Optional

import pandas as pd

from .base import Strategy, StrategyResult


class MAAboveStrategy(Strategy):
    """股价站上 N 日均线策略(默认 MA20)"""

    def __init__(self, ma_period: int = 20):
        self.ma_period = ma_period
        self.name = f"MA{ma_period}AboveStrategy"

    @property
    def min_data_points(self) -> int:
        return self.ma_period

    def evaluate(self, hist: pd.DataFrame) -> Optional[StrategyResult]:
        if hist is None or len(hist) < self.ma_period:
            return None

        ma = hist["close"].rolling(window=self.ma_period).mean()
        latest = hist.iloc[-1]
        close = float(latest["close"])
        ma_value = ma.iloc[-1]

        if pd.isna(ma_value):
            return None
        ma_value = float(ma_value)

        # 当日涨跌幅
        if len(hist) >= 2:
            prev_close = float(hist.iloc[-2]["close"])
            change_pct = (close - prev_close) / prev_close * 100 if prev_close else 0.0
        else:
            change_pct = 0.0

        distance_pct = (close - ma_value) / ma_value * 100 if ma_value else 0.0
        matched = close > ma_value

        trade_date = hist.index[-1]
        trade_date_str = (
            trade_date.strftime("%Y-%m-%d")
            if hasattr(trade_date, "strftime")
            else str(trade_date)
        )

        return StrategyResult(
            matched=matched,
            close=round(close, 2),
            ref_value=round(ma_value, 2),
            distance_pct=round(distance_pct, 2),
            change_pct=round(change_pct, 2),
            trade_date=trade_date_str,
            reason=f"收盘 {close:.2f} {'>' if matched else '<='} MA{self.ma_period} {ma_value:.2f}",
        )
