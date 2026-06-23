"""股票筛选器 - 命令行入口

用法:
    python main.py screen                  # 全市场筛选当日股价在 MA20 之上的股票
    python main.py screen --ma 20          # 指定均线周期
    python main.py screen --limit 50       # 只筛前 50 只(快速测试)
    python main.py screen --export out.csv # 筛完导出 CSV
    python main.py history                 # 查看历史筛选记录
    python main.py show <run_id>           # 查看某次筛选的详细结果
"""

import argparse
import logging
import sys
from pathlib import Path

# Windows 控制台默认可能是 GBK,中文/特殊字符会报 UnicodeEncodeError,强制 UTF-8
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

import pandas as pd
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn

from app.config import load_config
from app.data.fetcher import AkShareFetcher
from app.db.repository import Repository
from app.screener.engine import Screener
from app.strategy.ma_above import MAAboveStrategy

console = Console()


def setup_logging():
    """统一 UTF-8 日志,写文件,避免控制台编码问题。"""
    log_dir = Path("./logs")
    log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.FileHandler(log_dir / "app.log", encoding="utf-8"),
        ],
    )


def cmd_screen(args, config):
    """执行筛选"""
    ma_period = args.ma or config["screen"]["ma_period"]
    max_workers = config["data"]["rate_limit"]["max_workers"]
    db_path = config["database"]["path"]

    fetcher = AkShareFetcher(config)
    strategy = MAAboveStrategy(ma_period=ma_period)

    console.print(
        f"[bold cyan]开始筛选[/] 当日收盘价站上 MA{ma_period} 的股票"
        + (f" (限定前 {args.limit} 只)" if args.limit else " (全市场)")
    )

    with Repository(db_path) as repo:
        screener = Screener(fetcher, repo=repo, max_workers=max_workers)

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("筛选中", total=None)

            def on_progress(done, total):
                progress.update(task, completed=done, total=total)

            results = screener.run(strategy, limit=args.limit, progress_callback=on_progress)

    _print_results(results, ma_period)

    if args.export and results:
        _export(results, args.export, config)


def _print_results(results, ma_period):
    """打印筛选结果表格"""
    if not results:
        console.print("[yellow]未找到符合条件的股票[/]")
        return

    table = Table(title=f"站上 MA{ma_period} 的股票 (共 {len(results)} 只)")
    table.add_column("代码", style="cyan")
    table.add_column("名称")
    table.add_column("收盘价", justify="right")
    table.add_column(f"MA{ma_period}", justify="right")
    table.add_column("距均线%", justify="right", style="green")
    table.add_column("涨跌幅%", justify="right")
    table.add_column("日期")

    for r in results[:50]:  # 终端最多展示前 50 行
        change_style = "red" if r["change_pct"] >= 0 else "green"
        table.add_row(
            r["code"],
            r["name"],
            f"{r['close']:.2f}",
            f"{r['ma_value']:.2f}",
            f"+{r['distance_pct']:.2f}",
            f"[{change_style}]{r['change_pct']:+.2f}[/]",
            r["trade_date"],
        )

    console.print(table)
    if len(results) > 50:
        console.print(f"[dim](仅展示前 50 行,完整结果见数据库或用 --export 导出)[/]")


def _export(results, path, config):
    """导出结果到 CSV/Excel"""
    export_dir = Path(config["output"]["export_dir"])
    export_dir.mkdir(parents=True, exist_ok=True)

    out_path = Path(path)
    if not out_path.is_absolute():
        out_path = export_dir / out_path

    df = pd.DataFrame(results)
    if out_path.suffix.lower() in (".xlsx", ".xls"):
        df.to_excel(out_path, index=False)
    else:
        df.to_csv(out_path, index=False, encoding="utf-8-sig")

    console.print(f"[green]✓[/] 已导出 {len(results)} 条结果到 {out_path}")


def cmd_history(args, config):
    """查看历史筛选记录"""
    with Repository(config["database"]["path"]) as repo:
        runs = repo.list_runs(limit=args.limit or 20)

    if not runs:
        console.print("[yellow]暂无筛选记录[/]")
        return

    table = Table(title="历史筛选记录")
    table.add_column("ID", style="cyan", justify="right")
    table.add_column("时间")
    table.add_column("策略")
    table.add_column("均线", justify="right")
    table.add_column("检查数", justify="right")
    table.add_column("命中数", justify="right", style="green")

    for r in runs:
        table.add_row(
            str(r["id"]),
            r["run_at"],
            r["strategy"],
            str(r["ma_period"]),
            str(r["total"]),
            str(r["matched"]),
        )
    console.print(table)


def cmd_show(args, config):
    """查看某次筛选的详细结果"""
    with Repository(config["database"]["path"]) as repo:
        results = repo.get_results(args.run_id)

    if not results:
        console.print(f"[yellow]run_id={args.run_id} 没有结果[/]")
        return
    _print_results(results, "")


def main():
    parser = argparse.ArgumentParser(description="A股均线筛选器")
    parser.add_argument("--config", help="配置文件路径")
    sub = parser.add_subparsers(dest="command", required=True)

    p_screen = sub.add_parser("screen", help="筛选股价在均线之上的股票")
    p_screen.add_argument("--ma", type=int, help="均线周期(默认 20)")
    p_screen.add_argument("--limit", type=int, help="只筛前 N 只(快速测试)")
    p_screen.add_argument("--export", help="导出结果文件名(.csv 或 .xlsx)")

    p_history = sub.add_parser("history", help="查看历史筛选记录")
    p_history.add_argument("--limit", type=int, help="显示条数(默认 20)")

    p_show = sub.add_parser("show", help="查看某次筛选详情")
    p_show.add_argument("run_id", type=int, help="筛选运行 ID")

    args = parser.parse_args()

    setup_logging()
    config = load_config(args.config)

    if args.command == "screen":
        cmd_screen(args, config)
    elif args.command == "history":
        cmd_history(args, config)
    elif args.command == "show":
        cmd_show(args, config)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]已中断[/]")
        sys.exit(1)
