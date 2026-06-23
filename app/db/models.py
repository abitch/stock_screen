"""SQLite 数据库表结构定义与初始化"""

import sqlite3
from pathlib import Path

SCHEMA = """
-- 股票基础信息
CREATE TABLE IF NOT EXISTS stocks (
    code        TEXT PRIMARY KEY,
    name        TEXT,
    updated_at  TEXT
);

-- 历史日线行情(缓存,避免重复拉取)
CREATE TABLE IF NOT EXISTS daily_price (
    code    TEXT,
    date    TEXT,
    open    REAL,
    high    REAL,
    low     REAL,
    close   REAL,
    volume  REAL,
    PRIMARY KEY (code, date)
);

-- 自选股收藏
CREATE TABLE IF NOT EXISTS watchlist (
    code      TEXT PRIMARY KEY,
    name      TEXT,
    added_at  TEXT
);

-- 每次筛选的运行记录
CREATE TABLE IF NOT EXISTS screen_run (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at      TEXT,
    ma_period   INTEGER,
    strategy    TEXT,
    total       INTEGER,    -- 检查的股票总数
    matched     INTEGER     -- 命中数
);

-- 某次筛选命中的股票明细
CREATE TABLE IF NOT EXISTS screen_result (
    run_id          INTEGER,
    code            TEXT,
    name            TEXT,
    close           REAL,
    ma_value        REAL,
    distance_pct    REAL,   -- 距均线百分比
    change_pct      REAL,   -- 当日涨跌幅
    trade_date      TEXT,
    PRIMARY KEY (run_id, code),
    FOREIGN KEY (run_id) REFERENCES screen_run(id)
);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    """获取数据库连接,自动创建目录并初始化表结构。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.commit()
    return conn
