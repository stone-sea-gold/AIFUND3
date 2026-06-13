import math


def calc_sharpe_ratio(daily_returns: list[float], risk_free_rate: float = 0.02, periods_per_year: int = 250) -> float:
    """计算年化夏普比率

    公式: (mean_return - daily_rf) / std_dev * sqrt(periods_per_year)

    Args:
        daily_returns: 每日收益率列表（如 [0.01, -0.005, ...]）
        risk_free_rate: 年化无风险利率
        periods_per_year: 年化周期数（日K=250）

    Returns:
        年化夏普比率，保留4位小数
    """
    if not daily_returns or len(daily_returns) < 2:
        return 0.0
    mean_return = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_return) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std_dev = math.sqrt(variance)
    if std_dev == 0:
        return 0.0
    daily_rf = risk_free_rate / periods_per_year
    excess_return = mean_return - daily_rf
    return round(excess_return / std_dev * math.sqrt(periods_per_year), 4)


def calc_max_drawdown(values: list[float]) -> float:
    """计算最大回撤百分比

    Args:
        values: 净值/价格序列

    Returns:
        最大回撤百分比（如 18.18 表示回撤 18.18%）
    """
    if not values or len(values) < 2:
        return 0.0
    peak = values[0]
    max_dd = 0.0
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)


def calc_win_rate(returns: list[float]) -> float:
    """计算胜率（盈利笔数 / 总笔数 * 100）"""
    if not returns:
        return 0.0
    wins = sum(1 for r in returns if r > 0)
    return round(wins / len(returns) * 100, 1)


def calc_profit_factor(returns: list[float]) -> float:
    """计算盈亏比（总盈利 / 总亏损绝对值）"""
    total_gain = sum(r for r in returns if r > 0)
    total_loss = abs(sum(r for r in returns if r < 0))
    if total_loss == 0:
        return float("inf") if total_gain > 0 else 0.0
    return round(total_gain / total_loss, 4)


def calc_avg_return(returns: list[float]) -> float:
    """计算平均每笔收益率"""
    if not returns:
        return 0.0
    return round(sum(returns) / len(returns), 4)


def calc_nav_sharpe(nav_values: list[float], risk_free_rate: float = 0.02,
                    periods_per_year: int = 250) -> float:
    """基于 NAV 曲线计算夏普比率"""
    if len(nav_values) < 3:
        return 0.0
    daily_returns = []
    for i in range(1, len(nav_values)):
        if nav_values[i - 1] > 0:
            daily_returns.append((nav_values[i] - nav_values[i - 1]) / nav_values[i - 1])
    return calc_sharpe_ratio(daily_returns, risk_free_rate, periods_per_year)


def calc_nav_max_drawdown(nav_values: list[float]) -> float:
    """基于 NAV 曲线计算最大回撤"""
    return calc_max_drawdown(nav_values)


def calc_capital_utilization(trades: list[dict], initial_capital: float,
                             total_days: int) -> float:
    """计算资金利用率（资金被占用的平均比例）

    Args:
        trades: 交易记录列表，需包含 cost, buy_date, sell_date
        initial_capital: 初始资金
        total_days: 总交易日数

    Returns:
        资金利用率百分比 (0~100)
    """
    if not trades or total_days <= 0 or initial_capital <= 0:
        return 0.0

    # 简化计算：每笔交易占用资金的天数 / (总资金 × 总天数)
    total_occupied = 0.0
    for t in trades:
        cost = t.get("cost", 0)
        # 估算持有天数（从 buy_date 到 sell_date）
        buy_d = t.get("buy_date", "")
        sell_d = t.get("sell_date", "")
        if buy_d and sell_d:
            # 简单估算：日期字符串差值（不精确，但足够）
            try:
                from datetime import datetime
                bd = datetime.fromisoformat(buy_d)
                sd = datetime.fromisoformat(sell_d)
                hold_days = max(1, (sd - bd).days)
            except Exception:
                hold_days = 3
            total_occupied += cost * hold_days

    max_possible = initial_capital * total_days
    return round(total_occupied / max_possible * 100, 1) if max_possible > 0 else 0.0
