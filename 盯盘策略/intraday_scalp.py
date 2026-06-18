"""
超短线盯盘策略 — 砖型图选股次日分时图量价形态识别

基于前日砖型图选股结果，在 T+1 交易日通过日内 5 分钟分时图量价关系，
识别 5 种强势买入形态 + 3 种危险规避形态。

盯盘数据维度:
  - 实时报价 (quote): 当前价/开盘价/昨收价/涨跌幅
  - 5 分钟 K 线 (minute_bars): OHLCV，用于 VWAP/量比/形态识别
  - 目标锚点 (target.anchors): YC/ML/SL/YH/5日均量 等水位线

协议导出:
  STRATEGY_NAME, STRATEGY_DESC
  NEED_MINUTE_KLINE, MINUTE_PERIOD
  SIGNALS
"""

import numpy as np

# ═══════════════════════════════════════════════════════════════
# 协议元信息
# ═══════════════════════════════════════════════════════════════

STRATEGY_NAME = "超短线盯盘策略"
STRATEGY_DESC = (
    "砖型图选股次日，基于 5 分钟分时图量价关系识别 5 种强买入形态："
    "竞价抢筹强攻 / 平开放量突破 / 浅幅回踩确认 / 低开逆转收复 / 均线爬坡吸筹"
)
NEED_MINUTE_KLINE = True
MINUTE_PERIOD = "5"

# ═══════════════════════════════════════════════════════════════
# 内部阈值常量
# ═══════════════════════════════════════════════════════════════

_HIGH_OPEN_PCT = 0.02          # 高开阈值 (2%)
_FLAT_OPEN_PCT = 0.003         # 平开阈值 (0.3%)
_LOW_OPEN_PCT_MAX = 0.015      # 低开最大容忍 (1.5%)
_VOL_RATIO_STRONG = 15.0       # 量比强势线
_VOL_RATIO_MIN = 8.0           # 量比底线
_VOL_SPIKE_RATIO = 2.0         # 放量倍率 (当前量 / 均量)
_VOL_PULSE_RATIO = 1.5         # 脉冲放量倍率
_BREAKOUT_PCT = 0.01           # 突破涨幅 (1%)
_DIP_MAX_PCT = 0.01            # 回踩最大深度 (1%)
_RECOVERY_VOL_RATIO = 1.5      # 回踩回升量/下跌均量
_NARROW_RANGE = 0.008          # 窄幅波动范围 (0.8%)
_VWAP_SLOPE_MIN = 0.0002        # VWAP 爬坡最低斜率 (每5分钟)
_DANGER_DROP_PCT = 0.01        # 危险跌幅
_TIME_WINDOW_MORNING = 12       # 早盘确认窗口 (60min = 12 根 5min bar)
_TIME_WINDOW_EXTENDED = 18      # 延展窗口 (90min)
_MARKET_DOWN_LIMIT = 0.015      # 大盘跌幅阈值 (暂停买入)


# ═══════════════════════════════════════════════════════════════
# 辅助计算函数
# ═══════════════════════════════════════════════════════════════

def _get(bars: list, i: int, key: str, default=0.0):
    """安全取 bar 字段"""
    try:
        return float(bars[i].get(key, default))
    except (IndexError, TypeError, AttributeError):
        return default


def _get_int(bars: list, i: int, key: str, default=0):
    try:
        return int(bars[i].get(key, default))
    except (IndexError, TypeError, AttributeError):
        return default


def _price_ma(bars: list, key: str = "close", n: int = 5) -> float:
    """最近 N 根 bar 的均价"""
    n = min(n, len(bars))
    if n <= 0:
        return 0.0
    total = sum(_get(bars, -i - 1, key) for i in range(n))
    return total / n


def _volume_ma(bars: list, n: int = 5) -> float:
    """最近 N 根 bar 的均量"""
    n = min(n, len(bars))
    if n <= 0:
        return 1.0
    total = sum(_get_int(bars, -i - 1, "vol") for i in range(n))
    return total / max(n, 1)


