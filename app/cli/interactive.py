"""交互式菜单界面

进入后默认展示自选股(含实时价与 MA20 状态),提供搜索/收藏/筛选等菜单操作。
"""

import logging

from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn

from app.data.fetcher import AkShareFetcher
from app.db.repository import Repository
from app.screener.engine import Screener
from app.strategy.ma_above import MAAboveStrategy

logger = logging.getLogger(__name__)
console = Console()


class InteractiveApp:
    """交互式应用主循环"""

    def __init__(self, config: dict):
        self.config = config
        self.ma_period = config["screen"]["ma_period"]
        self.max_workers = config["data"]["rate_limit"]["max_workers"]
        self.fetcher = AkShareFetcher(config)
        self.repo = Repository(config["database"]["path"])

    def close(self):
        self.repo.close()

    # ---------- 主循环 ----------

    def run(self):
        console.print("[bold cyan]A股均线筛选器[/]  输入数字选择操作\n")
        # 进入时默认展示自选股
        self.show_watchlist()
        while True:
            self._print_menu()
            choice = Prompt.ask("请选择", default="0").strip()
            if choice == "0":
                self.show_watchlist()
            elif choice == "1":
                self.search_and_add()
            elif choice == "2":
                self.remove_from_watchlist()
            elif choice == "3":
                self.screen_watchlist()
            elif choice == "4":
                self.screen_market()
            elif choice == "5":
                self.show_history()
            elif choice in ("q", "Q", "quit", "exit"):
                console.print("[dim]再见[/]")
                break
            else:
                console.print("[yellow]无效选择[/]")

    def _print_menu(self):
        console.print(
            "\n[bold]菜单:[/] "
            "[cyan]0[/]刷新自选  "
            "[cyan]1[/]搜索并收藏  "
            "[cyan]2[/]移除自选  "
            "[cyan]3[/]筛选自选股MA20  "
            "[cyan]4[/]筛选全市场MA20  "
            "[cyan]5[/]历史记录  "
            "[cyan]q[/]退出"
        )

    # ---------- 自选股展示 ----------

    def show_watchlist(self):
        """展示自选股,联网拉取实时价与 MA20 状态"""
        watch = self.repo.get_watchlist()
        if not watch:
            console.print("[yellow]自选股为空,用菜单 1 搜索并收藏股票[/]")
            return

        console.print(f"\n[bold]我的自选股[/] (共 {len(watch)} 只,正在获取最新价...)")
        strategy = MAAboveStrategy(self.ma_period)
        rows = []
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            console=console,
            redirect_stdout=False,
            redirect_stderr=False,
            transient=True,
        ) as progress:
            task = progress.add_task("获取中", total=len(watch))
            for item in watch:
                hist = self.fetcher.get_stock_hist(item["code"])
                result = strategy.evaluate(hist) if hist is not None and not hist.empty else None
                rows.append((item, result))
                progress.advance(task)

        table = Table(title=f"我的自选股 (MA{self.ma_period})")
        table.add_column("代码", style="cyan")
        table.add_column("名称")
        table.add_column("最新价", justify="right")
        table.add_column(f"MA{self.ma_period}", justify="right")
        table.add_column("距均线%", justify="right")
        table.add_column("状态", justify="center")
        table.add_column("日期")

        for item, result in rows:
            if result is None:
                table.add_row(item["code"], item["name"], "-", "-", "-",
                              "[dim]无数据[/]", "-")
                continue
            status = "[red]站上[/]" if result.matched else "[green]跌破[/]"
            dist_style = "red" if result.distance_pct >= 0 else "green"
            table.add_row(
                item["code"], item["name"],
                f"{result.close:.2f}", f"{result.ref_value:.2f}",
                f"[{dist_style}]{result.distance_pct:+.2f}[/]",
                status, result.trade_date,
            )
        console.print(table)

    # ---------- 搜索并收藏 ----------

    def search_and_add(self):
        keyword = Prompt.ask("输入股票代码或名称关键词").strip()
        if not keyword:
            return
        results = self.fetcher.search(keyword, limit=20)
        if results.empty:
            console.print("[yellow]没有匹配的股票[/]")
            return

        table = Table(title=f"搜索结果: {keyword}")
        table.add_column("序号", style="cyan", justify="right")
        table.add_column("代码")
        table.add_column("名称")
        table.add_column("已收藏", justify="center")
        codes = []
        for i, row in results.iterrows():
            in_wl = self.repo.is_in_watchlist(row["code"])
            table.add_row(str(i + 1), row["code"], row["name"],
                          "✓" if in_wl else "")
            codes.append((row["code"], row["name"]))
        console.print(table)

        sel = Prompt.ask("输入序号收藏(多个用逗号分隔,回车取消)", default="").strip()
        if not sel:
            return
        added = 0
        for part in sel.split(","):
            part = part.strip()
            if not part.isdigit():
                continue
            idx = int(part) - 1
            if 0 <= idx < len(codes):
                code, name = codes[idx]
                if self.repo.add_to_watchlist(code, name):
                    added += 1
        console.print(f"[green]已收藏 {added} 只[/]")

    # ---------- 移除自选 ----------

    def remove_from_watchlist(self):
        watch = self.repo.get_watchlist()
        if not watch:
            console.print("[yellow]自选股为空[/]")
            return
        table = Table(title="自选股")
        table.add_column("序号", style="cyan", justify="right")
        table.add_column("代码")
        table.add_column("名称")
        for i, item in enumerate(watch):
            table.add_row(str(i + 1), item["code"], item["name"])
        console.print(table)

        sel = Prompt.ask("输入序号移除(多个用逗号分隔,回车取消)", default="").strip()
        if not sel:
            return
        removed = 0
        for part in sel.split(","):
            part = part.strip()
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(watch):
                    if self.repo.remove_from_watchlist(watch[idx]["code"]):
                        removed += 1
        console.print(f"[green]已移除 {removed} 只[/]")

    # ---------- 筛选自选股 ----------

    def screen_watchlist(self):
        watch = self.repo.get_watchlist()
        if not watch:
            console.print("[yellow]自选股为空,无法筛选[/]")
            return
        codes = [(w["code"], w["name"]) for w in watch]
        self._run_screen(codes, scope_desc="自选股")

    # ---------- 筛选全市场 ----------

    def screen_market(self):
        confirm = Prompt.ask(
            "全市场筛选约5500只,较耗时,确认?", choices=["y", "n"], default="n"
        )
        if confirm != "y":
            return
        self._run_screen(None, scope_desc="全市场")

    def _run_screen(self, codes, scope_desc: str):
        """执行筛选。codes 为 None 表示全市场;否则为 [(code,name),...]"""
        strategy = MAAboveStrategy(self.ma_period)
        screener = Screener(self.fetcher, repo=self.repo, max_workers=self.max_workers)

        console.print(f"[cyan]开始筛选[/] {scope_desc} 站上 MA{self.ma_period} 的股票")
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
            redirect_stdout=False,
            redirect_stderr=False,
        ) as progress:
            task = progress.add_task("筛选中", total=None)

            def on_progress(done, total):
                progress.update(task, completed=done, total=total)

            results = screener.run(
                strategy, codes=codes, progress_callback=on_progress
            )

        self._print_results(results)
        # 询问是否把命中结果加入自选
        if results:
            sel = Prompt.ask("把命中股票加入自选?(y/n)", choices=["y", "n"], default="n")
            if sel == "y":
                added = sum(
                    1 for r in results
                    if self.repo.add_to_watchlist(r["code"], r["name"])
                )
                console.print(f"[green]已收藏 {added} 只[/]")

    def _print_results(self, results):
        if not results:
            console.print("[yellow]未找到符合条件的股票[/]")
            return
        table = Table(title=f"站上 MA{self.ma_period} 的股票 (共 {len(results)} 只)")
        table.add_column("代码", style="cyan")
        table.add_column("名称")
        table.add_column("收盘价", justify="right")
        table.add_column(f"MA{self.ma_period}", justify="right")
        table.add_column("距均线%", justify="right", style="green")
        table.add_column("涨跌幅%", justify="right")
        table.add_column("日期")
        for r in results[:50]:
            change_style = "red" if r["change_pct"] >= 0 else "green"
            table.add_row(
                r["code"], r["name"], f"{r['close']:.2f}", f"{r['ma_value']:.2f}",
                f"+{r['distance_pct']:.2f}",
                f"[{change_style}]{r['change_pct']:+.2f}[/]", r["trade_date"],
            )
        console.print(table)
        if len(results) > 50:
            console.print("[dim](仅展示前 50 行,完整结果可用命令行 screen --export 导出)[/]")

    # ---------- 历史记录 ----------

    def show_history(self):
        runs = self.repo.list_runs(limit=20)
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
            table.add_row(str(r["id"]), r["run_at"], r["strategy"],
                          str(r["ma_period"]), str(r["total"]), str(r["matched"]))
        console.print(table)
