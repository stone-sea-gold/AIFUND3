"""
回测引擎核心 — 资金管理模式

每日循环：止损检查 → 卖出到期 → 策略选股 → 买入 → 记录NAV

用法:
    from 选股.backtest.engine import BacktestEngine
    engine = BacktestEngine(strategy_name="b1", pool_name="沪深300", initial_capital=100000)
    result = engine.run(start_date="2026-01-05", end_date="2026-06-01")
"""
import logging
import time
import threading
from typing import Any

logger = logging.getLogger("backtest")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[%(name)s] %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _check_cancelled(cancelled: threading.Event | None):
    """检查取消标志，已取消则抛出 BacktestCancelled 异常"""
    if cancelled and cancelled.is_set():
        from server.backtest_manager import BacktestCancelled
        raise BacktestCancelled()


class BacktestEngine:
    def __init__(
        self,
        strategy_name: str = "b1",
        pool_name: str = "沪深300",
        top_n: int = 10,
        min_score: int = 25,
        holding_days: int = 3,
        initial_capital: float = 100000.0,
        data_count: int = 500,
    ):
        self.strategy_name = strategy_name
        self.pool_name = pool_name
        self.top_n = top_n
        self.min_score = min_score
        self.holding_days = holding_days
        self.initial_capital = initial_capital
        self.data_count = data_count
        self._provider = None
        self._runner = None
        self._data_loaded = False

    def _ensure_data(self, start_date: str | None = None, cancelled: threading.Event | None = None):
        if not self._data_loaded:
            from 选股.backtest.data_provider import DataProvider
            from 选股.backtest.strategy_runner import BacktestStrategyRunner
            count = self.data_count
            if start_date:
                from datetime import datetime, date
                try:
                    sd = datetime.strptime(start_date, "%Y-%m-%d").date()
                    today = date.today()
                    days_span = (today - sd).days
                    trading_days = int(days_span * 250 / 365)
                    needed = trading_days + 300
                    if needed > count:
                        count = needed
                except ValueError:
                    pass
            logger.info(f"加载数据: pool={self.pool_name}, count={count}")
            try:
                self._provider = DataProvider.from_pool(self.pool_name, count=count, cancelled=cancelled)
            except Exception as e:
                logger.error(f"数据加载失败: {e}")
                raise
            codes = self._provider.get_codes()
            if not codes:
                logger.warning(f"股票池 {self.pool_name} 无可用数据")
            else:
                first_code = codes[0]
                first_klines = self._provider.get_stock_info(first_code)["klines"]
                logger.info(f"数据加载完成: {len(codes)}只股票, 首只K线数={len(first_klines)}, 首日={first_klines[0]['date'] if first_klines else 'N/A'}, 末日={first_klines[-1]['date'] if first_klines else 'N/A'}")
            self._runner = BacktestStrategyRunner(self.strategy_name, self._provider)
            self._data_loaded = True

    def run(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        skip_first: int = 250,
        verbose: bool = True,
        cancelled: threading.Event | None = None,
        progress_callback=None,
    ) -> dict[str, Any]:
        """执行资金管理回测。"""
        def _progress(phase, current, total, info=""):
            if progress_callback:
                progress_callback(phase, current, total, info)

        _progress("加载数据", 0, 0, self.pool_name)
        t0 = time.time()
        self._ensure_data(start_date, cancelled)

        try:
            calendar = self._provider.get_calendar()
            effective_skip = 0 if start_date else skip_first
            all_dates = calendar.get_dates(skip_first=effective_skip)
            logger.info(f"日历原始日期数: {len(all_dates)}, 首日: {all_dates[0] if all_dates else 'N/A'}, 末日: {all_dates[-1] if all_dates else 'N/A'}")
            logger.info(f"skip_first={effective_skip}, start_date={start_date}, end_date={end_date}")

            if start_date:
                all_dates = [d for d in all_dates if d >= start_date]
            if end_date:
                all_dates = [d for d in all_dates if d <= end_date]

            logger.info(f"过滤后日期数: {len(all_dates)}, 首日: {all_dates[0] if all_dates else 'N/A'}, 末日: {all_dates[-1] if all_dates else 'N/A'}")
        except Exception as e:
            logger.error(f"日历构建异常: {e}")
            import traceback; traceback.print_exc()
            return self._empty_result(time.time() - t0)

        if not all_dates:
            return self._empty_result(time.time() - t0)

        total = len(all_dates)
        if verbose:
            logger.info(f"开始回测: {self.strategy_name} @ {self.pool_name}")
            logger.info(f"  日期范围: {all_dates[0]} ~ {all_dates[-1]} ({total} 个交易日)")
            logger.info(f"  top_n={self.top_n}, min_score={self.min_score}, hold={self.holding_days}天")
            logger.info(f"  本金={self.initial_capital:,.0f}")

        # ── 预构建每日行情数据（O(总K线数)，一次性遍历）──
        _progress("构建行情", 0, 0, f"{len(self._provider.get_codes())}只股票")
        daily_data = self._provider.build_daily_lookup()

        # ── 预计算指标（每只股票只算一次）──
        _progress("预计算指标", 0, 0, f"{self.strategy_name}")
        date_index_map = self._provider.build_date_index_map()
        self._runner.precompute(date_index_map, verbose=False, cancelled=cancelled)

        # ── 每日选股（只做切片+打分，不重算指标）──
        selection_by_date = {}
        for i, date in enumerate(all_dates):
            _check_cancelled(cancelled)

            _progress("选股扫描", i + 1, total, date)

            stocks = self._runner.run(
                as_of_date=date,
                top_n=self.top_n,
                min_score=self.min_score,
            )

            if stocks and i + 1 < len(all_dates):
                buy_date = all_dates[i + 1]
                selections = []
                for s in stocks:
                    # 用 T+1 开盘价作为实际买入价
                    buy_bar = daily_data.get(buy_date, {}).get(s["code"])
                    buy_price = buy_bar["open"] if buy_bar else s["latest_info"]["close"]
                    selections.append({
                        "code": s["code"],
                        "name": s["name"],
                        "score": s["score"],
                        "price": s["latest_info"]["close"],  # 参考价
                        "buy_price": buy_price,  # 实际买入价
                    })
                selection_by_date[date] = selections

            if verbose and (i + 1) % 20 == 0:
                n_sel = len(selection_by_date.get(date, []))
                logger.info(f"  [{i+1}/{total}] {date} → {n_sel} 只合格")

        # ── 执行资金管理模拟 ──
        from 选股.backtest.portfolio import PortfolioSimulator
        portfolio = PortfolioSimulator(
            initial_capital=self.initial_capital,
            top_n=self.top_n,
            holding_days=self.holding_days,
        )
        portfolio.run(all_dates, daily_data, selection_by_date)

        elapsed = time.time() - t0
        m = portfolio.metrics

        result = {
            "strategy_name": self.strategy_name,
            "pool_name": self.pool_name,
            "date_range": {"start": all_dates[0], "end": all_dates[-1]},
            "total_rounds": len(selection_by_date),
            "total_trades": m.get("total_trades", 0),
            "metrics": m,
            "trades": portfolio.closed_trades,
            "nav_history": portfolio.nav_history,
            "config": self._get_config(),
            "elapsed": round(elapsed, 1),
        }

        if verbose:
            logger.info(f"回测完成 ({elapsed:.0f}s)")
            logger.info(f"  交易日: {total} | 选股轮次: {len(selection_by_date)} | 交易: {m.get('total_trades', 0)} 笔")
            logger.info(f"  本金: {m.get('initial_capital', 0):,.0f} → 最终: {m.get('final_nav', 0):,.0f}")
            logger.info(f"  总收益: {m.get('total_return_pct', 0):+.2f}% | 夏普: {m.get('nav_sharpe', 0):.2f}")
            logger.info(f"  最大回撤: {m.get('nav_max_drawdown', 0):.2f}% | 止损: {m.get('stop_loss_count', 0)} 次")

        return result

    def _empty_result(self, elapsed: float) -> dict:
        return {
            "strategy_name": self.strategy_name,
            "pool_name": self.pool_name,
            "total_rounds": 0,
            "total_trades": 0,
            "metrics": {},
            "trades": [],
            "nav_history": [],
            "config": self._get_config(),
            "elapsed": round(elapsed, 1),
        }

    def _get_config(self) -> dict:
        return {
            "strategy_name": self.strategy_name,
            "pool_name": self.pool_name,
            "top_n": self.top_n,
            "min_score": self.min_score,
            "holding_days": self.holding_days,
            "initial_capital": self.initial_capital,
        }
