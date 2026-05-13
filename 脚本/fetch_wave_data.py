#!/usr/bin/env python3
"""
波浪理论分析 - 数据抓取与指标计算
用法:
    python3 fetch_wave_data.py 600519          # 默认日K 250根
    python3 fetch_wave_data.py 000001 500      # 指定根数
    python3 fetch_wave_data.py 600519 250 week # 周K线
数据来源: 东方财富(主) / 新浪财经(备)
"""

import sys
import json
import os
import math
import socket as _sock
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 强制 IPv4：东方财富 CDN 的 IPv6 节点不回 HTTP 响应
_orig_getaddrinfo = _sock.getaddrinfo
def _getaddrinfo_v4(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, _sock.AF_INET, type, proto, flags)
_sock.getaddrinfo = _getaddrinfo_v4

# ═══════════════════════════════════════════════════════════════
# 第一部分: K线数据抓取（复用 ruthless-trader 逻辑）
# ═══════════════════════════════════════════════════════════════

def get_market(code: str) -> str:
    if code.startswith("6"):
        return "1"
    elif code.startswith(("0", "3")):
        return "0"
    elif code.startswith(("4", "8")):
        return "0"
    return "0"


def _fetch_eastmoney(code: str, count: int, klt: str = "101"):
    """东方财富K线接口  klt: 101=日K 102=周K 103=月K"""
    market = get_market(code)
    secid = f"{market}.{code}"
    url = "http://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": klt,
        "fqt": "1",
        "lmt": str(count),
        "end": "20500101",
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.eastmoney.com/",
    }
    for scheme in ("http", "https"):
        try:
            u = url.replace("http://", f"{scheme}://", 1)
            resp = requests.get(u, params=params, headers=headers, timeout=20, verify=False)
            if resp.status_code == 200 and len(resp.text) > 50:
                break
        except Exception:
            continue
    else:
        return None

    data = resp.json()
    klines_raw = data.get("data", {})
    if not klines_raw:
        return None

    name = klines_raw.get("name", code)
    klines = klines_raw.get("klines", [])
    result = []
    for k in klines:
        p = k.split(",")
        result.append({
            "date":      p[0],
            "open":      float(p[1]),
            "close":     float(p[2]),
            "high":      float(p[3]),
            "low":       float(p[4]),
            "volume":    float(p[5]),
            "amount":    float(p[6]),
            "amplitude": float(p[7]),
            "pct_chg":   float(p[8]),
            "change":    float(p[9]),
            "turnover":  float(p[10]),
        })
    return name, result


def _fetch_sina(code: str, count: int):
    """新浪财经K线接口（备用，仅日K）"""
    prefix = "sh" if code.startswith("6") else "sz"
    symbol = f"{prefix}{code}"
    url = ("https://money.finance.sina.com.cn/quotes_service/"
           "api/json_v2.php/CN_MarketData.getKLineData")
    params = {"symbol": symbol, "scale": 240, "ma": "no", "datalen": count}
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.sina.com.cn/",
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=20, verify=False)
        if resp.status_code != 200 or len(resp.text) < 50:
            return None
        data = resp.json()
    except Exception:
        return None
    if not data:
        return None

    result = []
    prev_close = None
    for k in data:
        o, c, h, l, v = float(k["open"]), float(k["close"]), float(k["high"]), float(k["low"]), float(k["volume"])
        avg_price = (o + c + h + l) / 4
        amount = v * avg_price
        pct_chg = ((c - prev_close) / prev_close * 100) if prev_close else 0
        change = (c - prev_close) if prev_close else 0
        amplitude = ((h - l) / prev_close * 100) if prev_close else 0
        prev_close = c
        result.append({
            "date": k["day"], "open": o, "close": c, "high": h, "low": l,
            "volume": v, "amount": amount, "amplitude": amplitude,
            "pct_chg": pct_chg, "change": change, "turnover": 0,
        })
    # 股票名称
    try:
        url2 = f"https://hq.sinajs.cn/list={symbol}"
        resp2 = requests.get(url2, headers={"Referer": "https://finance.sina.com.cn/"}, timeout=5, verify=False)
        if resp2.status_code == 200 and '="' in resp2.text:
            name = resp2.text.split('="')[1].split(",")[0]
        else:
            name = code
    except Exception:
        name = code
    return name, result


