"""PortfolioSimulator — 资金管理回测组合

模拟真实资金约束下的每日选股 + 买入/止损/到期卖出循环。
止损逻辑通过 stop_loss 模块解耦，可独立修改。

交易规则：
  1. T日策略选股 → T+1日开盘价买入
  2. 止损判断委托给 stop_loss 策略
  3. 资金约束：卖出释放的资金当日可用于新买入
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any


@dataclass
class Position:
    """单笔持仓"""
    code: str
    name: str
    buy_date: str          # 实际买入日 (T+1)
    buy_price: float       # 买入价 (T+1 开盘价)
    shares: int
    cost: float            # buy_price × shares
    select_date: str       # 选股日 (T)
    score: int = 0
    buy_day_close: float = 0.0   # T+1 收盘价（止损判断用）
    status: str = "open"   # "open" | "stop_loss_pending" | "closed"


class PortfolioSimulator:
    """资金管理回测组合"""

    def __init__(
        self,
        initial_capital: float = 100000.0,
        top_n: int = 5,
        holding_days: int = 3,
        stop_loss_strategy: dict | None = None,
        stop_loss_params: dict | None = None,
    ):
        """
        Args:
            initial_capital: 初始资金
            top_n: 每轮最多买入只数
            holding_days: 默认持有天数
            stop_loss_strategy: 止损策略（来自 stop_loss.get_stop_loss_strategy()）
            stop_loss_params: 止损参数覆盖（会合并到策略默认参数）
        """
        from 选股.backtest.stop_loss import get_stop_loss_strategy
        self.initial_capital = initial_capital
        self.top_n = top_n
        self.holding_days = holding_days

        # 加载止损策略
        if stop_loss_strategy is None:
            stop_loss_strategy = get_stop_loss_strategy("default")
        self._sl_strategy = stop_loss_strategy
        self._sl_params = dict(stop_loss_strategy.get("default_params", {}))
        if stop_loss_params:
            self._sl_params.update(stop_loss_params)
        # 确保 holding_days 同步
        self._sl_params["holding_days"] = holding_days

        self.cash: float = initial_capital
        self.positions: list[Position] = []
        self.closed_trades: list[dict] = []
        self.nav_history: list[dict] = []
        self.metrics: dict[str, Any] = {}

    def run(self, all_dates: list[str], daily_data: dict[str, dict],
            selection_by_date: dict[str, list[dict]]):
        """执行资金管理回测。"""
        self.cash = self.initial_capital
        self.positions = []
        self.closed_trades = []
        self.nav_history = []

        # 预建日期索引
        date_to_idx = {d: i for i, d in enumerate(all_dates)}

        for i, date in enumerate(all_dates):
            today = daily_data.get(date, {})

            # ── 1. 收盘时检查止损（对昨天买入的持仓）──
            check_close = self._sl_strategy["check_close"]
            for pos in self.positions:
                if pos.status != "open":
                    continue
                if i > 0 and pos.buy_date == all_dates[i - 1]:
                    bar = today.get(pos.code)
                    if bar:
                        result = check_close(pos, bar, self._sl_params)
                        if result == "stop_loss_pending":
                            pos.status = "stop_loss_pending"
                            pos.buy_day_close = bar["close"]

            # ── 2. 开盘时执行止损（对 stop_loss_pending 的持仓）──
            check_open = self._sl_strategy["check_open"]
            to_close = []
            for pos in self.positions:
                if pos.status != "stop_loss_pending":
                    continue
                bar = today.get(pos.code)
                if not bar:
                    continue
                action, price = check_open(pos, bar, self._sl_params)
                if action == "stop_loss":
                    to_close.append((pos, price, "stop_loss"))
                elif action == "cancel":
                    pos.status = "open"

            for pos, price, reason in to_close:
                self._close_position(pos, date, price, reason)

            # ── 3. 卖出到期持仓 ──
            should_sell = self._sl_strategy["should_sell"]
            to_expire = []
            for pos in self.positions:
                if pos.status != "open":
                    continue
                buy_idx = date_to_idx.get(pos.buy_date)
                if buy_idx is not None and should_sell(pos, i, buy_idx, self._sl_params):
                    bar = today.get(pos.code)
                    sell_price = bar["close"] if bar else pos.buy_price
                    to_expire.append((pos, sell_price, "take_profit"))

            for pos, price, reason in to_expire:
                self._close_position(pos, date, price, reason)

            # ── 4. 买入 ──
            candidates = selection_by_date.get(date, [])
            self._buy_candidates(date, candidates, all_dates, i)

            # ── 5. 记录 NAV ──
            positions_value = sum(
                today.get(pos.code, {}).get("close", pos.buy_price) * pos.shares
                for pos in self.positions
                if pos.status == "open"
            )
            nav = self.cash + positions_value
            self.nav_history.append({
                "date": date,
                "nav": round(nav, 2),
                "cash": round(self.cash, 2),
                "positions_value": round(positions_value, 2),
                "open_positions": len([p for p in self.positions if p.status == "open"]),
            })

        # 强制清仓
        if all_dates:
            last_date = all_dates[-1]
            last_data = daily_data.get(last_date, {})
            for pos in self.positions[:]:
                if pos.status == "open":
                    bar = last_data.get(pos.code)
                    sell_price = bar["close"] if bar else pos.buy_price
                    self._close_position(pos, last_date, sell_price, "end_of_backtest")

        self._calc_metrics()

    def _buy_candidates(self, date: str, candidates: list[dict],
                        all_dates: list[str], current_idx: int):
        """用可用资金买入候选股票"""
        if not candidates:
            return
        open_count = len([p for p in self.positions if p.status == "open"])
        slots = max(0, self.top_n - open_count)
        if slots <= 0:
            return
        next_idx = current_idx + 1
        if next_idx >= len(all_dates):
            return
        buy_date = all_dates[next_idx]

        bought = 0
        for stock in candidates:
            if bought >= slots:
                break
            code = stock["code"]
            if any(p.code == code and p.status in ("open", "stop_loss_pending")
                   for p in self.positions):
                continue
            buy_price = stock.get("buy_price", stock.get("price", 0))
            if buy_price <= 0:
                continue
            per_stock = self.cash / slots
            shares = int(per_stock / buy_price / 100) * 100
            if shares < 100:
                continue
            cost = buy_price * shares
            if cost > self.cash:
                continue

            self.cash -= cost
            pos = Position(
                code=code, name=stock.get("name", ""),
                buy_date=buy_date, buy_price=round(buy_price, 3),
                shares=shares, cost=round(cost, 2),
                select_date=date, score=stock.get("score", 0),
            )
            self.positions.append(pos)
            bought += 1

    def _close_position(self, pos: Position, date: str, price: float, reason: str):
        pnl = round((price - pos.buy_price) * pos.shares, 2)
        return_pct = round((price - pos.buy_price) / pos.buy_price * 100, 2)
        proceeds = price * pos.shares
        self.cash += proceeds
        pos.status = "closed"
        self.closed_trades.append({
            "code": pos.code, "name": pos.name,
            "select_date": pos.select_date, "buy_date": pos.buy_date,
            "sell_date": date,
            "buy_price": pos.buy_price, "sell_price": round(price, 3),
            "shares": pos.shares, "cost": pos.cost,
            "proceeds": round(proceeds, 2),
            "pnl": pnl, "return_pct": return_pct,
            "score": pos.score, "reason": reason,
        })

    def _calc_metrics(self):
        trades = self.closed_trades
        n = len(trades)
        if n == 0:
            self.metrics = {
                "initial_capital": self.initial_capital,
                "final_nav": self.initial_capital,
                "total_return_pct": 0.0, "total_pnl": 0.0,
                "total_trades": 0, "avg_return_pct": 0.0,
                "win_rate": 0.0, "wins": 0, "losses": 0,
                "max_return_pct": 0.0, "min_return_pct": 0.0,
                "nav_sharpe": 0.0, "nav_max_drawdown": 0.0,
                "stop_loss_count": 0,
            }
            return

        returns = [t["return_pct"] for t in trades]
        wins = sum(1 for r in returns if r > 0)
        total_pnl = sum(t["pnl"] for t in trades)
        stop_loss_count = sum(1 for t in trades if t["reason"] == "stop_loss")

        nav_values = [h["nav"] for h in self.nav_history]
        final_nav = self.nav_history[-1]["nav"] if self.nav_history else self.initial_capital

        from 选股.backtest.metrics import calc_nav_sharpe, calc_nav_max_drawdown

        self.metrics = {
            "initial_capital": self.initial_capital,
            "final_nav": round(final_nav, 2),
            "total_return_pct": round((final_nav - self.initial_capital) / self.initial_capital * 100, 2),
            "total_pnl": round(total_pnl, 2),
            "total_trades": n,
            "avg_return_pct": round(sum(returns) / n, 2),
            "win_rate": round(wins / n * 100, 1),
            "wins": wins, "losses": n - wins,
            "max_return_pct": round(max(returns), 2),
            "min_return_pct": round(min(returns), 2),
            "nav_sharpe": calc_nav_sharpe(nav_values),
            "nav_max_drawdown": calc_nav_max_drawdown(nav_values),
            "stop_loss_count": stop_loss_count,
        }
