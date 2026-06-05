"""
板块主线评分模型

6维度评分（满分100）:
  1. RS相对强度 (25分) — 板块涨幅 vs 大盘涨幅
  2. 行业动量   (20分) — 累计涨幅 + 均线多头排列
  3. 成交额占比 (15分) — 板块成交额占比 + 环比放大
  4. 涨停板占比 (15分) — 涨停家数/总家数
  5. 内部普涨度 (15分) — 上涨家数/总家数
  6. 资金集中度 (10分) — 主力净流入（可选，不可用时权重重分配）

评分函数签名: f(sector, klines, market_klines, weight) -> (score, detail)
"""

from __future__ import annotations

from typing import Optional


def score_rs(sector: dict, klines: list[dict], market_klines: list[dict], weight: int = 25) -> tuple[int, dict]:
    """
    RS相对强度评分: 板块N日涨幅 / 大盘N日涨幅 的比值。

    RS > 1.5 且趋势向上 → 满分
    RS > 1.0 且趋势向上 → 约72%分
    RS > 1.0 趋势走平   → 约48%分
    RS < 1.0             → 按比例递减
    """
    if not klines or not market_klines:
        return 0, {"reason": "无K线数据，无法计算RS"}

    def _calc_return(bars: list[dict], n: int) -> float:
        if len(bars) < n + 1:
            return 0.0
        old_close = bars[-(n + 1)]["close"]
        new_close = bars[-1]["close"]
        if old_close <= 0:
            return 0.0
        return (new_close - old_close) / old_close

    # RS = 超额收益(alpha): 板块N日收益 - 大盘N日收益
    # 避免了比值在大盘下跌时的正负号问题
    rs_values = {}
    for n in [3, 5, 10]:
        sec_ret = _calc_return(klines, n)
        mkt_ret = _calc_return(market_klines, n)
        rs_values[f"rs{n}"] = round(sec_ret - mkt_ret, 4)

    rs5 = rs_values.get("rs5", 0)
    rs3 = rs_values.get("rs3", 0)
    rs10 = rs_values.get("rs10", 0)

    # 趋势: 短期超额 > 长期超额
    trend_up = rs3 > rs5 > rs10 * 0.8

    # 评分: 基于超额收益幅度
    rs5_pct = rs5 * 100  # 转为百分比
    if rs5_pct >= 5 and trend_up:
        score = weight
    elif rs5_pct >= 5:
        score = int(weight * 0.85)
    elif rs5_pct >= 3 and trend_up:
        score = int(weight * 0.72)
    elif rs5_pct >= 3:
        score = int(weight * 0.55)
    elif rs5_pct >= 1:
        score = int(weight * 0.40)
    elif rs5_pct >= 0:
        score = int(weight * 0.20)
    else:
        score = 0

    detail = {
        "rs_values": {k: round(v * 100, 2) for k, v in rs_values.items()},
        "trend_up": trend_up,
        "reason": f"RS5={rs5_pct:+.1f}% RS3={rs3*100:+.1f}% RS10={rs10*100:+.1f}%" + (" 趋势↑" if trend_up else ""),
    }
    return score, detail


def score_momentum(sector: dict, klines: list[dict], market_klines: list[dict], weight: int = 20) -> tuple[int, dict]:
    """
    行业动量评分: 累计涨幅排名 + 均线多头排列。
    """
    pct_chg = sector.get("pct_chg", 0)

    # 从K线计算累计涨幅
    returns = {}
    if klines and len(klines) >= 6:
        for n in [3, 5, 10]:
            if len(klines) >= n + 1:
                old = klines[-(n + 1)]["close"]
                new = klines[-1]["close"]
                if old > 0:
                    returns[f"ret{n}"] = round((new - old) / old * 100, 2)

    # 均线多头排列检查
    ma_bullish = False
    if klines and len(klines) >= 20:
        closes = [k["close"] for k in klines[-20:]]
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes[-10:]) / 10
        ma20 = sum(closes) / 20
        ma_bullish = ma5 > ma10 > ma20

    # 评分
    score = 0

    # 当日涨幅 (max 8分)
    if pct_chg >= 3:
        score += 8
    elif pct_chg >= 2:
        score += 6
    elif pct_chg >= 1:
        score += 4
    elif pct_chg >= 0:
        score += 2

    # 近5日涨幅 (max 8分)
    ret5 = returns.get("ret5", 0)
    if ret5 >= 8:
        score += 8
    elif ret5 >= 5:
        score += 6
    elif ret5 >= 3:
        score += 4
    elif ret5 >= 0:
        score += 2

    # 均线多头排列 (max 4分)
    if ma_bullish:
        score += 4

    score = min(score, weight)
    detail = {
        "pct_chg": pct_chg,
        "returns": returns,
        "ma_bullish": ma_bullish,
        "reason": f"今日{pct_chg:+.2f}%" +
                  (f" 5日{ret5:+.2f}%" if ret5 else "") +
                  (" 均线多头" if ma_bullish else ""),
    }
    return score, detail


