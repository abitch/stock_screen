"""数据库读写封装"""

from datetime import datetime
from typing import List, Dict, Optional

import pandas as pd

from .models import get_connection


class Repository:
    """SQLite 读写封装"""

    def __init__(self, db_path: str):
        self.conn = get_connection(db_path)

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ---------- 股票列表 ----------

    def upsert_stocks(self, stocks: pd.DataFrame):
        """写入/更新股票基础信息。stocks 含 code, name 列。"""
        now = datetime.now().isoformat(timespec="seconds")
        rows = [(r["code"], r["name"], now) for _, r in stocks.iterrows()]
        self.conn.executemany(
            "INSERT INTO stocks(code, name, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at",
            rows,
        )
        self.conn.commit()

    def get_all_stocks(self) -> pd.DataFrame:
        """从库读取全部股票基础信息。

        Returns:
            DataFrame,含 code, name 两列(库为空时返回空表)
        """
        cur = self.conn.execute("SELECT code, name FROM stocks ORDER BY code ASC")
        rows = cur.fetchall()
        if not rows:
            return pd.DataFrame(columns=["code", "name"])
        return pd.DataFrame([dict(r) for r in rows])

    def stocks_age_days(self) -> Optional[float]:
        """返回股票列表缓存的年龄(天)。库为空返回 None。"""
        cur = self.conn.execute("SELECT MAX(updated_at) AS latest FROM stocks")
        row = cur.fetchone()
        if not row or not row["latest"]:
            return None
        try:
            updated = datetime.fromisoformat(row["latest"])
        except ValueError:
            return None
        return (datetime.now() - updated).total_seconds() / 86400.0

    # ---------- 历史行情缓存 ----------

    def save_daily_price(self, code: str, hist: pd.DataFrame):
        """缓存单只股票历史行情。hist 索引为日期,含 OHLCV。"""
        if hist is None or hist.empty:
            return
        rows = [
            (
                code,
                idx.strftime("%Y-%m-%d"),
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row["volume"]),
            )
            for idx, row in hist.iterrows()
        ]
        self.conn.executemany(
            "INSERT INTO daily_price(code, date, open, high, low, close, volume) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(code, date) DO UPDATE SET "
            "open=excluded.open, high=excluded.high, low=excluded.low, "
            "close=excluded.close, volume=excluded.volume",
            rows,
        )
        self.conn.commit()

    # ---------- 增量缓存读取 ----------

    def get_cache_status(self) -> dict:
        """返回每只股票的缓存状态: {code: (最新日期, 行数)}。"""
        cur = self.conn.execute(
            "SELECT code, MAX(date) AS latest, COUNT(*) AS cnt "
            "FROM daily_price GROUP BY code"
        )
        return {row["code"]: (row["latest"], row["cnt"]) for row in cur.fetchall()}

    def load_daily_price(self, code: str) -> pd.DataFrame:
        """从库读取单只股票的历史行情。

        Returns:
            DataFrame,索引为日期,含 open/high/low/close/volume(按日期升序)
        """
        cur = self.conn.execute(
            "SELECT date, open, high, low, close, volume FROM daily_price "
            "WHERE code=? ORDER BY date ASC",
            (code,),
        )
        rows = cur.fetchall()
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([dict(r) for r in rows])
        df["date"] = pd.to_datetime(df["date"])
        return df.set_index("date")[["open", "high", "low", "close", "volume"]]

    # ---------- 筛选结果 ----------

    def create_run(self, ma_period: int, strategy: str, total: int, matched: int) -> int:
        """创建一次筛选运行记录,返回 run_id。"""
        cur = self.conn.execute(
            "INSERT INTO screen_run(run_at, ma_period, strategy, total, matched) "
            "VALUES(?,?,?,?,?)",
            (
                datetime.now().isoformat(timespec="seconds"),
                ma_period,
                strategy,
                total,
                matched,
            ),
        )
        self.conn.commit()
        return cur.lastrowid

    def save_results(self, run_id: int, results: List[Dict]):
        """保存某次筛选命中的股票明细。"""
        rows = [
            (
                run_id,
                r["code"],
                r["name"],
                r["close"],
                r["ma_value"],
                r["distance_pct"],
                r["change_pct"],
                r["trade_date"],
            )
            for r in results
        ]
        self.conn.executemany(
            "INSERT INTO screen_result(run_id, code, name, close, ma_value, "
            "distance_pct, change_pct, trade_date) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(run_id, code) DO NOTHING",
            rows,
        )
        self.conn.commit()

    def list_runs(self, limit: int = 20) -> List[Dict]:
        """列出最近的筛选运行记录。"""
        cur = self.conn.execute(
            "SELECT id, run_at, ma_period, strategy, total, matched "
            "FROM screen_run ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]

    def get_results(self, run_id: int) -> List[Dict]:
        """获取某次运行的命中明细。"""
        cur = self.conn.execute(
            "SELECT code, name, close, ma_value, distance_pct, change_pct, trade_date "
            "FROM screen_result WHERE run_id=? ORDER BY distance_pct DESC",
            (run_id,),
        )
        return [dict(row) for row in cur.fetchall()]

    # ---------- 自选股 ----------

    def add_to_watchlist(self, code: str, name: str) -> bool:
        """加入自选股。已存在返回 False,新增返回 True。"""
        cur = self.conn.execute(
            "INSERT INTO watchlist(code, name, added_at) VALUES(?,?,?) "
            "ON CONFLICT(code) DO NOTHING",
            (code, name, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def remove_from_watchlist(self, code: str) -> bool:
        """从自选股移除。移除成功返回 True。"""
        cur = self.conn.execute("DELETE FROM watchlist WHERE code=?", (code,))
        self.conn.commit()
        return cur.rowcount > 0

    def get_watchlist(self) -> List[Dict]:
        """获取自选股列表(按加入时间)。"""
        cur = self.conn.execute(
            "SELECT code, name, added_at FROM watchlist ORDER BY added_at ASC"
        )
        return [dict(row) for row in cur.fetchall()]

    def is_in_watchlist(self, code: str) -> bool:
        cur = self.conn.execute("SELECT 1 FROM watchlist WHERE code=?", (code,))
        return cur.fetchone() is not None