def _cumulative_vwap(bars: list) -> list[float]:
    """计算累计分时均线 (VWAP) 序列，用 (H+L+C)/3 * vol 近似"""
    if not bars:
        return []
    vwap = []
    cum_amt = 0.0
    cum_vol = 0.0
    for bar in bars:
        h = _get([bar], 0, "high")
        l = _get([bar], 0, "low")
        c = _get([bar], 0, "close")
        v = _get_int([bar], 0, "vol")
        typ = (h + l + c) / 3 if (h + l + c) > 0 else c
        cum_amt += typ * v
        cum_vol += v
        if cum_vol > 0:
            vwap.append(cum_amt / cum_vol)
        else:
            vwap.append(c)  # fallback
    return vwap


def _latest_vwap(bars: list) -> float:
    """最新 VWAP 值"""
    vwap = _cumulative_vwap(bars)
    return vwap[-1] if vwap else 0.0


def _vwap_slope(bars: list, lookback: int = 12) -> float:
    """VWAP 近 lookback 根 bar 的线性斜率 (每 bar)"""
    vwap = _cumulative_vwap(bars)
    if len(vwap) < lookback:
        return 0.0
    segment = vwap[-lookback:]
    x = np.arange(lookback)
    y = np.array(segment)
    slope, _ = np.polyfit(x, y, 1)
    return float(slope) / lookback if len(vwap) > 0 else 0.0


def _volume_ratio_estimate(quote: dict, bars: list, avg_daily_vol: float = 0) -> float:
    """
    估算量比 = 当日累计量 / (5日均量 / 240 * 当前分钟数)
    avg_daily_vol: 来自 anchors 的 5 日均量，无则粗略估计
    """
    if not bars:
        return 1.0
    minutes_elapsed = len(bars) * 5  # 5 分钟 bar 数 × 5
    if minutes_elapsed <= 0:
        return 1.0
    cum_vol = sum(_get_int(bars, i, "vol") for i in range(len(bars)))

    # 使用 anchors 均量或当天已成交均量估算
    if avg_daily_vol > 0:
        expected = avg_daily_vol / 240.0 * minutes_elapsed
    else:
        # 粗略: 用当日速率代替
        expected = max(cum_vol * 240.0 / minutes_elapsed / 5.0, 1.0)
    return cum_vol / max(expected, 1.0)


def _is_volume_expanding(bars: list, window: int = 3, ratio: float = _VOL_SPIKE_RATIO) -> bool:
    """最近一根 bar 是否相对前 window 根放量"""
    if len(bars) < window + 1:
        return False
    latest_vol = _get_int(bars, -1, "vol")
    prev_avg = _volume_ma(bars[:-1], window)
    return prev_avg > 0 and latest_vol >= prev_avg * ratio


def _is_volume_contracting(bars: list, start: int, end: int) -> bool:
    """检查 bars[start:end] 区间内成交量是否逐根缩小 (end 不包含)"""
    if end - start < 2:
        return False
    vols = [_get_int(bars, i, "vol") for i in range(start, min(end, len(bars)))]
    # 每根比前一根小
    shrinking = sum(1 for i in range(1, len(vols)) if vols[i] < vols[i-1])
    return shrinking >= len(vols) - 1


def _min_in_range(bars: list, start: int, end: int, key: str = "low") -> float:
    """区间内最低价"""
    vals = [_get(bars, i, key) for i in range(start, min(end, len(bars)))]
    return min(vals) if vals else 1e9


def _max_in_range(bars: list, start: int, end: int, key: str = "high") -> float:
    """区间内最高价"""
    vals = [_get(bars, i, key) for i in range(start, min(end, len(bars)))]
    return max(vals) if vals else 0.0


def _dip_depth_pct(bars: list, yc: float) -> float:
    """日内最大回踩深度 (相对 YC)"""
    if not bars or yc <= 0:
        return 0.0
    min_price = _min_in_range(bars, 0, len(bars), "low")
    return (yc - min_price) / yc


def _price_above_vwap_count(bars: list, from_bar: int = 0) -> int:
    """统计价格持续在 VWAP 上方运行的 bar 数 (从 from_bar 开始)"""
    vwap = _cumulative_vwap(bars)
    if not vwap:
        return 0
    count = 0
    for i in range(from_bar, len(bars)):
        c = _get(bars, i, "close")
        l = _get(bars, i, "low")
        # 收盘或最低价在 VWAP 上方
        if c >= vwap[i] - 0.001 and l >= vwap[i] - 0.003:
            count += 1
        else:
            break
    return count