def fetch_kline(code: str, count: int = 250, period: str = "day"):
    """
    抓取K线数据
    period: "day"=日K, "week"=周K, "month"=月K
    """
    klt_map = {"day": "101", "week": "102", "month": "103"}
    klt = klt_map.get(period, "101")

    result = _fetch_eastmoney(code, count, klt)
    if result:
        return result

    if period == "day":
        result = _fetch_sina(code, count)
        if result:
            return result

    raise ConnectionError(f"K线数据获取失败，代码: {code}, 周期: {period}")


# ═══════════════════════════════════════════════════════════════
# 第二部分: 波浪分析所需技术指标计算
# ═══════════════════════════════════════════════════════════════

def calc_ema(values: list, period: int) -> list:
    """指数移动平均线"""
    ema = [0.0] * len(values)
    if not values:
        return ema
    k = 2 / (period + 1)
    ema[0] = values[0]
    for i in range(1, len(values)):
        ema[i] = values[i] * k + ema[i - 1] * (1 - k)
    return ema


def calc_macd(closes: list, fast: int = 5, slow: int = 34, signal: int = 5) -> dict:
    """
    MACD指标（参数5,34,5）
    返回: {"dif": [...], "dea": [...], "histogram": [...]}
    """
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    dif = [ema_fast[i] - ema_slow[i] for i in range(len(closes))]
    dea = calc_ema(dif, signal)
    histogram = [(dif[i] - dea[i]) * 2 for i in range(len(closes))]
    return {"dif": dif, "dea": dea, "histogram": histogram}


def calc_sma(values: list, period: int) -> list:
    """简单移动平均线"""
    sma = [0.0] * len(values)
    for i in range(len(values)):
        if i < period - 1:
            sma[i] = sum(values[:i + 1]) / (i + 1)
        else:
            sma[i] = sum(values[i - period + 1: i + 1]) / period
    return sma


def calc_bollinger(closes: list, period: int = 20, num_std: float = 2.0) -> dict:
    """布林带"""
    mid = calc_sma(closes, period)
    upper = [0.0] * len(closes)
    lower = [0.0] * len(closes)
    for i in range(len(closes)):
        start = max(0, i - period + 1)
        window = closes[start: i + 1]
        if len(window) < 2:
            std = 0
        else:
            mean = sum(window) / len(window)
            std = math.sqrt(sum((x - mean) ** 2 for x in window) / len(window))
        upper[i] = mid[i] + num_std * std
        lower[i] = mid[i] - num_std * std
    return {"upper": upper, "mid": mid, "lower": lower}


def calc_atr(klines: list, period: int = 14) -> list:
    """平均真实波幅（ATR）"""
    tr_list = []
    for i, k in enumerate(klines):
        h, l, c_prev = k["high"], k["low"], klines[i - 1]["close"] if i > 0 else k["open"]
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        tr_list.append(tr)
    return calc_ema(tr_list, period)


def find_swing_points(klines: list, left: int = 5, right: int = 5) -> dict:
    """
    寻找摆动高低点（波浪的拐点基础）
    left/right: 左右各需几根K线确认
    返回: {"highs": [(index, price, date), ...], "lows": [(index, price, date), ...]}
    """
    highs, lows = [], []
    for i in range(left, len(klines) - right):
        h = klines[i]["high"]
        l = klines[i]["low"]
        is_high = all(h >= klines[j]["high"] for j in range(i - left, i + right + 1) if j != i)
        is_low = all(l <= klines[j]["low"] for j in range(i - left, i + right + 1) if j != i)
        if is_high:
            highs.append((i, h, klines[i]["date"]))
        if is_low:
            lows.append((i, l, klines[i]["date"]))
    return {"highs": highs, "lows": lows}