def score_activity(sector: dict, klines: list[dict], market_klines: list[dict], weight: int = 15) -> tuple[int, dict]:
    """
    成交额占比/活跃度评分: 成交额环比放大倍数 + 换手率。
    """
    amount = sector.get("amount", 0)
    turnover = sector.get("turnover", 0)

    # 成交额环比放大
    ratio = 0.0
    if klines and len(klines) >= 6:
        recent_avg = sum(k.get("amount", 0) for k in klines[-3:]) / 3
        prev_avg = sum(k.get("amount", 0) for k in klines[-6:-3]) / 3
        if prev_avg > 0:
            ratio = recent_avg / prev_avg

    score = 0

    # 成交额放大 (max 10分)
    if ratio >= 2.0:
        score += 10
    elif ratio >= 1.5:
        score += 8
    elif ratio >= 1.2:
        score += 6
    elif ratio >= 1.0:
        score += 4
    elif ratio > 0:
        score += 2

    # 换手率 (max 5分)
    if turnover >= 3:
        score += 5
    elif turnover >= 2:
        score += 4
    elif turnover >= 1:
        score += 2

    score = min(score, weight)
    detail = {
        "amount_ratio": round(ratio, 2),
        "turnover": turnover,
        "reason": f"成交额比={ratio:.2f}x 换手={turnover:.1f}%",
    }
    return score, detail


def score_limit_up(sector: dict, klines: list[dict], market_klines: list[dict], weight: int = 15) -> tuple[int, dict]:
    """
    涨停板占比评分: 涨停家数/总家数。
    注: 涨停数据来自东方财富 f104/f105 字段的近似统计。
    """
    up_count = sector.get("up_count", 0)
    down_count = sector.get("down_count", 0)
    total = up_count + down_count
    pct_chg = sector.get("pct_chg", 0)

    if total == 0:
        return 0, {"reason": "无涨跌家数数据"}

    # 上涨比例
    up_ratio = up_count / total if total > 0 else 0

    score = 0

    # 上涨家数占比 (max 10分)
    if up_ratio >= 0.8:
        score += 10
    elif up_ratio >= 0.7:
        score += 8
    elif up_ratio >= 0.6:
        score += 6
    elif up_ratio >= 0.5:
        score += 4
    else:
        score += max(0, int(up_ratio * 5))

    # 板块涨幅越高，涨停可能性越大 (max 5分)
    if pct_chg >= 4:
        score += 5
    elif pct_chg >= 3:
        score += 4
    elif pct_chg >= 2:
        score += 3
    elif pct_chg >= 1:
        score += 2

    score = min(score, weight)
    detail = {
        "up_count": up_count,
        "down_count": down_count,
        "up_ratio": round(up_ratio * 100, 1),
        "reason": f"涨{up_count}/跌{down_count} 涨跌比={up_ratio * 100:.0f}%",
    }
    return score, detail


def score_breadth(sector: dict, klines: list[dict], market_klines: list[dict], weight: int = 15) -> tuple[int, dict]:
    """
    内部普涨度评分: 上涨家数/总家数 + 涨跌比。
    与涨停板占比不同，这里关注的是广度而非极端值。
    """
    up_count = sector.get("up_count", 0)
    down_count = sector.get("down_count", 0)
    total = up_count + down_count

    if total == 0:
        return 0, {"reason": "无涨跌家数数据"}

    up_ratio = up_count / total
    # 涨跌比: 涨家数 / max(跌家数, 1)
    up_down_ratio = up_count / max(down_count, 1)

    score = 0

    # 普涨度 (max 10分)
    if up_ratio >= 0.75:
        score += 10
    elif up_ratio >= 0.65:
        score += 8
    elif up_ratio >= 0.55:
        score += 6
    elif up_ratio >= 0.5:
        score += 4
    else:
        score += max(0, int(up_ratio * 6))

    # 涨跌比 (max 5分)
    if up_down_ratio >= 3:
        score += 5
    elif up_down_ratio >= 2:
        score += 4
    elif up_down_ratio >= 1.5:
        score += 3
    elif up_down_ratio >= 1:
        score += 2

    score = min(score, weight)
    detail = {
        "up_ratio": round(up_ratio * 100, 1),
        "up_down_ratio": round(up_down_ratio, 2),
        "reason": f"普涨度={up_ratio * 100:.0f}% 涨跌比={up_down_ratio:.1f}",
    }
    return score, detail