def _get_anchors(target: dict) -> dict:
    """从 target 中提取锚点数据，带 fallback"""
    a = target.get("anchors", {}) if isinstance(target, dict) else {}
    return {
        "yc": a.get("yc", 0),
        "ml": a.get("ml", 0),
        "sl": a.get("sl", 0),
        "yh": a.get("yh", 0),
        "avg_vol_5d": a.get("avg_vol_5d", 0),
    }


# ═══════════════════════════════════════════════════════════════
# 形态一: 竞价抢筹 + 开盘强攻 ★★★★★
# ═══════════════════════════════════════════════════════════════

def _eval_pattern1(quote: dict, minute_bars: list, target: dict) -> bool:
    """
    条件:
      1. 高开 > 2%
      2. 量比 > 15
      3. 前 30 分钟不回踩 VWAP (最多 3 bar 不低于 VWAP)
      4. 当前价 > VWAP
      5. VWAP 以陡峭角度上移
    """
    if not minute_bars or len(minute_bars) < 6:
        return False

    price = float(quote.get("price", 0))
    open_p = float(quote.get("open", 0))
    last_close = float(quote.get("last_close", 0))
    if last_close <= 0:
        return False

    # 条件 1: 高开 > 2%
    if (open_p - last_close) / last_close < _HIGH_OPEN_PCT:
        return False

    # 条件 2: 量比 > 15
    anchors = _get_anchors(target)
    vol_ratio = _volume_ratio_estimate(quote, minute_bars, anchors["avg_vol_5d"])
    if vol_ratio < _VOL_RATIO_STRONG:
        return False

    # 条件 3: 前 6 根 bar (30min) 价格始终在 VWAP 上方
    vwap = _cumulative_vwap(minute_bars)
    n_bars = min(6, len(vwap))
    for i in range(1, n_bars):  # 从第 2 根开始 (第一根可容忍)
        if _get(minute_bars, i, "low") < vwap[i] - 0.005:
            return False

    # 条件 4: 当前价 > VWAP
    if price < _latest_vwap(minute_bars):
        return False

    # 条件 5: VWAP 斜率 > 阈值 (陡峭上升)
    slope = _vwap_slope(minute_bars, min(n_bars, len(minute_bars)))
    if slope <= _VWAP_SLOPE_MIN:
        return False

    # 条件 6: 当前价格创新高 or 横盘不跌
    recent_high = _max_in_range(minute_bars, max(0, n_bars - 3), n_bars, "high")
    if price < recent_high - (recent_high * 0.002):
        return False

    return True


def _inval_pattern1(quote: dict, minute_bars: list, target: dict) -> bool:
    """失效: 价格跌破 VWAP 或跌破昨日收盘价"""
    price = float(quote.get("price", 0))
    last_close = float(quote.get("last_close", 0))
    vwap = _latest_vwap(minute_bars) if minute_bars else 0

    if vwap > 0 and price < vwap - 0.003:
        return True
    if last_close > 0 and price < last_close:
        return True
    return False


# ═══════════════════════════════════════════════════════════════
# 形态二: 平开蓄力 + 放量突破 ★★★★
# ═══════════════════════════════════════════════════════════════

def _eval_pattern2(quote: dict, minute_bars: list, target: dict) -> bool:
    """
    条件:
      1. 平开 ±0.3%
      2. 前 30 分钟内不创新低 (低点 ≥ 开盘 - 0.5%)
      3. 当前出现放量拉升 (量 > 前 N 均量 × 2)
      4. 价格突破近 30 分钟平台高点
      5. 价格 > VWAP
    """
    if not minute_bars or len(minute_bars) < 10:
        return False

    price = float(quote.get("price", 0))
    open_p = float(quote.get("open", 0))
    last_close = float(quote.get("last_close", 0))
    if last_close <= 0:
        return False

    # 条件 1: 平开
    open_pct = (open_p - last_close) / last_close
    if abs(open_pct) > _FLAT_OPEN_PCT:
        return False

    # 条件 2: 前 30 分钟不创新低
    early_bars = min(6, len(minute_bars))
    early_low = _min_in_range(minute_bars, 0, early_bars, "low")
    if early_low < open_p - (open_p * 0.005):
        return False

    # 条件 3: 最近一根 bar 放量
    if not _is_volume_expanding(minute_bars, window=5, ratio=_VOL_SPIKE_RATIO):
        return False

    # 条件 4: 价格突破近期平台
    lookback = min(len(minute_bars) - 1, early_bars)
    platform_high = _max_in_range(minute_bars, max(0, len(minute_bars) - lookback - 1), len(minute_bars) - 1, "high")
    if price <= platform_high:
        return False

    # 条件 5: 价格 > VWAP
    if price < _latest_vwap(minute_bars):
        return False

    return True


