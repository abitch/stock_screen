"""akshare 数据获取封装

提供 A 股股票列表和历史日线行情,带限流与重试,降低被数据源封 IP 的风险。
"""

import random
import time
import logging
from typing import Optional

import pandas as pd
import akshare as ak

logger = logging.getLogger(__name__)


def _safe_float(val) -> float:
    """安全转 float,空值/异常返回 NaN(停牌等情况)。"""
    try:
        f = float(val)
        return f
    except (TypeError, ValueError):
        return float("nan")


def _clean_name(series: pd.Series) -> pd.Series:
    """清洗股票名称:去掉数据源夹带的空白(如 '五 粮 液' -> '五粮液')。"""
    return series.astype(str).str.replace(r"\s+", "", regex=True)


class AkShareFetcher:
    """A 股数据获取器(基于 akshare)"""

    def __init__(self, config: dict, repo=None):
        """
        Args:
            config: 完整配置字典(读取 data 段)
            repo: 可选 Repository,用于把股票列表持久化缓存到本地库,
                  避免每次启动都联网拉全市场(约 5500 只,十余秒)
        """
        data_cfg = config.get("data", {})
        rate_cfg = data_cfg.get("rate_limit", {})

        self.adjust = data_cfg.get("adjust", "hfq")
        self.hist_days = data_cfg.get("hist_days", 60)
        self.delay_min = rate_cfg.get("request_delay_min", 0.2)
        self.delay_max = rate_cfg.get("request_delay_max", 0.5)
        self.max_retries = rate_cfg.get("max_retries", 3)
        # 历史行情回退源: auto=东方财富失败后切baostock, eastmoney=只用东方财富, baostock=只用baostock
        self.hist_source = data_cfg.get("hist_source", "auto")
        # 股票列表缓存有效期(天),超过则联网刷新
        self.stock_list_ttl_days = data_cfg.get("stock_list_ttl_days", 7)

        self._config = config
        self._repo = repo
        self._stock_list_cache: Optional[pd.DataFrame] = None
        self._baostock = None  # 惰性初始化

    def _get_baostock(self):
        """惰性获取 baostock fetcher(首次使用时才登录)"""
        if self._baostock is None:
            from app.data.baostock_fetcher import BaostockFetcher
            self._baostock = BaostockFetcher(self._config)
        return self._baostock

    def _sleep(self):
        """请求间随机延时,降低被封风险"""
        time.sleep(random.uniform(self.delay_min, self.delay_max))

    def get_stock_list(self, use_cache: bool = True) -> pd.DataFrame:
        """获取全市场 A 股列表。

        优先级:进程内存缓存 -> 本地库(未过期) -> 联网拉取并回写库。

        Returns:
            DataFrame,列含 code(代码) name(名称)
        """
        if use_cache and self._stock_list_cache is not None:
            return self._stock_list_cache

        # 本地库缓存:未超过 TTL 直接用,避免联网等十余秒
        if use_cache and self._repo is not None:
            try:
                age = self._repo.stocks_age_days()
                if age is not None and age <= self.stock_list_ttl_days:
                    cached = self._repo.get_all_stocks()
                    if not cached.empty:
                        self._stock_list_cache = cached
                        logger.info("股票列表读自本地缓存,共 %d 只(缓存 %.1f 天前)",
                                    len(cached), age)
                        return cached
            except Exception as e:
                logger.warning("读取本地股票列表缓存失败,改为联网: %s", str(e)[:120])

        result = self._fetch_stock_list_online()
        if not result.empty:
            self._stock_list_cache = result
            if self._repo is not None:
                try:
                    self._repo.upsert_stocks(result)
                    logger.info("股票列表已回写本地缓存,共 %d 只", len(result))
                except Exception as e:
                    logger.warning("回写股票列表缓存失败: %s", str(e)[:120])
        return result

    def _fetch_stock_list_online(self) -> pd.DataFrame:
        """联网拉取全市场 A 股列表(多接口回退)。"""
        # 多接口回退:不同网络/数据源稳定性不一,逐个尝试直到成功
        # stock_info_a_code_name 最轻量且不依赖 eastmoney push2 接口,优先
        sources = [
            ("stock_info_a_code_name", self._fetch_list_code_name),
            ("stock_zh_a_spot(sina)", self._fetch_list_sina),
            ("stock_zh_a_spot_em(eastmoney)", self._fetch_list_em),
        ]

        for source_name, fetch_fn in sources:
            for attempt in range(1, self.max_retries + 1):
                try:
                    result = fetch_fn()
                    if result is None or result.empty:
                        raise ValueError("返回的股票列表为空")
                    logger.info("获取股票列表成功(%s),共 %d 只", source_name, len(result))
                    return result
                except Exception as e:
                    logger.warning(
                        "获取股票列表失败(%s 第 %d 次): %s",
                        source_name, attempt, str(e)[:120],
                    )
                    if attempt < self.max_retries:
                        time.sleep(attempt)

        logger.error("所有数据源获取股票列表均失败")
        return pd.DataFrame(columns=["code", "name"])

    def search(self, keyword: str, limit: int = None) -> pd.DataFrame:
        """按代码或名称搜索股票。

        Args:
            keyword: 代码片段或名称片段
            limit: 最多返回条数;None 表示返回全部匹配(交互层自行分页)

        Returns:
            DataFrame,列含 code name
        """
        stock_list = self.get_stock_list()
        if stock_list.empty:
            return stock_list
        keyword = keyword.strip().replace(" ", "")
        if not keyword:
            return stock_list.head(0)
        # 名称已在入库时去空白;搜索也去掉空白,保证 '五 粮 液'/'五粮液' 都能命中
        names = stock_list["name"].astype(str).str.replace(r"\s+", "", regex=True)
        mask = stock_list["code"].str.contains(keyword, na=False) | \
            names.str.contains(keyword, na=False)
        matched = stock_list[mask].reset_index(drop=True)
        if limit is not None:
            matched = matched.head(limit)
        return matched

    @staticmethod
    def _fetch_list_code_name() -> pd.DataFrame:
        """A 股代码+名称列表(轻量,不含行情)"""
        df = ak.stock_info_a_code_name()
        out = df[["code", "name"]].reset_index(drop=True)
        out["name"] = _clean_name(out["name"])
        return out

    @staticmethod
    def _fetch_list_sina() -> pd.DataFrame:
        """新浪实时行情快照(取代码名称)"""
        df = ak.stock_zh_a_spot()
        out = df[["代码", "名称"]].rename(
            columns={"代码": "code", "名称": "name"}
        ).reset_index(drop=True)
        out["name"] = _clean_name(out["name"])
        return out

    @staticmethod
    def _fetch_list_em() -> pd.DataFrame:
        """东方财富实时行情快照(取代码名称)"""
        df = ak.stock_zh_a_spot_em()
        out = df[["代码", "名称"]].rename(
            columns={"代码": "code", "名称": "name"}
        ).reset_index(drop=True)
        out["name"] = _clean_name(out["name"])
        return out

    def get_realtime_quotes(self, codes) -> dict:
        """批量获取实时行情快照(最新价/涨跌额/涨跌幅),用于自选股展示。

        用新浪轻量行情接口(hq.sinajs.cn)按代码精确批量查询,
        一次请求返回所有指定代码,通常几百毫秒。
        失败时回退到全市场快照过滤(较慢)。

        Args:
            codes: 纯数字代码的可迭代对象(如 ['000001', '600000'])

        Returns:
            dict: {code: {"name", "price", "change", "change_pct"}}
                  取不到的代码不会出现在结果中
        """
        want = [str(c).strip() for c in codes]
        want = [c for c in want if c]
        if not want:
            return {}

        # 首选:新浪轻量接口,按代码精确查,只传自选股,不拉全市场
        for attempt in range(1, self.max_retries + 1):
            try:
                quotes = self._fetch_quotes_sina_light(want)
                if quotes:
                    logger.info("实时行情成功(sina-light),命中 %d/%d 只",
                                len(quotes), len(want))
                    return quotes
                raise ValueError("行情为空或无匹配")
            except Exception as e:
                logger.warning("实时行情失败(sina-light 第 %d 次): %s",
                               attempt, str(e)[:120])
                if attempt < self.max_retries:
                    self._sleep()

        # 回退:东方财富全市场快照(纯数字代码),仅在轻量接口失败时启用
        try:
            want_set = set(want)
            quotes = self._fetch_spot_em(want_set)
            if quotes:
                logger.info("实时行情成功(eastmoney-spot 回退),命中 %d/%d 只",
                            len(quotes), len(want))
                return quotes
        except Exception as e:
            logger.warning("实时行情回退(eastmoney-spot)失败: %s", str(e)[:120])

        logger.error("所有数据源获取实时行情均失败")
        return {}

    def _fetch_quotes_sina_light(self, codes) -> dict:
        """新浪轻量行情接口: https://hq.sinajs.cn/list=sh600000,sz000858

        返回形如:
            var hq_str_sh600000="浦发银行,今开,昨收,现价,最高,最低,...";
        字段: 0=名称 1=今开 2=昨收 3=现价 4=最高 5=最低
        """
        import requests

        # 纯数字代码 -> 新浪带市场前缀,并建立反查表
        prefixed = {self._to_sina_code(c): c for c in codes}
        url = "https://hq.sinajs.cn/list=" + ",".join(prefixed.keys())
        resp = requests.get(
            url,
            headers={"Referer": "https://finance.sina.com.cn"},
            timeout=8,
        )
        resp.encoding = "gbk"  # 新浪该接口为 GBK 编码
        resp.raise_for_status()

        quotes = {}
        for line in resp.text.splitlines():
            line = line.strip()
            if not line.startswith("var hq_str_"):
                continue
            # var hq_str_sh600000="...";
            try:
                key, payload = line[len("var hq_str_"):].split("=", 1)
            except ValueError:
                continue
            sina_code = key.strip()
            pure = prefixed.get(sina_code)
            if pure is None:
                continue
            payload = payload.strip().strip('";')
            fields = payload.split(",")
            if len(fields) < 4 or not fields[0]:
                continue  # 停牌或无数据
            name = fields[0].replace(" ", "")
            prev_close = _safe_float(fields[2])
            price = _safe_float(fields[3])
            if price != price or price == 0:  # NaN 或 0:停牌
                quotes[pure] = {"name": name, "price": float("nan"),
                                "change": float("nan"), "change_pct": float("nan")}
                continue
            change = price - prev_close if prev_close == prev_close else float("nan")
            change_pct = (change / prev_close * 100) if prev_close else float("nan")
            quotes[pure] = {
                "name": name,
                "price": price,
                "change": change,
                "change_pct": change_pct,
            }
        return quotes

    @staticmethod
    def _fetch_spot_em(want: set) -> dict:
        """东方财富全市场快照,代码为纯数字,直接匹配。"""
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return {}
        df = df[df["代码"].isin(want)]
        quotes = {}
        for _, row in df.iterrows():
            quotes[str(row["代码"])] = {
                "name": row["名称"],
                "price": _safe_float(row.get("最新价")),
                "change": _safe_float(row.get("涨跌额")),
                "change_pct": _safe_float(row.get("涨跌幅")),
            }
        return quotes

    def get_stock_hist(self, code: str, days: int = None) -> pd.DataFrame:
        """获取单只股票的历史日线行情。

        Args:
            code: 纯数字股票代码(如 '000001')
            days: 拉取天数,默认用配置的 hist_days

        Returns:
            DataFrame,索引为日期,列含 open high low close volume

        数据源由 hist_source 控制:
            sina(默认)  - 新浪,支持高并发,最快
            baostock     - baostock 自有服务器,稳定但单连接串行较慢
            eastmoney    - 东方财富,数据实时但部分网络被拦截
            auto         - 依次尝试 新浪 -> baostock,任一成功即返回
        """
        days = days or self.hist_days

        if self.hist_source == "sina":
            return self._fetch_hist_sina(code, days)
        if self.hist_source == "baostock":
            return self._get_baostock().get_stock_hist(code, days=days)
        if self.hist_source == "eastmoney":
            return self._fetch_hist_eastmoney(code, days)

        # auto: 新浪优先,失败回退 baostock
        df = self._fetch_hist_sina(code, days)
        if not df.empty:
            return df
        try:
            return self._get_baostock().get_stock_hist(code, days=days)
        except Exception as e:
            logger.debug("baostock 回退获取 %s 失败: %s", code, str(e)[:80])
        return pd.DataFrame()

    @staticmethod
    def _to_sina_code(code: str) -> str:
        """纯数字代码转新浪格式(带市场前缀): sh600000 / sz000001 / bj830000"""
        code = code.strip()
        if code.startswith(("60", "68", "9")):
            return "sh" + code
        if code.startswith(("4", "8")):
            return "bj" + code
        return "sz" + code

    def _fetch_hist_sina(self, code: str, days: int) -> pd.DataFrame:
        """新浪源:一次返回全部历史,截取所需尾部。支持高并发。"""
        for attempt in range(1, self.max_retries + 1):
            try:
                df = ak.stock_zh_a_daily(
                    symbol=self._to_sina_code(code), adjust=self.adjust
                )
                if df is None or df.empty:
                    return pd.DataFrame()
                df = df.copy()
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date").sort_index()
                # 只保留计算均线所需的尾部(留足余量),减少写库量
                df = df.tail(max(days, 60))
                return df[["open", "high", "low", "close", "volume"]]
            except Exception as e:
                logger.debug("新浪获取 %s 失败(第 %d 次): %s", code, attempt, str(e)[:80])
                if attempt < self.max_retries:
                    self._sleep()
        return pd.DataFrame()

    def _fetch_hist_eastmoney(self, code: str, days: int) -> pd.DataFrame:
        """东方财富源(akshare)"""
        start_date = (pd.Timestamp.now() - pd.Timedelta(days=days * 2)).strftime("%Y%m%d")
        end_date = pd.Timestamp.now().strftime("%Y%m%d")
        for attempt in range(1, self.max_retries + 1):
            try:
                df = ak.stock_zh_a_hist(
                    symbol=code,
                    period="daily",
                    start_date=start_date,
                    end_date=end_date,
                    adjust=self.adjust,
                )
                if df is None or df.empty:
                    return pd.DataFrame()
                df = df.rename(
                    columns={
                        "日期": "date",
                        "开盘": "open",
                        "最高": "high",
                        "最低": "low",
                        "收盘": "close",
                        "成交量": "volume",
                    }
                )
                df["date"] = pd.to_datetime(df["date"])
                df = df.set_index("date").sort_index()
                return df[["open", "high", "low", "close", "volume"]]
            except Exception as e:
                logger.debug("东方财富获取 %s 失败(第 %d 次): %s", code, attempt, str(e)[:80])
                if attempt < self.max_retries:
                    self._sleep()
        return pd.DataFrame()