def score_capital_flow(sector: dict, klines: list[dict], market_klines: list[dict], weight: int = 10) -> tuple[int, dict]:
    """
    资金集中度评分: 主力净流入额（东方财富 f62 字段）。
    此字段可能不可用，不可用时返回 0 分但不影响其他维度。
    """
    net_inflow = sector.get("net_inflow", 0)

    if not net_inflow or net_inflow == "-":
        return 0, {"reason": "资金流向数据不可用", "available": False}

    try:
        net_inflow = float(net_inflow)
    except (ValueError, TypeError):
        return 0, {"reason": "资金流向数据异常", "available": False}

    # 净流入额（单位：元），转为亿元
    inflow_yi = net_inflow / 1e8

    score = 0
    if inflow_yi >= 10:
        score = weight
    elif inflow_yi >= 5:
        score = int(weight * 0.8)
    elif inflow_yi >= 2:
        score = int(weight * 0.6)
    elif inflow_yi >= 0:
        score = int(weight * 0.3)
    else:
        score = 0

    detail = {
        "net_inflow_yi": round(inflow_yi, 2),
        "available": True,
        "reason": f"主力净流入 {inflow_yi:+.2f}亿",
    }
    return score, detail


# ═══════════════════════════════════════════════════════════════
# 综合评分
# ═══════════════════════════════════════════════════════════════

CRITERIA = {
    "rs":           {"func": score_rs,          "weight": 25, "desc": "RS相对强度"},
    "momentum":     {"func": score_momentum,    "weight": 20, "desc": "行业动量"},
    "activity":     {"func": score_activity,    "weight": 15, "desc": "成交额活跃度"},
    "limit_up":     {"func": score_limit_up,    "weight": 15, "desc": "涨停板占比"},
    "breadth":      {"func": score_breadth,     "weight": 15, "desc": "内部普涨度"},
    "capital_flow": {"func": score_capital_flow, "weight": 10, "desc": "资金集中度"},
}


def score_sector(sector: dict, klines: list[dict], market_klines: list[dict]) -> dict:
    """
    对单个板块进行综合评分。

    Args:
        sector: 板块数据 dict
        klines: 板块指数K线列表
        market_klines: 大盘(上证)K线列表

    Returns:
        {
            "name": str,
            "total_score": int,
            "details": [{"criterion": str, "desc": str, "score": int, "weight": int, "detail": dict}, ...],
            "category": "mainline" | "potential" | "neutral",
        }
    """
    total = 0
    details = []
    capital_available = True

    for key, cfg in CRITERIA.items():
        func = cfg["func"]
        weight = cfg["weight"]
        score, detail = func(sector, klines, market_klines, weight)
        if key == "capital_flow" and not detail.get("available", True):
            capital_available = False
        total += score
        details.append({
            "criterion": key,
            "desc": cfg["desc"],
            "score": score,
            "weight": weight,
            "detail": detail,
        })

    # 资金流向不可用时，将10分重分配给RS和动量
    if not capital_available:
        bonus_rs = 5
        bonus_mom = 5
        total += bonus_rs + bonus_mom
        for d in details:
            if d["criterion"] == "rs":
                d["score"] += bonus_rs
                d["weight"] += bonus_rs
                d["detail"]["reason"] += f" (+{bonus_rs}资金重分配)"
            elif d["criterion"] == "momentum":
                d["score"] += bonus_mom
                d["weight"] += bonus_mom
                d["detail"]["reason"] += f" (+{bonus_mom}资金重分配)"

    # 分类：根据数据可用性动态调整阈值
    # EM不可用时（limit_up和breadth均为0），降低阈值以适配仅RS+动量的评分范围
    rs_score = next((d["score"] for d in details if d["criterion"] == "rs"), 0)
    act_score = next((d["score"] for d in details if d["criterion"] == "activity"), 0)
    limit_up_score = next((d["score"] for d in details if d["criterion"] == "limit_up"), 0)

    em_available = limit_up_score > 0  # EM数据可用时limit_up有得分

    if em_available:
        # EM数据完整：使用原始阈值
        if total >= 70 and rs_score >= 18:
            category = "mainline"
        elif total >= 50 and act_score >= 10:
            category = "potential"
        else:
            category = "neutral"
    else:
        # EM不可用：仅RS+动量+活跃度，最高约75分，降低阈值
        if total >= 55 and rs_score >= 18:
            category = "mainline"
        elif total >= 40:
            category = "potential"
        else:
            category = "neutral"

    return {
        "name": sector.get("name", ""),
        "code": sector.get("code", ""),
        "index_code": sector.get("index_code", ""),
        "pct_chg": sector.get("pct_chg", 0),
        "total_score": total,
        "details": details,
        "category": category,
        "up_count": sector.get("up_count", 0),
        "down_count": sector.get("down_count", 0),
        "amount": sector.get("amount", 0),
        "net_inflow": sector.get("net_inflow", 0),
    }