def _inval_pattern2(quote: dict, minute_bars: list, target: dict) -> bool:
    """失效: 价格回落到突破前平台下方或跌破分时均线"""
    price = float(quote.get("price", 0))
    vwap = _latest_vwap(minute_bars) if minute_bars else 0
    open_p = float(quote.get("open", 0))
    # 涨幅缩回
    if price < min(vwap, open_p):
        return True
    return False


# ═══════════════════════════════════════════════════════════════
# 形态三: 浅幅回踩 + 缩量确认 + 放量回升 ★★★★
# ═══════════════════════════════════════════════════════════════

def _eval_pattern3(quote: dict, minute_bars: list, target: dict) -> bool:
    """
    条件:
      1. 日内出现过回踩 (低点 < YC 但 > YC - 1%)
      2. 回踩阶段缩量 (成交量逐根减小)
      3. 回升阶段放量 (量 > 下跌均量 × 1.5)
      4. 价格已重新站回 VWAP
      5. 全程未跌破 ML (多空线，来自 anchors)
    """
    if not minute_bars or len(minute_bars) < 8:
        return False

    price = float(quote.get("price", 0))
    last_close = float(quote.get("last_close", 0))
    anchors = _get_anchors(target)
    if last_close <= 0:
        return False

    # 条件 5: 检查 ML (若有锚点数据)
    ml = anchors["ml"]
    if ml > 0 and _min_in_range(minute_bars, 0, len(minute_bars), "low") < ml:
        return False

    # 条件 1: 寻找回踩区间
    bars_len = len(minute_bars)
    dip_start = -1
    dip_end = -1
    dip_min_idx = -1

    for i in range(3, bars_len):
        low = _get(minute_bars, i, "low")
        if low < last_close and low > last_close * (1 - _DIP_MAX_PCT):
            if dip_start < 0:
                dip_start = i
            dip_end = i
            if dip_min_idx < 0 or _get(minute_bars, i, "low") < _get(minute_bars, dip_min_idx, "low"):
                dip_min_idx = i
        else:
            if dip_start >= 0 and low >= last_close:
                break  # 回踩结束

    if dip_start < 0 or dip_end < 0 or dip_min_idx < 0:
        return False

    # 条件 2: 回踩阶段 (dip_start → dip_min_idx) 缩量
    if not _is_volume_contracting(minute_bars, dip_start, dip_min_idx + 1):
        return False

    # 条件 3: 回升阶段 (dip_min_idx → 当前) 放量
    decline_avg_vol = _volume_ma(minute_bars[dip_start:dip_min_idx + 1], max(1, dip_min_idx - dip_start + 1))
    recovery_start = dip_min_idx + 1
    if recovery_start >= bars_len:
        return False
    recovery_vol = sum(_get_int(minute_bars, i, "vol") for i in range(recovery_start, bars_len))
    recovery_bars = max(1, bars_len - recovery_start)
    recovery_avg_vol = recovery_vol / recovery_bars
    if recovery_avg_vol < decline_avg_vol * _RECOVERY_VOL_RATIO:
        return False

    # 条件 4: 当前价 > VWAP
    if price < _latest_vwap(minute_bars):
        return False

    return True


def _inval_pattern3(quote: dict, minute_bars: list, target: dict) -> bool:
    """失效: 跌破当日最低点，或重回 VWAP 下方"""
    price = float(quote.get("price", 0))
    vwap = _latest_vwap(minute_bars) if minute_bars else 0
    if not minute_bars:
        return False
    day_low = _min_in_range(minute_bars, 0, len(minute_bars), "low")
    if price < day_low:
        return True
    if vwap > 0 and price < vwap - 0.003:
        return True
    return False


