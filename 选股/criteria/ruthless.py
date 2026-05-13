"""
无情操盘手筛选条件

复用 backtest_ruthless.py 的指标函数，每个条件返回 (得分, 详情)
"""

import os
import sys

# 确保可以 import 项目现有脚本
_script_dir = os.path.join(os.path.dirname(__file__), "..", "..", "脚本")
sys.path.insert(0, _script_dir)
from backtest_ruthless import (
    calc_dema, calc_quad_ma, calc_kdj, calc_brick,
    is_death_cross, is_golden_cross,
    detect_b1, detect_b_brick,
)


def build_ruthless_indicators(klines: list[dict], closes: list[float]) -> dict:
    """从K线计算无情操盘手全套指标，供多个条件复用"""
    white = calc_dema(closes, 10)
    yellow = calc_quad_ma(closes)
    k_vals, d_vals, j_vals = calc_kdj(klines, 9, 3, 3)
    bricks, brick_colors = calc_brick(white, yellow)
    return {
        "white": white,
        "yellow": yellow,
        "kdj": {"k": k_vals, "d": d_vals, "j": j_vals},
        "bricks": bricks,
        "brick_colors": brick_colors,
    }


# ═══════════════════════════════════════════════════════════════
# 基础过滤（返回 True=排除）
# ═══════════════════════════════════════════════════════════════

def is_death_cross_now(ind: dict) -> bool:
    """最近端是否处于死叉状态"""
    i = len(ind["white"]) - 1
    return is_death_cross(ind["white"], ind["yellow"], i)


def is_below_yellow(ind: dict, klines: list[dict]) -> bool:
    """最新价是否跌破黄线"""
    i = len(ind["yellow"]) - 1
    if ind["yellow"][i] is None:
        return True  # 数据不足视为危险
    return klines[i]["close"] < ind["yellow"][i]


# ═══════════════════════════════════════════════════════════════
# 正向筛选条件
# ═══════════════════════════════════════════════════════════════

def check_b1_over_sold(ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """B1超卖信号：RSV(J值) < rsv_max 且在黄线上方"""
    rsv_max = params.get("rsv_max", 30)
    j_vals = ind["kdj"]["j"]
    yellow = ind["yellow"]
    i = len(j_vals) - 1
    if yellow[i] is None or j_vals[i] is None:
        return 0, {"reason": "数据不足"}
    # 检查最近5根K线内是否有B1
    found = []
    for offset in range(min(5, len(klines))):
        idx = len(klines) - 1 - offset
        if idx < 0:
            break
        if detect_b1(klines, j_vals, yellow, idx):
            found.append(klines[idx]["date"])
    if found:
        return weight, {"dates": found, "reason": f"B1超卖 {len(found)}次 (最近: {found[0]})", "strength": len(found)}
    # 部分得分：RSV < rsv_max 但未完全触发B1
    if j_vals[-1] < rsv_max:
        return weight // 2, {"rsv": round(j_vals[-1], 1), "reason": f"RSV={j_vals[-1]:.1f}<{rsv_max}(接近B1)"}
    return 0, {"rsv": round(j_vals[-1], 1), "reason": f"RSV={j_vals[-1]:.1f}≥{rsv_max}"}


def check_b_brick_reversal(ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """B砖底部反转：绿柱→过渡→黄砖+B2确认"""
    bricks = ind["bricks"]
    colors = ind["brick_colors"]
    i = len(klines) - 1
    if detect_b_brick(bricks, colors, klines, i):
        return weight, {"date": klines[i]["date"], "reason": "B砖底部反转确认"}
    # 部分得分：正在形成中（黄砖已出现但B2未确认）
    if i > 0 and colors[i] == "yellow" and colors[i - 1] in ("blue", "red"):
        return weight // 2, {"date": klines[i]["date"], "reason": "黄砖出现(待B2确认)"}
    return 0, {"reason": "无B砖信号"}


def check_white_yellow_golden(ind: dict, weight: int, params: dict) -> tuple[int, dict]:
    """白黄线金叉：最近N日内白线上穿黄线"""
    lookback = params.get("lookback_days", 5)
    white = ind["white"]
    yellow = ind["yellow"]
    n = len(white)
    for offset in range(min(lookback, n - 1)):
        idx = n - 1 - offset
        if idx < 1:
            continue
        if is_golden_cross(white, yellow, idx):
            return weight, {"date": f"T-{offset}", "reason": f"{offset}天前白黄线金叉"}
    return 0, {"reason": f"近{lookback}日内无金叉"}


def check_price_structure_strong(ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """价格>白线>黄线（强势排列）"""
    w, y = ind["white"], ind["yellow"]
    i = len(w) - 1
    if any(v[i] is None for v in (w, y)):
        return 0, {"reason": "数据不足"}
    price = klines[i]["close"]
    if price > w[i] > y[i]:
        return weight, {"reason": f"强势: {price:.2f}>{w[i]:.2f}>{y[i]:.2f}"}
    if w[i] > price > y[i]:
        return weight // 2, {"reason": f"夹心: {w[i]:.2f}>{price:.2f}>{y[i]:.2f}"}
    if w[i] > y[i] > price:
        return 0, {"reason": "弱势: 价格跌破黄线"}
    return 0, {"reason": "均线紊乱"}


def check_kdj_bottom_golden(ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """KDJ低位金叉：J<30且K上穿D"""
    k = ind["kdj"]["k"]
    d = ind["kdj"]["d"]
    j = ind["kdj"]["j"]
    n = len(k)
    # 检查最近5根
    for offset in range(min(5, n - 1)):
        idx = n - 1 - offset
        if idx < 1:
            continue
        if j[idx] is not None and j[idx] < 30 and k[idx] > d[idx] and k[idx - 1] <= d[idx - 1]:
            return weight, {"date": klines[idx]["date"], "reason": f"KDJ低位金叉 J={j[idx]:.1f}"}
    # 部分得分：J在低位但未金叉
    if j[-1] is not None and j[-1] < 30:
        return weight // 3, {"j": round(j[-1], 1), "reason": f"J={j[-1]:.1f}低位(待金叉)"}
    return 0, {"j": round(j[-1] or 50, 1), "reason": "无低位金叉信号"}
