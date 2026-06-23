# 股票筛选器 (Stock Screener)

A 股选股工具命令行版:一键筛选出**当日收盘价站上 20 日均线 (MA20)** 的股票,结果存入 SQLite,可导出 CSV/Excel。策略层预留扩展接口,后续可挂自定义选股策略。

## 功能

- 全市场 A 股筛选,找出收盘价在 MA20 之上的股票
- 筛选结果持久化到 SQLite(含历史行情缓存)
- 终端表格输出(基于 rich),支持导出 CSV/Excel
- 策略抽象基类,新增策略只需继承 `Strategy` 并实现 `evaluate()`
- 并发拉取 + 限流重试,降低被数据源封 IP 风险

## 环境要求

- Python 3.10+
- 依赖见 `requirements.txt`

```bash
pip install -r requirements.txt
```

## 用法

```bash
# 全市场筛选当日股价站上 MA20 的股票
python main.py screen

# 指定均线周期
python main.py screen --ma 20

# 只筛前 50 只(快速测试)
python main.py screen --limit 50

# 筛完导出 CSV(或 .xlsx)
python main.py screen --export result.csv

# 查看历史筛选记录
python main.py history

# 查看某次筛选的详细结果
python main.py show <run_id>
```

## 项目结构

```
stock-screener/
├── main.py                      # CLI 入口
├── config.yaml                  # 配置(均线周期、限流、数据库路径)
├── requirements.txt
├── app/
│   ├── config.py                # 配置加载
│   ├── data/fetcher.py          # akshare 数据获取(限流/重试)
│   ├── db/models.py             # SQLite 表结构
│   ├── db/repository.py         # 数据库读写
│   ├── screener/engine.py       # 并发筛选引擎
│   └── strategy/
│       ├── base.py              # 策略抽象基类(扩展点)
│       └── ma_above.py          # MA20 内置策略
└── tests/test_screener.py       # 筛选逻辑单测(不依赖网络)
```

## 数据源说明 ⚠️

本工具支持两个免费数据源,通过 `config.yaml` 的 `data.hist_source` 切换:

| 取值 | 说明 |
|------|------|
| `baostock`(默认) | 走 baostock 自有服务器,**稳定,推荐**,不受东方财富接口拦截影响 |
| `eastmoney` | 只用东方财富(akshare),数据更实时,但部分网络会被拦截 |
| `auto` | 东方财富优先,失败自动回退到 baostock |

东方财富的 `push2`/`push2his` 接口在部分公司网络/代理环境下会被拦截或限流(表现为 `SSL: UNEXPECTED_EOF_WHILE_READING` 或 `RemoteDisconnected`)。默认用 baostock 可避开这个问题。家庭网络通畅时可改用 `eastmoney` 或 `auto` 获取更实时的数据。

> 股票列表始终通过 akshare 的 `stock_info_a_code_name` 等轻量接口获取(带多接口自动回退)。

## 扩展策略

新增一个策略:

```python
from app.strategy.base import Strategy, StrategyResult

class MyStrategy(Strategy):
    name = "MyStrategy"

    @property
    def min_data_points(self) -> int:
        return 60

    def evaluate(self, hist) -> StrategyResult | None:
        # hist: 单只股票历史日线 DataFrame(open/high/low/close/volume)
        ...
        return StrategyResult(matched=True, close=..., ref_value=..., ...)
```

然后在筛选时把策略实例传给 `Screener.run()` 即可。

## 测试

```bash
python -m pytest tests/ -v
```

## 免责声明

本工具仅供学习研究使用,不构成任何投资建议。股市有风险,入市需谨慎。