# ═══════════════════════════════════════════════════════════════
# 形态四: 低开逆转 + 收复 YC ★★★
# ═══════════════════════════════════════════════════════════════

def _eval_pattern4(quote: dict, minute_bars: list, target: dict) -> bool:
    """
    条件:
      1. 低开幅度在 0.3%~1.5% 之间
      2. 30 分钟内回到 YC 上方
      3. 收复后回踩不破 YC
      4. 回升成交量 > 开盘下跌成交量
    """
    if not minute_bars or len(minute_bars) < 6:
        return False

    price = float(quote.get("price", 0))
    open_p = float(quote.get("open", 0))
    last_close = float(quote.get("last_close", 0))
    if last_close <= 0:
        return False

    # 条件 1: 低开范围
    open_pct = (open_p - last_close) / last_close
    if open_pct > -_FLAT_OPEN_PCT or open_pct < -_LOW_OPEN_PCT_MAX:
        return False

    # 条件 2: 前 30 分钟 (6 bar) 内收复 YC
    early_bars = min(6, len(minute_bars))
    recovered = False
    recover_bar = -1
    for i in range(early_bars):
        if _get(minute_bars, i, "close") > last_close:
            recovered = True
            recover_bar = i
            break
        if _get(minute_bars, i, "high") > last_close:
            recovered = True
            recover_bar = i
            break

    if not recovered:
        return False

    # 条件 3: 收复后回踩不破 YC
    for i in range(recover_bar, len(minute_bars)):
        if _get(minute_bars, i, "low") < last_close - 0.003:
            return False

    # 条件 4: 回升量 > 开盘下跌量
    if recover_bar <= 1:
        return False  # 回升太快，没有足够量能对比
    decline_vol = sum(_get_int(minute_bars, i, "vol") for i in range(0, recover_bar))
    recovery_vol = sum(_get_int(minute_bars, i, "vol") for i in range(recover_bar, len(minute_bars)))
    if recovery_vol < decline_vol:
        return False

    # 当前价必须 > YC
    if price <= last_close:
        return False

    return True


def _inval_pattern4(quote: dict, minute_bars: list, target: dict) -> bool:
    """失效: 再度跌破 YC"""
    price = float(quote.get("price", 0))
    last_close = float(quote.get("last_close", 0))
    if last_close > 0 and price < last_close - 0.003:
        return True
    return False


# ═══════════════════════════════════════════════════════════════
# 形态五: 分时均线爬坡 (持续吸筹型) ★★★
# ═══════════════════════════════════════════════════════════════

def _eval_pattern5(quote: dict, minute_bars: list, target: dict) -> bool:
    """
    条件:
      1. 价格围绕 VWAP 窄幅波动 (±0.8%) 持续 > 12 根 bar (60min)
      2. VWAP 持续缓慢爬升 (斜率正且平缓)
      3. 价格始终运行在 VWAP 上方或紧贴
      4. 间歇性出现小量脉冲 (5-min 量柱偶尔 > 均量 × 1.5，非持续巨量)
      5. 当前价突破窄幅区间上沿
    """
    if not minute_bars or len(minute_bars) < 14:
        return False

    price = float(quote.get("price", 0))
    vwap = _cumulative_vwap(minute_bars)

    # 条件 1: 近 12 根 bar 波动区间在 ±0.8% 内
    lookback = min(12, len(minute_bars))
    segment_high = _max_in_range(minute_bars, len(minute_bars) - lookback, len(minute_bars), "high")
    segment_low = _min_in_range(minute_bars, len(minute_bars) - lookback, len(minute_bars), "low")
    mid_price = (segment_high + segment_low) / 2
    if mid_price <= 0:
        return False
    if (segment_high - segment_low) / mid_price > _NARROW_RANGE:
        return False

    # 条件 2: VWAP 持续缓慢上升
    slope = _vwap_slope(minute_bars, lookback)
    if slope <= 0:  # 斜率非负即可 (缓慢爬升)
        return False

    # 条件 3: 价格在 VWAP 上方持续运行
    above_count = _price_above_vwap_count(minute_bars, len(minute_bars) - lookback)
    if above_count < lookback * 0.7:
        return False

    # 条件 4: 脉冲放量 (至少出现 1 次 > 均量 × 1.5)
    avg_vol = _volume_ma(minute_bars[-lookback:], lookback)
    pulse_count = 0
    for i in range(len(minute_bars) - lookback, len(minute_bars)):
        vol = _get_int(minute_bars, i, "vol")
        if vol > avg_vol * _VOL_PULSE_RATIO:
            pulse_count += 1
    if pulse_count < 1:
        return False

    # 条件 5: 当前价突破窄幅上沿
    if price < segment_high - (segment_high * 0.001):
        return False

    return True


