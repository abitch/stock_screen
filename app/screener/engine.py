"""筛选引擎:对全市场(或限定范围)并发跑策略,返回命中结果

并发模型:工作线程只做网络请求(拉行情)和纯计算(跑策略),
所有 SQLite 写入都在主线程的 as_completed 循环里完成,避免跨线程使用连接。
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

from app.data.fetcher import AkShareFetcher
from app.db.repository import Repository
from app.strategy.base import Strategy

logger = logging.getLogger(__name__)


class Screener:
    """筛选引擎"""

    def __init__(self, fetcher: AkShareFetcher, repo: Optional[Repository] = None,
                 max_workers: int = 6):
        """
        Args:
            fetcher: 数据获取器
            repo: 数据库仓储(可选,传入则缓存行情并保存结果)
            max_workers: 并发线程数
        """
        self.fetcher = fetcher
        self.repo = repo
        self.max_workers = max_workers

    def run(self, strategy: Strategy, limit: int = None,
            progress_callback: Callable[[int, int], None] = None) -> List[Dict]:
        """对全市场跑策略筛选。

        Args:
            strategy: 选股策略
            limit: 只筛选前 N 只(用于快速测试),None 表示全市场
            progress_callback: 进度回调 callback(done, total)

        Returns:
            命中股票的结果字典列表,按距均线百分比降序
        """
        stock_list = self.fetcher.get_stock_list()
        if stock_list.empty:
            logger.error("股票列表为空,无法筛选")
            return []

        if limit:
            stock_list = stock_list.head(limit)

        if self.repo is not None:
            self.repo.upsert_stocks(stock_list)

        total = len(stock_list)
        done = 0
        matched: List[Dict] = []

        def process(code: str, name: str) -> Tuple[str, str, Optional[pd.DataFrame]]:
            """工作线程:仅拉数据,不碰数据库。"""
            hist = self.fetcher.get_stock_hist(code)
            return code, name, hist

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(process, row["code"], row["name"]): row["code"]
                for _, row in stock_list.iterrows()
            }
            for future in as_completed(futures):
                done += 1
                if progress_callback:
                    progress_callback(done, total)
                try:
                    code, name, hist = future.result()
                    if hist is None or hist.empty:
                        continue

                    # DB 写入在主线程完成
                    if self.repo is not None:
                        self.repo.save_daily_price(code, hist)

                    result = strategy.evaluate(hist)
                    if result is None or not result.matched:
                        continue

                    matched.append({
                        "code": code,
                        "name": name,
                        "close": result.close,
                        "ma_value": result.ref_value,
                        "distance_pct": result.distance_pct,
                        "change_pct": result.change_pct,
                        "trade_date": result.trade_date,
                    })
                except Exception as e:
                    logger.debug("处理 %s 失败: %s", futures[future], e)

        matched.sort(key=lambda x: x["distance_pct"], reverse=True)

        # 保存运行记录
        if self.repo is not None:
            run_id = self.repo.create_run(
                ma_period=getattr(strategy, "ma_period", 0),
                strategy=strategy.name,
                total=total,
                matched=len(matched),
            )
            self.repo.save_results(run_id, matched)
            logger.info("筛选结果已保存,run_id=%d", run_id)

        return matched
