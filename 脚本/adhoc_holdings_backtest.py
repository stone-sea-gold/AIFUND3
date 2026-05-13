#!/usr/bin/env python3
"""
6 只持仓股回测 · 白黄线过滤器效果对比
策略对比:
  1. 手册 Wave+MACD       (Fib + MACD金叉 + 放量1.3x)
  2. 手册 + 白>黄          (+ 白线 DEMA10 > 黄线 quad-MA 前置)
  3. Pure MACD            (MACD金叉进/死叉出)
  4. Pure MACD + 白>黄     (+ 白线 > 黄线 前置)

区间: 最近 500 根日K（约 2 年）
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from fetch_wave_data import fetch_kline, calc_macd, calc_ema, calc_sma

HOLDINGS = [
    ("002452", "长高电新"),
    ("600742", "富维股份"),
    ("000422", "湖北宜化"),
    ("600744", "华银电力"),
    ("002498", "汉缆股份"),
    ("002202", "金风科技"),
    ("600522", "中天科技"),
]

BARS = 500
LOOKBACK_START = 120  # warmup for Fib zone detection


def calc_vol_avg(klines, n=5):
    out = [0.0] * len(klines)
    for i in range(len(klines)):
        s = max(0, i - n)
        w = klines[s:i] if i > 0 else [klines[0]]
        out[i] = sum(k["volume"] for k in w) / max(len(w), 1)
    return out


def calc_dema(closes, period=10):
    ema1 = calc_ema(closes, period)
    return calc_ema(ema1, period)


def calc_quad_ma(closes):
    ma14 = calc_sma(closes, 14)
    ma28 = calc_sma(closes, 28)
    ma57 = calc_sma(closes, 57)
    ma114 = calc_sma(closes, 114)
    return [(ma14[i] + ma28[i] + ma57[i] + ma114[i]) / 4 for i in range(len(closes))]


def find_fib_zone(klines, i, lookback=120, min_range_pct=0.15):
    start = max(0, i - lookback)
    window = klines[start:i + 1]
    if len(window) < 20:
        return None
    high_idx = max(range(len(window)), key=lambda x: window[x]["high"])
    high_price = window[high_idx]["high"]
    if high_idx < 5:
        return None
    pre = window[:high_idx + 1]
    low_idx = min(range(len(pre)), key=lambda x: pre[x]["low"])
    low_price = pre[low_idx]["low"]
    if high_price <= low_price:
        return None
    r = high_price - low_price
    if r / low_price < min_range_pct:
        return None
    if high_idx == len(window) - 1:
        return None
    return {
        "high": high_price, "low": low_price,
        "fib_382": high_price - r * 0.382,
        "fib_618": high_price - r * 0.618,
    }


def run_strategy(klines, use_fib, use_vol, use_wy):
    """单一策略回测，返回 (trades, filtered_wy_count)"""
    trades = []
    position = 0
    ep = None
    ed = None
    filtered_by_wy = 0

    for i in range(LOOKBACK_START, len(klines)):
        k = klines[i]
        prev = klines[i - 1]
        macd_gold = k["hist"] > 0 and prev["hist"] <= 0
        macd_death = k["hist"] < 0 and prev["hist"] >= 0
        vol_ok = (not use_vol) or (k["volume"] >= k["vol5"] * 1.3)
        fib = find_fib_zone(klines, i, 120) if use_fib else None
        fib_ok = (not use_fib) or (fib and fib["fib_618"] <= k["close"] <= fib["fib_382"])
        wy_bull = k["white"] > k["yellow"]
        wy_ok = (not use_wy) or wy_bull

        # 白黄线死叉：任意时刻持仓 → 立即清仓（仅 use_wy=True 时生效）
        if use_wy and position == 1:
            wy_prev = prev["white"] > prev["yellow"]
            if wy_prev and not wy_bull:
                pnl = (k["close"] - ep) / ep * 100
                trades.append({"entry": ed, "exit": k["date"], "ep": ep,
                               "xp": k["close"], "pnl": pnl, "reason": "白黄线死叉"})
                position = 0
                ep = None
                continue

        if position == 0:
            # 统计因白>黄被过滤的金叉次数
            if use_wy and macd_gold and vol_ok and fib_ok and not wy_bull:
                filtered_by_wy += 1
            if macd_gold and vol_ok and fib_ok and wy_ok:
                position = 1
                ep = k["close"]
                ed = k["date"]
        elif position == 1 and macd_death:
            pnl = (k["close"] - ep) / ep * 100
            trades.append({"entry": ed, "exit": k["date"], "ep": ep,
                           "xp": k["close"], "pnl": pnl, "reason": "MACD死叉"})
            position = 0
            ep = None

    if position == 1:
        k = klines[-1]
        pnl = (k["close"] - ep) / ep * 100
        trades.append({"entry": ed, "exit": k["date"], "ep": ep,
                       "xp": k["close"], "pnl": pnl, "reason": "期末平仓"})

    return trades, filtered_by_wy


def stats(trades):
    if not trades:
        return {"count": 0, "wr": 0, "total": 0, "avg": 0, "best": 0, "worst": 0}
    wins = sum(1 for t in trades if t["pnl"] > 0)
    total = sum(t["pnl"] for t in trades)
    return {
        "count": len(trades),
        "wr": wins / len(trades) * 100,
        "total": total,
        "avg": total / len(trades),
        "best": max(t["pnl"] for t in trades),
        "worst": min(t["pnl"] for t in trades),
    }


def analyze_stock(code, short_name):
    print(f"\n{'═' * 80}")
    print(f"  {short_name}（{code}）")
    print(f"{'═' * 80}")

    # 额外 200 根用于 MACD/MA 预热
    warmup = 200
    _, all_klines = fetch_kline(code, BARS + warmup)
    actual_warmup = max(0, len(all_klines) - BARS)
    all_closes = [k["close"] for k in all_klines]

    macd_full = calc_macd(all_closes, 5, 34, 5)
    white_full = calc_dema(all_closes, 10)
    yellow_full = calc_quad_ma(all_closes)

    w = actual_warmup
    klines = all_klines[w:]
    for i, k in enumerate(klines):
        k["dif"] = macd_full["dif"][w + i]
        k["dea"] = macd_full["dea"][w + i]
        k["hist"] = macd_full["histogram"][w + i]
        k["white"] = white_full[w + i]
        k["yellow"] = yellow_full[w + i]

    vol5 = calc_vol_avg(klines)
    for i, k in enumerate(klines):
        k["vol5"] = vol5[i]

    print(f"  区间: {klines[LOOKBACK_START]['date']} → {klines[-1]['date']}  "
          f"共 {len(klines) - LOOKBACK_START} 根")

    # 白>黄 / 白<黄 比例
    wy_bull_count = sum(1 for k in klines[LOOKBACK_START:] if k["white"] > k["yellow"])
    wy_bear_count = len(klines) - LOOKBACK_START - wy_bull_count
    print(f"  白>黄 占比: {wy_bull_count}/{len(klines) - LOOKBACK_START} "
          f"({wy_bull_count/(len(klines) - LOOKBACK_START)*100:.1f}%)"
          f"   白<黄 占比: {wy_bear_count}/{len(klines) - LOOKBACK_START} "
          f"({wy_bear_count/(len(klines) - LOOKBACK_START)*100:.1f}%)")

    # 四路回测
    hb, _ = run_strategy(klines, True, True, False)
    hb_wy, hb_filt = run_strategy(klines, True, True, True)
    pm, _ = run_strategy(klines, False, False, False)
    pm_wy, pm_filt = run_strategy(klines, False, False, True)

    print(f"\n  {'策略':<22} {'笔数':>4} {'胜率':>8} {'累计':>10} {'均单':>9} "
          f"{'最好':>9} {'最差':>9}  过滤")
    print(f"  {'-' * 80}")
    for label, tr, filt in [
        ("手册 Wave+MACD", hb, 0),
        ("手册 + 白>黄", hb_wy, hb_filt),
        ("Pure MACD", pm, 0),
        ("Pure MACD + 白>黄", pm_wy, pm_filt),
    ]:
        s = stats(tr)
        print(f"  {label:<22} {s['count']:>4} "
              f"{s['wr']:>6.1f}% {s['total']:>+9.2f}% {s['avg']:>+8.2f}% "
              f"{s['best']:>+8.2f}% {s['worst']:>+8.2f}%  "
              f"{'剔除 ' + str(filt) + ' 金叉' if filt else '—'}")

    return {
        "code": code, "name": short_name,
        "wy_bull_pct": wy_bull_count / (len(klines) - LOOKBACK_START) * 100,
        "hb": stats(hb), "hb_wy": stats(hb_wy), "hb_filt": hb_filt,
        "pm": stats(pm), "pm_wy": stats(pm_wy), "pm_filt": pm_filt,
    }


def main():
    print(f"\n{'█' * 80}")
    print(f"  6 只持仓股 · 白黄线过滤器效果对比（{BARS} 根日K）")
    print(f"{'█' * 80}")

    results = []
    for code, name in HOLDINGS:
        try:
            r = analyze_stock(code, name)
            results.append(r)
        except Exception as e:
            print(f"\n  ❌ {name}({code}) 回测失败: {e}")

    # 汇总表
    print(f"\n\n{'█' * 80}")
    print(f"  汇总：白>黄 过滤器对两种策略的影响")
    print(f"{'█' * 80}")
    print(f"  {'股票':<12} {'白>黄占比':>8} {'手册':>10} {'手册+白>黄':>12} "
          f"{'差':>7} {'Pure':>10} {'Pure+白>黄':>12} {'差':>7}")
    print(f"  {'-' * 80}")
    for r in results:
        hb_delta = r["hb_wy"]["total"] - r["hb"]["total"]
        pm_delta = r["pm_wy"]["total"] - r["pm"]["total"]
        hb_mark = "↑" if hb_delta > 0 else ("↓" if hb_delta < 0 else "—")
        pm_mark = "↑" if pm_delta > 0 else ("↓" if pm_delta < 0 else "—")
        print(f"  {r['name']:<12} {r['wy_bull_pct']:>6.1f}% "
              f"{r['hb']['total']:>+8.2f}% {r['hb_wy']['total']:>+10.2f}% "
              f"{hb_delta:>+5.2f}{hb_mark}  "
              f"{r['pm']['total']:>+8.2f}% {r['pm_wy']['total']:>+10.2f}% "
              f"{pm_delta:>+5.2f}{pm_mark}")

    print(f"\n  {'─' * 80}")
    hb_sum_no = sum(r["hb"]["total"] for r in results)
    hb_sum_wy = sum(r["hb_wy"]["total"] for r in results)
    pm_sum_no = sum(r["pm"]["total"] for r in results)
    pm_sum_wy = sum(r["pm_wy"]["total"] for r in results)
    hb_filt_sum = sum(r["hb_filt"] for r in results)
    pm_filt_sum = sum(r["pm_filt"] for r in results)
    print(f"  6 票加总   手册: {hb_sum_no:+.2f}% → 加白>黄 {hb_sum_wy:+.2f}%  "
          f"(Δ {hb_sum_wy - hb_sum_no:+.2f}pp, 共剔除 {hb_filt_sum} 次金叉)")
    print(f"             Pure: {pm_sum_no:+.2f}% → 加白>黄 {pm_sum_wy:+.2f}%  "
          f"(Δ {pm_sum_wy - pm_sum_no:+.2f}pp, 共剔除 {pm_filt_sum} 次金叉)")


if __name__ == "__main__":
    main()