def _inval_pattern5(quote: dict, minute_bars: list, target: dict) -> bool:
    """失效: 有效跌破 VWAP 或区间下沿"""
    price = float(quote.get("price", 0))
    if not minute_bars:
        return False
    vwap = _latest_vwap(minute_bars)
    if vwap > 0 and price < vwap - 0.005:
        return True
    # 跌破窄幅下沿
    lookback = min(12, len(minute_bars))
    range_low = _min_in_range(minute_bars, len(minute_bars) - lookback, len(minute_bars), "low")
    if price < range_low - 0.002:
        return True
    return False


# ═══════════════════════════════════════════════════════════════
# 危险形态一: 高开诱多出货 ⚠️
# ═══════════════════════════════════════════════════════════════

def _eval_danger_high_fake(quote: dict, minute_bars: list, target: dict) -> bool:
    """
    条件:
      1. 高开 > 1%
      2. 冲高后持续回落
      3. 当前价 < VWAP (有效跌破)
      4. 当前价 < 开盘价 - 1%
    """
    if not minute_bars or len(minute_bars) < 8:
        return False

    price = float(quote.get("price", 0))
    open_p = float(quote.get("open", 0))
    last_close = float(quote.get("last_close", 0))
    if last_close <= 0:
        return False

    # 条件 1
    if (open_p - last_close) / last_close < 0.01:
        return False

    # 条件 2 & 3 & 4: 冲高 → 回落 → 破 VWAP → 跌幅 > 1%
    early_high = _max_in_range(minute_bars, 0, min(4, len(minute_bars)), "high")
    if price > early_high * 0.995:  # 未回落
        return False
    if price >= _latest_vwap(minute_bars):  # 还在 VWAP 上方
        return False
    if (open_p - price) / open_p < _DANGER_DROP_PCT:
        return False

    return True


def _inval_danger_high_fake(quote: dict, minute_bars: list, target: dict) -> bool:
    """危险解除: 价格重新站回 VWAP 且位于开盘价上方"""
    price = float(quote.get("price", 0))
    open_p = float(quote.get("open", 0))
    vwap = _latest_vwap(minute_bars) if minute_bars else 0
    return price > vwap and price > open_p


# ═══════════════════════════════════════════════════════════════
# 危险形态二: 放量滞涨 ⚠️
# ═══════════════════════════════════════════════════════════════

def _eval_danger_volume_trap(quote: dict, minute_bars: list, target: dict) -> bool:
    """
    条件:
      1. 近 3 根 bar 量持续放大 (每根 > 均量 × 2)
      2. 但价格横盘或微跌 (近 3 根 bar 涨幅 < 0.3%)
    """
    if not minute_bars or len(minute_bars) < 5:
        return False

    price = float(quote.get("price", 0))
    avg_vol = _volume_ma(minute_bars, 10)

    if avg_vol <= 0:
        return False

    # 条件 1: 近 3 根 bar 均放量
    for i in range(1, 4):
        vol = _get_int(minute_bars, -i, "vol")
        if vol < avg_vol * _VOL_SPIKE_RATIO:
            return False

    # 条件 2: 近 3 根 bar 价格横盘
    price_3_ago = _get(minute_bars, -4, "close", default=price)
    if price_3_ago > 0:
        pct = abs(price - price_3_ago) / price_3_ago
        if pct > 0.003:
            return False

    # 条件 3: 当前价 < VWAP 或持平
    if price > _latest_vwap(minute_bars) + 0.003:
        return False

    return True


