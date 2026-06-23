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
            codes: list = None,
            progress_callback: Callable[[int, int], None] = None,
            force_refresh: bool = False) -> List[Dict]:
        """对全市场或指定股票列表跑策略筛选(支持增量缓存)。

        Args:
            strategy: 选股策略
            limit: 只筛选前 N 只(用于快速测试),None 表示全市场
            codes: 指定股票列表 [(code, name), ...];传入则只筛这些(优先于 limit)
            progress_callback: 进度回调 callback(done, total)
            force_refresh: 强制重新联网拉取,忽略缓存

        Returns:
            命中股票的结果字典列表,按距均线百分比降序
        """
        if codes is not None:
            stock_list = pd.DataFrame(codes, columns=["code", "name"])
        else:
            stock_list = self.fetcher.get_stock_list()
        if stock_list.empty:
            logger.error("股票列表为空,无法筛选")
            return []

        if limit and codes is None:
            stock_list = stock_list.head(limit)

        if self.repo is not None:
            self.repo.upsert_stocks(stock_list)

        # 增量缓存:确定最新交易日 + 读取每只股票的缓存状态
        latest_trading_date = None
        cache_status = {}
        min_points = getattr(strategy, "min_data_points", 20)
        if self.repo is not None and not force_refresh:
            latest_trading_date = self._probe_latest_trading_date()
            cache_status = self.repo.get_cache_status()
            if latest_trading_date:
                logger.info("最新交易日 %s,已缓存 %d 只", latest_trading_date, len(cache_status))

        total = len(stock_list)
        done = 0
        matched: List[Dict] = []
        cache_hits = 0

        def needs_fetch(code: str) -> bool:
            """判断该股票是否需要联网拉取(缓存不够新/不够多则需要)。"""
            if force_refresh or latest_trading_date is None:
                return True
            cached = cache_status.get(code)
            if cached is None:
                return True
            cached_latest, cached_count = cached
            # 缓存已到最新交易日,且数据点足够算策略 -> 用缓存
            return not (cached_latest >= latest_trading_date and cached_count >= min_points)

        def process(code: str, name: str) -> Tuple[str, str, Optional[pd.DataFrame], bool]:
            """工作线程:按需联网拉取;命中缓存的不在此联网(留主线程读库)。"""
            if needs_fetch(code):
                hist = self.fetcher.get_stock_hist(code)
                return code, name, hist, False  # False=非缓存,需写库
            return code, name, None, True        # True=走缓存,主线程读库

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
                    code, name, hist, from_cache = future.result()

                    if from_cache:
                        # 缓存命中:主线程从库读取
                        cache_hits += 1
                        hist = self.repo.load_daily_price(code)
                    elif hist is not None and not hist.empty and self.repo is not None:
                        # 新拉取的数据:写入缓存(主线程)
                        self.repo.save_daily_price(code, hist)

                    if hist is None or hist.empty:
                        continue

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
        logger.info("筛选完成: 共 %d 只,缓存命中 %d 只,联网 %d 只,命中策略 %d 只",
                    total, cache_hits, total - cache_hits, len(matched))

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

    def _probe_latest_trading_date(self, ref_code: str = "000001") -> Optional[str]:
        """探针:用参考股票(默认平安银行)取最新交易日,格式 YYYY-MM-DD。

        用真实行情确定最新交易日,避免自行判断节假日/周末。
        """
        try:
            hist = self.fetcher.get_stock_hist(ref_code)
            if hist is None or hist.empty:
                return None
            return hist.index[-1].strftime("%Y-%m-%d")
        except Exception as e:
            logger.warning("探测最新交易日失败: %s", str(e)[:80])
            return None
