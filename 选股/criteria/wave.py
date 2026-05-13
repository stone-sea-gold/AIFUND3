"""
波浪理论筛选条件

复用 fetch_wave_data.py 的指标函数，每个条件返回 (得分, 详情)
"""

import os
import sys

_script_dir = os.path.join(os.path.dirname(__file__), "..", "..", "脚本")
sys.path.insert(0, _script_dir)
from fetch_wave_data import (
    calc_macd, calc_fibonacci_retracement, calc_volume_profile,
    detect_macd_divergence, find_swing_points,
)


def build_wave_indicators(klines: list[dict], closes: list[float]) -> dict:
    """从K线计算波浪理论全套指标，供多个条件复用"""
    macd = calc_macd(closes, 5, 34, 5)
    swing = find_swing_points(klines, left=5, right=5)
    divergences = detect_macd_divergence(klines, macd, swing)
    vol_profile = calc_volume_profile(klines, 20)

    # Fibonacci 回撤（基于最近一波主要走势）
    fib_data = {}
    if swing["highs"] and swing["lows"]:
        last_high = max(swing["highs"], key=lambda x: x[1])
        last_low = min(swing["lows"][-5:], key=lambda x: x[1]) if len(swing["lows"]) >= 5 else min(swing["lows"], key=lambda x: x[1])
        if last_high[0] > last_low[0]:
            fib_data = {
                "direction": "up_retrace",
                "high": {"price": last_high[1], "date": last_high[2]},
                "low": {"price": last_low[1], "date": last_low[2]},
                "levels": calc_fibonacci_retracement(last_high[1], last_low[1], "up"),
            }
        else:
            fib_data = {
                "direction": "down_bounce",
                "high": {"price": last_high[1], "date": last_high[2]},
                "low": {"price": last_low[1], "date": last_low[2]},
                "levels": calc_fibonacci_retracement(last_high[1], last_low[1], "down"),
            }

    return {
        "macd": macd,
        "swing_points": {
            "highs": [{"index": h[0], "price": h[1], "date": h[2]} for h in swing["highs"]],
            "lows": [{"index": l[0], "price": l[1], "date": l[2]} for l in swing["lows"]],
        },
        "divergences": divergences,
        "fibonacci": fib_data,
        "volume_profile": {k: v for k, v in vol_profile.items() if k != "vol_ratios"},
    }


# ═══════════════════════════════════════════════════════════════
# 正向筛选条件
# ═══════════════════════════════════════════════════════════════

def check_macd_golden_cross(ind: dict, weight: int, params: dict) -> tuple[int, dict]:
    """MACD(5,34,5)金叉：最近N日内DIF上穿DEA"""
    lookback = params.get("lookback_days", 5)
    dif = ind["macd"]["dif"]
    dea = ind["macd"]["dea"]
    n = len(dif)
    for offset in range(min(lookback, n - 1)):
        idx = n - 1 - offset
        if idx < 1:
            continue
        if dif[idx] > dea[idx] and dif[idx - 1] <= dea[idx - 1]:
            return weight, {"date": f"T-{offset}", "reason": f"MACD金叉 T-{offset} DIF={dif[idx]:.4f}"}
    # 部分得分：DIF>DEA（金叉已持有中）
    if dif[-1] > dea[-1]:
        return weight // 2, {"dif": round(dif[-1], 4), "dea": round(dea[-1], 4), "reason": "金叉持有中(非近日触发)"}
    return 0, {"dif": round(dif[-1], 4), "reason": "死叉状态"}