def calc_fibonacci_retracement(high: float, low: float, direction: str = "up") -> dict:
    """
    斐波那契回撤位
    direction: "up"=上涨后回撤(从高点向下算), "down"=下跌后反弹(从低点向上算)
    """
    diff = high - low
    levels = {
        "0.0%": 0, "23.6%": 0.236, "38.2%": 0.382,
        "50.0%": 0.5, "61.8%": 0.618, "78.6%": 0.786, "100.0%": 1.0,
    }
    result = {}
    for label, ratio in levels.items():
        if direction == "up":
            result[label] = high - diff * ratio
        else:
            result[label] = low + diff * ratio
    return result


def calc_fibonacci_extension(wave1_start: float, wave1_end: float, wave2_end: float) -> dict:
    """
    斐波那契扩展位（用于推算浪3/浪5目标位）
    wave1_start: 浪1起点, wave1_end: 浪1终点, wave2_end: 浪2终点
    """
    wave1_len = abs(wave1_end - wave1_start)
    direction = 1 if wave1_end > wave1_start else -1
    ratios = {
        "0.618": 0.618, "1.000": 1.0, "1.382": 1.382,
        "1.618": 1.618, "2.000": 2.0, "2.618": 2.618,
    }
    result = {}
    for label, ratio in ratios.items():
        result[label] = wave2_end + direction * wave1_len * ratio
    return result


def calc_volume_profile(klines: list, recent_n: int = 20) -> dict:
    """
    成交量分析（近N根）
    返回: 平均量、量比趋势、缩量/放量判断
    """
    if len(klines) < recent_n + 5:
        return {}
    recent = klines[-recent_n:]
    prior = klines[-(recent_n + 20):-recent_n] if len(klines) >= recent_n + 20 else klines[:len(klines) - recent_n]
    avg_recent = sum(k["volume"] for k in recent) / len(recent)
    avg_prior = sum(k["volume"] for k in prior) / len(prior) if prior else avg_recent

    # 逐根量比序列
    vol_ratios = []
    for i in range(len(klines)):
        if i < 5:
            vol_ratios.append(1.0)
        else:
            avg5 = sum(klines[j]["volume"] for j in range(i - 5, i)) / 5
            vol_ratios.append(klines[i]["volume"] / avg5 if avg5 > 0 else 1.0)

    return {
        "avg_recent_vol": round(avg_recent),
        "avg_prior_vol": round(avg_prior),
        "vol_trend": "放量" if avg_recent > avg_prior * 1.2 else ("缩量" if avg_recent < avg_prior * 0.8 else "平量"),
        "vol_ratios": vol_ratios,
    }


def detect_macd_divergence(klines: list, macd: dict, swing_points: dict) -> list:
    """
    检测MACD顶/底背离
    返回: [{"type": "顶背离"/"底背离", "point1": ..., "point2": ..., "desc": ...}, ...]
    """
    divergences = []
    dif = macd["dif"]

    # 顶背离: 价格高点抬高，DIF高点降低
    highs = swing_points["highs"]
    for i in range(1, len(highs)):
        idx1, price1, date1 = highs[i - 1]
        idx2, price2, date2 = highs[i]
        if price2 > price1 and dif[idx2] < dif[idx1]:
            divergences.append({
                "type": "顶背离",
                "point1": {"index": idx1, "date": date1, "price": price1, "dif": round(dif[idx1], 4)},
                "point2": {"index": idx2, "date": date2, "price": price2, "dif": round(dif[idx2], 4)},
                "desc": f"价格 {price1}→{price2}(↑) DIF {dif[idx1]:.4f}→{dif[idx2]:.4f}(↓)",
            })

    # 底背离: 价格低点降低，DIF低点抬高
    lows = swing_points["lows"]
    for i in range(1, len(lows)):
        idx1, price1, date1 = lows[i - 1]
        idx2, price2, date2 = lows[i]
        if price2 < price1 and dif[idx2] > dif[idx1]:
            divergences.append({
                "type": "底背离",
                "point1": {"index": idx1, "date": date1, "price": price1, "dif": round(dif[idx1], 4)},
                "point2": {"index": idx2, "date": date2, "price": price2, "dif": round(dif[idx2], 4)},
                "desc": f"价格 {price1}→{price2}(↓) DIF {dif[idx1]:.4f}→{dif[idx2]:.4f}(↑)",
            })

    return divergences


