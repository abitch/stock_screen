"""baostock 数据获取封装(备用数据源)

baostock 使用自有服务器,不依赖东方财富接口,在东方财富被限流/拦截时作为回退。
注意:
- 代码需带市场前缀,如 sz.000001 / sh.600000
- 需要登录会话(全局 login 一次即可)
- adjustflag: 1=后复权 2=前复权 3=不复权
- 并发过高时偶发解码异常,内部带重试吸收
"""

import logging
import threading
import time

import pandas as pd

logger = logging.getLogger(__name__)


def _to_bs_code(code: str) -> str:
    """纯数字代码转 baostock 带前缀格式。

    沪市: 60/68/9 开头 -> sh
    深市/创业板: 00/30/2 开头 -> sz
    北交所: 8/4 开头 -> bj
    """
    code = code.strip()
    if code.startswith(("60", "68", "9")):
        return f"sh.{code}"
    if code.startswith(("4", "8")):
        return f"bj.{code}"
    return f"sz.{code}"


class BaostockFetcher:
    """baostock 数据获取器"""

    _login_lock = threading.Lock()
    _query_lock = threading.Lock()   # baostock 单一全局连接,查询需串行,否则并发会串流
    _logged_in = False

    def __init__(self, config: dict):
        data_cfg = config.get("data", {})
        rate_cfg = data_cfg.get("rate_limit", {})
        self.hist_days = data_cfg.get("hist_days", 60)
        self.max_retries = rate_cfg.get("max_retries", 3)
        # baostock adjustflag: 1后复权 2前复权 3不复权
        adjust_map = {"hfq": "1", "qfq": "2", "": "3"}
        self.adjustflag = adjust_map.get(data_cfg.get("adjust", "hfq"), "1")
        self._ensure_login()

    @classmethod
    def _ensure_login(cls):
        """全局登录一次(线程安全)"""
        with cls._login_lock:
            if cls._logged_in:
                return
            import baostock as bs
            lg = bs.login()
            if lg.error_code != "0":
                raise ConnectionError(f"baostock 登录失败: {lg.error_msg}")
            cls._logged_in = True
            logger.info("baostock 登录成功")

    @classmethod
    def logout(cls):
        """登出 baostock 会话(程序结束时调用,释放连接)"""
        with cls._login_lock:
            if not cls._logged_in:
                return
            try:
                import baostock as bs
                bs.logout()
            except Exception:
                pass
            cls._logged_in = False

    def get_stock_hist(self, code: str, days: int = None) -> pd.DataFrame:
        """获取单只股票历史日线。

        Returns:
            DataFrame,索引为日期,含 open/high/low/close/volume
        """
        days = days or self.hist_days
        start = (pd.Timestamp.now() - pd.Timedelta(days=days * 2)).strftime("%Y-%m-%d")
        end = pd.Timestamp.now().strftime("%Y-%m-%d")
        bs_code = _to_bs_code(code)

        # 并发高时偶发解码异常,重试吸收
        for attempt in range(1, self.max_retries + 1):
            try:
                with self._query_lock:   # 串行化查询,避免并发串流
                    return self._query(bs_code, start, end)
            except Exception as e:
                logger.debug("baostock 取 %s 失败(第 %d 次): %s", bs_code, attempt, str(e)[:80])
                if attempt < self.max_retries:
                    time.sleep(0.3 * attempt)
        return pd.DataFrame()

    def _query(self, bs_code: str, start: str, end: str) -> pd.DataFrame:
        """单次查询(可能抛异常,由调用方重试)"""
        import baostock as bs

        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume",
            start_date=start,
            end_date=end,
            frequency="d",
            adjustflag=self.adjustflag,
        )
        if rs.error_code != "0":
            raise RuntimeError(rs.error_msg)

        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        # baostock 返回字符串,转数值;空串转 NaN 再丢弃
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"])
        return df[["open", "high", "low", "close", "volume"]]