def check_bottom_divergence(ind: dict, weight: int, params: dict) -> tuple[int, dict]:
    """MACD底背离：价格新低而DIF走高"""
    divs = ind.get("divergences", [])
    lookback = params.get("lookback_days", 60)
    # 筛选底背离
    bottom_divs = [d for d in divs if d["type"] == "底背离"]
    if not bottom_divs:
        return 0, {"reason": "无底背离"}
    # 过滤最近 lookback 天内的
    klines_len = len(ind["macd"]["dif"])
    recent = []
    for d in bottom_divs:
        idx = d.get("point2", {}).get("index", 0)
        if klines_len - 1 - idx < lookback:
            recent.append(d)
    if recent:
        latest = recent[-1]
        return weight, {
            "count": len(recent),
            "latest": latest["desc"],
            "reason": f"底背离×{len(recent)}，最近: {latest['desc']}",
        }
    # 有底背离但较久
    return weight // 2, {"count": len(bottom_divs), "reason": f"有{len(bottom_divs)}个底背离(较远)"}


def check_fib_support(ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """斐波那契支撑：当前价格在Fib关键回撤位附近"""
    fib = ind.get("fibonacci", {})
    levels = fib.get("levels", {})
    if not levels:
        return 0, {"reason": "无Fibonacci数据"}
    price = klines[-1]["close"]
    fib_keys = params.get("fib_levels", [0.382, 0.5, 0.618])
    tolerance = params.get("tolerance", 0.02)
    matched = []
    for ratio in fib_keys:
        level_label = f"{ratio * 100:.1f}%"
        level_price = levels.get(level_label)
        if level_price and level_price > 0:
            deviation = abs(price - level_price) / level_price
            if deviation < tolerance:
                matched.append((level_label, level_price, round(deviation * 100, 1)))
    if matched:
        best = min(matched, key=lambda x: x[2])
        return weight, {
            "matched": matched,
            "reason": f"价{price:.2f}在Fib{best[0]}({best[1]:.2f})±{best[2]}%",
            "count": len(matched),
        }
    return 0, {"price": price, "reason": "不在Fib关键位附近"}


def check_volume_shrink_stop(ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """缩量止跌：近期均量 < 前期 × shrink_ratio，且价格趋于平稳"""
    shrink_ratio = params.get("shrink_ratio", 0.7)
    lookback = params.get("lookback_days", 20)
    vp = ind.get("volume_profile", {})
    if not vp:
        return 0, {"reason": "无成交量数据"}

    avg_recent = vp.get("avg_recent_vol", 0)
    avg_prior = vp.get("avg_prior_vol", 0)
    if avg_recent == 0 or avg_prior == 0:
        return 0, {"reason": "成交量数据不足"}

    vol_ratio = avg_recent / avg_prior

    # 检查价格是否企稳（近5根振幅收窄）
    recent_bars = klines[-min(5, len(klines)):]
    amplitudes = [b["amplitude"] for b in recent_bars]
    avg_amp = sum(amplitudes) / len(amplitudes) if amplitudes else 100

    score = 0
    reasons = []
    if vol_ratio < shrink_ratio:
        reasons.append(f"缩量(近/前={vol_ratio:.2f}<{shrink_ratio})")
        score += weight // 2 + 1
    if avg_amp < 4:  # 振幅<4%视为收敛
        reasons.append(f"振幅收窄({avg_amp:.1f}%)")
        score += weight // 2
    return min(score, weight), {
        "vol_ratio": round(vol_ratio, 2),
        "avg_amplitude": round(avg_amp, 1),
        "reason": " + ".join(reasons) if reasons else "不满足缩量止跌条件",
    }


def check_wave_position_fav(ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """波浪位有利评估（简化版：基于MACD位置和回调深度做启发式判断）"""
    macd = ind["macd"]
    fib = ind.get("fibonacci", {})
    closes = [b["close"] for b in klines]
    n = len(closes)
    if n < 60:
        return 0, {"reason": "数据不足"}

    dif = macd["dif"]
    dea = macd["dea"]
    hist = macd["histogram"]

    # 简易启发式：MACD从零轴附近金叉回升 = 可能浪2/浪4/浪C完成
    score = 0
    reasons = []

    # 1. DIF值：在零轴附近（-0.5 到 1.0之间）比极端值更好（不是追高）
    if dif[-1] is not None:
        if abs(dif[-1]) < 0.5:
            score += weight // 2
            reasons.append(f"DIF近零轴({dif[-1]:.4f})")

    # 2. 回调深度：Fibonacci 38.2%~61.8% = 理想浪2/浪4调整区
    fib_levels = fib.get("levels", {})
    price = klines[-1]["close"]
    for label, level in fib_levels.items():
        if label in ("38.2%", "50.0%", "61.8%"):
            if abs(price - level) / level < 0.05:
                score += weight // 2
                reasons.append(f"Fib{label}附近")
                break

    # 3. 柱状体由负转正或正在收敛
    if hist[-1] is not None and hist[-2] is not None:
        if hist[-1] > hist[-2] and hist[-2] < 0:
            score += weight // 3
            reasons.append("红柱回升")

    return min(score, weight), {"reason": " + ".join(reasons) if reasons else "波浪位不明确"}


# ═══════════════════════════════════════════════════════════════
# 加分项
# ═══════════════════════════════════════════════════════════════

def check_bull_divergence_multi(ind: dict, weight: int, params: dict) -> tuple[int, dict]:
    """多重底背离：同一股出现多个级别的底背离"""
    divs = ind.get("divergences", [])
    bottom_divs = [d for d in divs if d["type"] == "底背离"]
    count = len(bottom_divs)
    if count >= 3:
        return weight, {"count": count, "reason": f"多重底背离×{count}"}
    if count >= 2:
        return weight // 2, {"count": count, "reason": f"双重底背离"}
    return 0, {"reason": "无多重底背离"}


def check_fib_cluster_support(ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """Fib汇聚支撑：两个不同来源的Fib位在同一区间重合"""
    fib = ind.get("fibonacci", {})
    swing = ind.get("swing_points", {})
    if not fib or not swing:
        return 0, {"reason": "Fib/摆动点数据不足"}
    price = klines[-1]["close"]
    levels = fib.get("levels", {})
    # 检查多个Fib级别是否汇聚在±3%内
    key_levels = []
    for label, lv in levels.items():
        if label in ("38.2%", "50.0%", "61.8%", "78.6%"):
            key_levels.append((label, lv))
    # 如果两个Fib水平相差<3%，视为汇聚
    clusters = []
    for i in range(len(key_levels)):
        for j in range(i + 1, len(key_levels)):
            if abs(key_levels[i][1] - key_levels[j][1]) / max(key_levels[i][1], 0.001) < 0.03:
                clusters.append((key_levels[i], key_levels[j]))
    # 当前价格在汇聚区附近
    for (l1, v1), (l2, v2) in clusters:
        if abs(price - v1) / v1 < 0.03:
            return weight, {"levels": f"Fib{l1}+Fib{l2}@{v1:.2f}", "reason": f"Fib汇聚支撑 @{v1:.2f}"}
    return 0, {"reason": "无Fib汇聚"}


def check_volume_breakout_confirm(ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """放量阳线确认：近3日内有中阳(实体≥body_pct%) + 放量(>均量×vol_multiple)"""
    body_pct = params.get("body_pct", 2.0)
    vol_multiple = params.get("vol_multiple", 2.0)
    n = len(klines)
    for offset in range(min(3, n - 5)):
        idx = n - 1 - offset
        bar = klines[idx]
        if bar["open"] <= 0:
            continue
        bar_body = (bar["close"] - bar["open"]) / bar["open"] * 100
        if bar_body < body_pct:
            continue
        # 计算前5日均量
        if idx < 5:
            continue
        avg5_vol = sum(klines[j]["volume"] for j in range(idx - 5, idx)) / 5
        if avg5_vol > 0 and bar["volume"] > avg5_vol * vol_multiple:
            return weight, {
                "date": bar["date"],
                "body_pct": round(bar_body, 1),
                "vol_ratio": round(bar["volume"] / avg5_vol, 1),
                "reason": f"{bar['date']} 放量阳线 实体{bar_body:.1f}% 量比{bar['volume']/avg5_vol:.1f}",
            }
    return 0, {"reason": "近3日无放量阳线"}
