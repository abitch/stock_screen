"""策略抽象基类 - 选股策略的扩展点

后续新增策略只需继承 Strategy,实现 evaluate(),并在 registry 中注册。
当日选股 = 对每只股票的历史数据跑策略的 evaluate()。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class StrategyResult:
    """策略对单只股票的评估结果"""

    matched: bool                 # 是否选中
    close: float = 0.0            # 最新收盘价
    ref_value: float = 0.0        # 参考值(如 MA20 的值)
    distance_pct: float = 0.0     # 收盘价距参考值的百分比
    change_pct: float = 0.0       # 当日涨跌幅
    trade_date: str = ""          # 最新交易日
    reason: str = ""              # 选中/排除原因


class Strategy(ABC):
    """选股策略基类"""

    name: str = "Strategy"

    @property
    @abstractmethod
    def min_data_points(self) -> int:
        """策略所需的最少历史数据点数"""
        ...

    @abstractmethod
    def evaluate(self, hist: pd.DataFrame) -> Optional[StrategyResult]:
        """评估单只股票是否符合策略。

        Args:
            hist: 单只股票历史日线数据,索引为日期,含 open/high/low/close/volume

        Returns:
            StrategyResult;数据不足以判断时返回 None
        """
        ...
