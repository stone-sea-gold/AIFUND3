"""
止损策略模块 — 独立于回测核心，可单独修改

协议：
  每个止损策略是一个函数，接收当日行情和持仓信息，返回操作指令。

  check_stop_loss(position, today_bar, yesterday_bar, params) -> (action, price)
    action: "hold" | "stop_loss" | "cancel_stop"
    price:  卖出价格（仅 stop_loss 时有效）

  check_sell_on_open(position, today_bar, params) -> (action, price)
    对 stop_loss_pending 状态的持仓，在 T+2 开盘时判断是否执行止损
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any


# ═══════════════════════════════════════════════════════════════
# 默认止损策略参数
# ═══════════════════════════════════════════════════════════════

DEFAULT_PARAMS = {
    "stop_loss_pct": 3.0,      # T+1 收盘亏损超过此百分比 → 标记止损
    "gap_up_pct": 4.0,         # T+2 高开超过此百分比 → 取消止损
    "holding_days": 3,         # 默认持有天数
}


# ═══════════════════════════════════════════════════════════════
# 默认止损策略
# ═══════════════════════════════════════════════════════════════

def default_check_close(position, today_bar: dict, params: dict) -> str:
    """T+1 收盘时检查：是否触发止损标记

    Args:
        position: 持仓对象 (Position)
        today_bar: 当日行情 {"open", "close", "high", "low"}
        params: 止损参数

    Returns:
        "stop_loss_pending" — 标记待止损
        "hold" — 继续持有
    """
    stop_pct = params.get("stop_loss_pct", 3.0)
    close = today_bar["close"]
    if close < position.buy_price * (1 - stop_pct / 100):
        position.buy_day_close = close
        return "stop_loss_pending"
    return "hold"


def default_check_open(position, today_bar: dict, params: dict) -> tuple[str, float]:
    """T+2 开盘时检查：是否执行止损或取消

    Args:
        position: 持仓对象（status 应为 stop_loss_pending）
        today_bar: 当日行情
        params: 止损参数

    Returns:
        (action, price)
        action: "stop_loss" — 执行止损卖出 | "cancel" — 取消止损继续持有
        price: 卖出价格（仅 stop_loss 时有效）
    """
    gap_pct = params.get("gap_up_pct", 4.0)
    open_price = today_bar["open"]
    prev_close = position.buy_day_close

    if open_price >= prev_close * (1 + gap_pct / 100):
        return "cancel", 0.0
    return "stop_loss", open_price


def default_should_sell(position, current_idx: int, buy_idx: int, params: dict) -> bool:
    """到期检查：是否该卖出（持有满 N 天）

    Args:
        position: 持仓对象
        current_idx: 当前日期在 all_dates 中的索引
        buy_idx: 买入日期在 all_dates 中的索引
        params: 止损参数

    Returns:
        True — 到期应卖出
    """
    holding_days = params.get("holding_days", 3)
    return (current_idx - buy_idx) >= holding_days


# ═══════════════════════════════════════════════════════════════
# 止损策略注册表（可扩展）
# ═══════════════════════════════════════════════════════════════

STOP_LOSS_STRATEGIES = {
    "default": {
        "name": "默认止损",
        "desc": "T+1亏损>3%标记止损，T+2高开≥4%取消，否则开盘卖出；默认持有3天",
        "check_close": default_check_close,
        "check_open": default_check_open,
        "should_sell": default_should_sell,
        "default_params": DEFAULT_PARAMS,
    },
    # 在此处添加自定义止损策略，例如：
    # "trailing": {
    #     "name": "移动止损",
    #     "desc": "从最高点回撤N%止损",
    #     "check_close": trailing_check_close,
    #     "check_open": trailing_check_open,
    #     "should_sell": trailing_should_sell,
    #     "default_params": {"trailing_pct": 5.0, "holding_days": 5},
    # },
}


def get_stop_loss_strategy(name: str = "default") -> dict:
    """获取止损策略配置"""
    if name not in STOP_LOSS_STRATEGIES:
        raise ValueError(f"止损策略 '{name}' 不存在，可用: {list(STOP_LOSS_STRATEGIES.keys())}")
    return STOP_LOSS_STRATEGIES[name]


def list_strategies() -> list[dict]:
    """列出所有可用止损策略"""
    return [
        {"id": k, "name": v["name"], "desc": v["desc"]}
        for k, v in STOP_LOSS_STRATEGIES.items()
    ]