# ═══════════════════════════════════════════════════════════════
# 第三部分: 汇总输出
# ═══════════════════════════════════════════════════════════════

def analyze(code: str, count: int = 250, period: str = "day"):
    """抓取数据 + 计算全部波浪分析指标，返回完整分析数据"""
    # 多取预热K线，让 EMA/MACD 等指标充分收敛，与行情软件数值一致
    warmup = 200
    name, all_klines = fetch_kline(code, count + warmup, period)
    actual_warmup = max(0, len(all_klines) - count)

    all_closes = [k["close"] for k in all_klines]

    # ── 在全量数据（含预热段）上计算需要 EMA 收敛的指标 ──

    # MACD（5,34,5 用于背离检测）
    macd_full = calc_macd(all_closes, 5, 34, 5)

    # 标准MACD（12,26,9 作为对照）
    macd_std_full = calc_macd(all_closes, 12, 26, 9)

    # 均线系统
    ma5_full = calc_sma(all_closes, 5)
    ma10_full = calc_sma(all_closes, 10)
    ma20_full = calc_sma(all_closes, 20)
    ma60_full = calc_sma(all_closes, 60)
    ma120_full = calc_sma(all_closes, 120)

    # 布林带
    boll_full = calc_bollinger(all_closes, 20, 2)

    # ATR
    atr_full = calc_atr(all_klines, 14)

    # ── 裁剪预热段，只保留用户请求的根数 ──
    w = actual_warmup
    klines = all_klines[w:]
    closes = all_closes[w:]

    macd = {k: v[w:] for k, v in macd_full.items()}
    macd_std = {k: v[w:] for k, v in macd_std_full.items()}
    ma5 = ma5_full[w:]
    ma10 = ma10_full[w:]
    ma20 = ma20_full[w:]
    ma60 = ma60_full[w:]
    ma120 = ma120_full[w:]
    boll = {k: v[w:] for k, v in boll_full.items()}
    atr = atr_full[w:]

    # ── 以下指标仅依赖局部窗口，在裁剪后数据上计算 ──

    # 摆动高低点
    swing = find_swing_points(klines, left=5, right=5)
    swing_fine = find_swing_points(klines, left=3, right=3)

    # MACD背离检测
    divergences = detect_macd_divergence(klines, macd, swing)

    # 成交量分析
    vol_profile = calc_volume_profile(klines, 20)

    # 斐波那契回撤（基于最近一波主要走势）
    fib_retracement = {}
    if swing["highs"] and swing["lows"]:
        last_high = max(swing["highs"], key=lambda x: x[1])
        last_low = min(swing["lows"][-5:], key=lambda x: x[1]) if len(swing["lows"]) >= 5 else min(swing["lows"], key=lambda x: x[1])
        # 判断当前是从高点回落还是从低点反弹
        if last_high[0] > last_low[0]:
            fib_retracement = {
                "direction": "上涨后回撤",
                "high": {"price": last_high[1], "date": last_high[2]},
                "low": {"price": last_low[1], "date": last_low[2]},
                "levels": calc_fibonacci_retracement(last_high[1], last_low[1], "up"),
            }
        else:
            fib_retracement = {
                "direction": "下跌后反弹",
                "high": {"price": last_high[1], "date": last_high[2]},
                "low": {"price": last_low[1], "date": last_low[2]},
                "levels": calc_fibonacci_retracement(last_high[1], last_low[1], "down"),
            }

    # 将指标写入每根K线
    for i, k in enumerate(klines):
        k["ma5"] = round(ma5[i], 3)
        k["ma10"] = round(ma10[i], 3)
        k["ma20"] = round(ma20[i], 3)
        k["ma60"] = round(ma60[i], 3)
        k["ma120"] = round(ma120[i], 3)
        k["macd_dif"] = round(macd["dif"][i], 4)
        k["macd_dea"] = round(macd["dea"][i], 4)
        k["macd_hist"] = round(macd["histogram"][i], 4)
        k["macd_std_dif"] = round(macd_std["dif"][i], 4)
        k["macd_std_dea"] = round(macd_std["dea"][i], 4)
        k["macd_std_hist"] = round(macd_std["histogram"][i], 4)
        k["boll_upper"] = round(boll["upper"][i], 3)
        k["boll_mid"] = round(boll["mid"][i], 3)
        k["boll_lower"] = round(boll["lower"][i], 3)
        k["atr"] = round(atr[i], 3)
        if vol_profile.get("vol_ratios"):
            k["vol_ratio"] = round(vol_profile["vol_ratios"][i], 2)

    return {
        "code": code,
        "name": name,
        "period": period,
        "count": len(klines),
        "klines": klines,
        "swing_points": {
            "highs": [{"index": h[0], "price": h[1], "date": h[2]} for h in swing["highs"]],
            "lows": [{"index": l[0], "price": l[1], "date": l[2]} for l in swing["lows"]],
        },
        "swing_points_fine": {
            "highs": [{"index": h[0], "price": h[1], "date": h[2]} for h in swing_fine["highs"]],
            "lows": [{"index": l[0], "price": l[1], "date": l[2]} for l in swing_fine["lows"]],
        },
        "divergences": divergences,
        "volume_profile": {k: v for k, v in vol_profile.items() if k != "vol_ratios"},
        "fibonacci": fib_retracement,
        "latest": {
            "date": klines[-1]["date"],
            "close": klines[-1]["close"],
            "macd_dif": round(macd["dif"][-1], 4),
            "macd_dea": round(macd["dea"][-1], 4),
            "macd_hist": round(macd["histogram"][-1], 4),
            "atr": round(atr[-1], 3),
            "vol_trend": vol_profile.get("vol_trend", ""),
        },
    }


