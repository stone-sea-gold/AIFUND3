"""
示例盯盘策略：量价突破
=====================
基于价格突破和成交量放大的盘中信号监控。
提供两个示例信号，展示 evaluate / invalidation 的写法。
"""

STRATEGY_NAME = "量价突破"
STRATEGY_DESC = "基于价格突破和成交量放大的盘中信号监控"
NEED_MINUTE_KLINE = True
MINUTE_PERIOD = 1


def _eval_breakout(quote, minute_bars):
    """放量突破日内高点：价格突破开盘价一定幅度 + 当前分钟成交量放大"""
    if not quote or quote.get("price", 0) <= 0:
        return False
    price = quote["price"]
    open_price = quote.get("open", 0)
    high = quote.get("high", 0)
    if open_price <= 0 or high <= 0:
        return False

    # 价格突破日内高点（当前价 >= 最高价）
    if price < high:
        return False

    # 计算突破幅度：至少比开盘价高 1.5%
    pct_from_open = (price - open_price) / open_price * 100
    if pct_from_open < 1.5:
        return False

    # 检查成交量放大：当前成交量 > 前5分钟平均的1.5倍
    if minute_bars and len(minute_bars) >= 6:
        recent_vols = [b.get("vol", 0) for b in minute_bars[-6:-1]]
        avg_vol = sum(recent_vols) / len(recent_vols) if recent_vols else 0
        current_vol = minute_bars[-1].get("vol", 0)
        if avg_vol > 0 and current_vol >= avg_vol * 1.5:
            return True

    return False


def _inv_breakout(quote, minute_bars):
    """突破失效：价格跌破开盘价"""
    if not quote or quote.get("price", 0) <= 0:
        return False
    return quote["price"] < quote.get("open", float("inf"))


def _eval_ma_pullback(quote, minute_bars):
    """缩量回踩均线：价格回踩分时均价线附近且成交量萎缩"""
    if not quote or not minute_bars or len(minute_bars) < 10:
        return False

    price = quote["price"]
    if price <= 0:
        return False

    # 计算分时均价线（VWAP）
    total_amount = 0
    total_volume = 0
    for bar in minute_bars:
        vol = bar.get("vol", 0)
        amount = bar.get("amount", 0)
        if vol > 0 and amount > 0:
            total_volume += vol
            total_amount += amount
    if total_volume <= 0:
        return False
    vwap = total_amount / total_volume

    # 价格在 VWAP 附近 ±0.3%
    pct_diff = abs(price - vwap) / vwap * 100
    if pct_diff > 0.3:
        return False

    # 最近3根K线成交量萎缩：当前成交量 < 前5根平均的70%
    if len(minute_bars) >= 8:
        recent_vols = [b.get("vol", 0) for b in minute_bars[-8:-3]]
        avg_vol = sum(recent_vols) / len(recent_vols) if recent_vols else 0
        current_vol = minute_bars[-1].get("vol", 0)
        if avg_vol > 0 and current_vol < avg_vol * 0.7:
            return True

    return False


def _inv_ma_pullback(quote, minute_bars):
    """回踩失效：价格跌破开盘价的1%以上"""
    if not quote or quote.get("price", 0) <= 0:
        return False
    open_price = quote.get("open", 0)
    if open_price <= 0:
        return False
    return quote["price"] < open_price * 0.99


SIGNALS = [
    {
        "name": "放量突破日内高点",
        "desc": "价格突破当日最高价，突破幅度>1.5%，且成交量放大1.5倍",
        "level": "强烈",
        "evaluate": _eval_breakout,
        "invalidation": _inv_breakout,
    },
    {
        "name": "缩量回踩均线",
        "desc": "价格回踩分时均价线±0.3%，且成交量萎缩至均值70%以下",
        "level": "温和",
        "evaluate": _eval_ma_pullback,
        "invalidation": _inv_ma_pullback,
    },
]