def _inval_danger_volume_trap(quote: dict, minute_bars: list, target: dict) -> bool:
    """危险解除: 放量 + 价格上涨或量缩"""
    price = float(quote.get("price", 0))
    if not minute_bars or len(minute_bars) < 5:
        return True
    # 价格方向选择 (涨)
    price_3_ago = _get(minute_bars, -4, "close", default=price)
    if price_3_ago > 0 and price > price_3_ago * 1.005:
        return True
    # 量能萎缩
    latest_vol = _get_int(minute_bars, -1, "vol")
    avg_vol = _volume_ma(minute_bars, 10)
    if latest_vol < avg_vol:
        return True
    return False


# ═══════════════════════════════════════════════════════════════
# 危险形态三: 破位多空线 ⚠️
# ═══════════════════════════════════════════════════════════════

def _eval_danger_below_ml(quote: dict, minute_bars: list, target: dict) -> bool:
    """
    条件:
      1. 价格有效跌破 ML (多空线)
      2. 趋势保护失效
    """
    price = float(quote.get("price", 0))
    anchors = _get_anchors(target)
    ml = anchors["ml"]
    if ml <= 0:
        return False  # 无锚点数据，不判断

    # 跌破 ML 且持续 > 3 根 bar
    if price < ml - 0.005:
        if minute_bars and len(minute_bars) >= 3:
            below_count = sum(
                1 for i in range(1, 4)
                if _get(minute_bars, -i, "close") < ml
            )
            return below_count >= 2
        return True
    return False


def _inval_danger_below_ml(quote: dict, minute_bars: list, target: dict) -> bool:
    """危险解除: 放量回升到 ML 上方"""
    price = float(quote.get("price", 0))
    anchors = _get_anchors(target)
    ml = anchors["ml"]
    if ml <= 0:
        return True
    if price > ml + 0.005:
        return _is_volume_expanding(minute_bars, window=3, ratio=1.2) if minute_bars else True
    return False


# ═══════════════════════════════════════════════════════════════
# 信号注册表
# ═══════════════════════════════════════════════════════════════

SIGNALS = [
    # ── 5 种强买入形态 ──
    {
        "name": "竞价抢筹强攻",
        "desc": "高开>2% + 量比>15 + 不回踩分时均线 + VWAP陡峭上升",
        "level": "★★★★★",
        "evaluate": _eval_pattern1,
        "invalidation": _inval_pattern1,
    },
    {
        "name": "平开放量突破",
        "desc": "平开±0.3% + 前30min不创新低 + 放量突破平台高点",
        "level": "★★★★",
        "evaluate": _eval_pattern2,
        "invalidation": _inval_pattern2,
    },
    {
        "name": "浅幅回踩确认",
        "desc": "跌幅<1% + 缩量下跌 + 放量回升 + 站稳VWAP",
        "level": "★★★★",
        "evaluate": _eval_pattern3,
        "invalidation": _inval_pattern3,
    },
    {
        "name": "低开逆转收复",
        "desc": "低开<1.5% + 30min收复YC + 回踩不破YC + 回升放量",
        "level": "★★★",
        "evaluate": _eval_pattern4,
        "invalidation": _inval_pattern4,
    },
    {
        "name": "均线爬坡吸筹",
        "desc": "窄幅波动±0.8% + VWAP缓升 + 脉冲放量 + 突破区间上沿",
        "level": "★★★",
        "evaluate": _eval_pattern5,
        "invalidation": _inval_pattern5,
    },

    # ── 3 种危险规避形态 ──
    {
        "name": "高开诱多出货",
        "desc": "高开>1%后持续回落跌破VWAP，跌幅>1%",
        "level": "⚠️ 危险",
        "evaluate": _eval_danger_high_fake,
        "invalidation": _inval_danger_high_fake,
    },
    {
        "name": "放量滞涨陷阱",
        "desc": "连续放量但价格横盘或微跌，疑似主力对倒",
        "level": "⚠️ 危险",
        "evaluate": _eval_danger_volume_trap,
        "invalidation": _inval_danger_volume_trap,
    },
    {
        "name": "破位多空线",
        "desc": "价格有效跌破ML多空线，趋势保护失效",
        "level": "⚠️ 危险",
        "evaluate": _eval_danger_below_ml,
        "invalidation": _inval_danger_below_ml,
    },
]