def print_summary(data: dict):
    """打印分析摘要"""
    name, code = data["name"], data["code"]
    period_name = {"day": "日K", "week": "周K", "month": "月K"}.get(data["period"], "日K")
    klines = data["klines"]
    latest = data["latest"]

    print(f"\n{'═' * 72}")
    print(f"  {name}（{code}）波浪分析数据  {period_name} × {data['count']}根")
    print(f"{'═' * 72}")

    # 最新行情
    k = klines[-1]
    sign = "+" if k["pct_chg"] > 0 else ""
    print(f"\n【最新行情】{k['date']}")
    print(f"  开:{k['open']:.2f}  高:{k['high']:.2f}  低:{k['low']:.2f}  收:{k['close']:.2f}  {sign}{k['pct_chg']:.2f}%")
    print(f"  成交量:{k['volume']:,.0f}手  成交额:{k['amount']/1e8:.2f}亿")

    # MACD
    print(f"\n【MACD(5,34,5)】")
    print(f"  DIF: {latest['macd_dif']:.4f}  DEA: {latest['macd_dea']:.4f}  柱: {latest['macd_hist']:.4f}")
    state = "多头" if latest["macd_dif"] > 0 else "空头"
    cross = "金叉" if latest["macd_dif"] > latest["macd_dea"] else "死叉"
    print(f"  状态: {state} / {cross}")

    # 均线
    print(f"\n【均线】")
    print(f"  MA5:{k['ma5']:.2f}  MA10:{k['ma10']:.2f}  MA20:{k['ma20']:.2f}  MA60:{k['ma60']:.2f}  MA120:{k['ma120']:.2f}")
    ma_arr = "多头排列" if k["ma5"] > k["ma10"] > k["ma20"] > k["ma60"] else (
        "空头排列" if k["ma5"] < k["ma10"] < k["ma20"] < k["ma60"] else "交叉缠绕")
    print(f"  排列: {ma_arr}")

    # 成交量
    vp = data["volume_profile"]
    if vp:
        print(f"\n【成交量】")
        print(f"  近20日均量:{vp.get('avg_recent_vol',0):,}手  前期均量:{vp.get('avg_prior_vol',0):,}手  趋势:{vp.get('vol_trend','')}")

    # 摆动点
    sp = data["swing_points"]
    print(f"\n【关键高低点(left=5,right=5)】")
    if sp["highs"]:
        recent_highs = sp["highs"][-5:]
        highs_str = ', '.join(h['date']+'='+f"{h['price']:.2f}" for h in recent_highs)
        print(f'  近期高点: {highs_str}')
    if sp["lows"]:
        recent_lows = sp["lows"][-5:]
        lows_str = ', '.join(l['date']+'='+f"{l['price']:.2f}" for l in recent_lows)
        print(f'  近期低点: {lows_str}')

    # 背离
    divs = data["divergences"]
    if divs:
        print(f"\n【MACD背离信号】共{len(divs)}个")
        for d in divs[-5:]:
            print(f"  {d['type']}: {d['desc']}")

    # 斐波那契
    fib = data["fibonacci"]
    if fib:
        print(f"\n【斐波那契回撤】{fib['direction']}")
        print(f"  高点: {fib['high']['date']} = {fib['high']['price']:.2f}")
        print(f"  低点: {fib['low']['date']} = {fib['low']['price']:.2f}")
        for label, price in fib["levels"].items():
            marker = " ◄ 当前" if abs(price - k["close"]) / k["close"] < 0.01 else ""
            print(f"  {label:>6}: {price:.2f}{marker}")

    # ATR
    print(f"\n【波动率】ATR(14): {latest['atr']:.2f}  (占价格{latest['atr']/k['close']*100:.2f}%)")

    # 最近K线
    print(f"\n【最近20根K线】")
    print(f"  {'日期':<12} {'开盘':>7} {'最高':>7} {'最低':>7} {'收盘':>7} {'涨跌%':>7} {'量(万手)':>9} {'MACD柱':>8}")
    print(f"  {'-'*68}")
    for k in klines[-20:]:
        sign = "+" if k["pct_chg"] > 0 else ""
        bar = "+" if k["macd_hist"] > 0 else "-"
        print(f"  {k['date']:<12} {k['open']:>7.2f} {k['high']:>7.2f} {k['low']:>7.2f} {k['close']:>7.2f} "
              f"{sign}{k['pct_chg']:>6.2f}% {k['volume']/10000:>8.1f} {bar}{abs(k['macd_hist']):>7.4f}")

    print(f"\n{'═' * 72}\n")


def save_json(data: dict, output_dir: str = None):
    """保存完整数据为JSON"""
    if output_dir is None:
        output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(output_dir, exist_ok=True)
    filename = f"{data['code']}_{data['period']}_wave.json"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"数据已保存: {filepath}")
    return filepath


def main():
    if len(sys.argv) < 2:
        print("用法: python3 fetch_wave_data.py <股票代码> [根数] [day/week/month]")
        print("示例: python3 fetch_wave_data.py 600519")
        print("      python3 fetch_wave_data.py 000001 500")
        print("      python3 fetch_wave_data.py 600519 120 week")
        sys.exit(1)

    code = sys.argv[1].strip()
    count = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 250
    period = sys.argv[-1] if sys.argv[-1] in ("day", "week", "month") else "day"

    print(f"正在抓取 {code} {period}K × {count}根...")
    data = analyze(code, count, period)
    print_summary(data)
    save_json(data)


if __name__ == "__main__":
    main()
