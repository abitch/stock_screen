# 股票筛选器 (Stock Screener)

A 股选股工具命令行版:一键筛选出**当日收盘价站上 20 日均线 (MA20)** 的股票,结果存入 SQLite,可导出 CSV/Excel。策略层预留扩展接口,后续可挂自定义选股策略。

## 功能

- **交互式菜单**:运行即进入,默认展示自选股(含最新价与 MA20 站上/跌破状态)
- **自选股管理**:搜索股票、收藏到自选、移除
- 全市场 A 股筛选,找出收盘价在 MA20 之上的股票
- 筛选结果持久化到 SQLite(含历史行情缓存、自选股)
- **增量缓存**:行情存入 SQLite,二次运行只补当日新数据,大幅提速
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

### 交互式菜单(推荐)

直接运行,进入交互界面,默认展示自选股(含最新价与 MA20 状态):

```bash
python main.py
```

菜单操作:

| 选项 | 功能 |
|------|------|
| `0` | 刷新自选股(联网拉最新价 + MA20 站上/跌破状态) |
| `1` | 搜索股票并收藏到自选(按代码或名称) |
| `2` | 从自选移除 |
| `3` | 筛选自选股中站上 MA20 的 |
| `4` | 筛选全市场站上 MA20 的(约5500只,较慢) |
| `5` | 查看历史筛选记录 |
| `q` | 退出 |

### 一次性命令(脚本/自动化)

```bash
# 全市场筛选当日股价站上 MA20 的股票
python main.py screen

# 指定均线周期
python main.py screen --ma 20

# 只筛前 50 只(快速测试)
python main.py screen --limit 50

# 筛完导出 CSV(或 .xlsx)
python main.py screen --export result.csv

# 强制重新联网拉取,忽略本地缓存
python main.py screen --refresh

# 查看历史筛选记录
python main.py history

# 查看某次筛选的详细结果
python main.py show <run_id>
```

## 项目结构

```
stock-screener/
├── main.py                      # 入口(无参进入交互菜单,带子命令为一次性操作)
├── config.yaml                  # 配置(均线周期、限流、数据库路径、数据源)
├── requirements.txt
├── app/
│   ├── config.py                # 配置加载
│   ├── cli/interactive.py       # 交互式菜单界面
│   ├── data/fetcher.py          # 行情获取(新浪/baostock/东方财富,限流/重试)
│   ├── data/baostock_fetcher.py # baostock 数据源
│   ├── db/models.py             # SQLite 表结构(行情缓存/筛选记录/自选股)
│   ├── db/repository.py         # 数据库读写
│   ├── screener/engine.py       # 并发筛选引擎(增量缓存)
│   └── strategy/
│       ├── base.py              # 策略抽象基类(扩展点)
│       └── ma_above.py          # MA20 内置策略
└── tests/test_screener.py       # 筛选逻辑单测(不依赖网络)
```

## 数据源说明 ⚠️

本工具支持多个免费数据源,通过 `config.yaml` 的 `data.hist_source` 切换:

| 取值 | 说明 |
|------|------|
| `sina`(默认) | 新浪,**支持高并发,最快**(全市场约比 baostock 快数倍),推荐 |
| `baostock` | baostock 自有服务器,稳定但单连接串行,较慢 |
| `eastmoney` | 东方财富(akshare),数据实时,但部分网络会被拦截 |
| `auto` | 新浪优先,失败自动回退 baostock |

东方财富的 `push2`/`push2his` 接口在部分公司网络/代理环境下会被拦截或限流(表现为 `SSL: UNEXPECTED_EOF_WHILE_READING` 或 `RemoteDisconnected`)。新浪和 baostock 不受此影响。

> ⚠️ **换数据源后请用 `--refresh` 重建缓存**:不同数据源的后复权基准价不同(绝对价格不一样,但 MA 站上/跌破的判断一致)。同一只股票的历史必须来自同一个源,否则缓存里混入不同基准的价格会导致均线计算错误。

> 股票列表始终通过 akshare 的 `stock_info_a_code_name` 等轻量接口获取(带多接口自动回退)。

## 增量缓存

历史行情会缓存到 SQLite 的 `daily_price` 表。每次筛选时:

1. 用参考股票(平安银行)探测最新交易日
2. 逐只比对本地缓存:已缓存到最新交易日且数据点充足的股票,直接读库,**不联网**
3. 缺数据或缓存过期的股票才联网拉取,并更新缓存

因此**首次全市场较慢,之后每天只补新数据,大幅提速**。用 `--refresh` 可强制忽略缓存全部重拉。

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
