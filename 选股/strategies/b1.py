"""
B1 量价共振选股策略（通达信公式移植）

基于通达信量价共振公式的完整选股策略：
  硬门: KDJ 超卖 (J<=20) — 不满足直接排除
  打分: 资金吸筹 + 异动触发 + 防雷过滤 + 均线趋势共振 + 综合信号 + 瀑布线
  6项条件满分100

指标字典结构:
  ind["_df"]         → 完整的 B1 结果 DataFrame（缓存，各条件函数复用）
  ind["b1"]["j"]     → 最新 KDJ-J 值
  ind["b1"]["wl"]    → 最新 DEMA10 白线值
  ind["b1"]["yl"]    → 最新四均线黄线值
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_CODE_DIR = Path(__file__).resolve().parent.parent.parent / "选股代码"
sys.path.insert(0, str(_CODE_DIR))
from B1 import calculate_tongdaxin_signals
from 选股.strategies._indicators import check_waterfall_divergence



# ═══════════════════════════════════════════════════════════════
# 元信息
# ═══════════════════════════════════════════════════════════════

STRATEGY_NAME = "B1量价共振"
STRATEGY_DESC = "通达信量价共振选股：KDJ超卖硬门+资金吸筹+异动触发+防雷+趋势共振+瀑布线，6项条件满分100"


# ═══════════════════════════════════════════════════════════════
# 指标构建
# ═══════════════════════════════════════════════════════════════

def build_indicators(klines: list[dict], closes: list[float]) -> dict:
    """从K线计算B1全套指标，结果 DataFrame 缓存在 ind['_df'] 中"""
    df = _to_df(klines)

    try:
        result_df = calculate_tongdaxin_signals(df)
    except Exception:
        return {"_error": True}

    latest = result_df.iloc[-1]
    close = df['close']

    # DEMA10 白线
    wl = close.ewm(span=10, adjust=False).mean().ewm(span=10, adjust=False).mean()
    # 四均线黄线
    yl = (close.rolling(window=14).mean() +
          close.rolling(window=28).mean() +
          close.rolling(window=57).mean() +
          close.rolling(window=114).mean()) / 4

    j_val = float(latest['J']) if pd.notna(latest.get('J')) else 50.0
    wl_val = float(wl.iloc[-1]) if pd.notna(wl.iloc[-1]) else 0.0
    yl_val = float(yl.iloc[-1]) if pd.notna(yl.iloc[-1]) else 0.0

    return {
        "_error": False,
        "_df": result_df,
        "b1": {
            "j":  j_val,
            "wl": wl_val,
            "yl": yl_val,
        },
    }


# ═══════════════════════════════════════════════════════════════
# 排除过滤
# ═══════════════════════════════════════════════════════════════

def _exclude_no_kdj_oversold(ind: dict, klines: list[dict]) -> bool:
    """KDJ 硬门：最新交易日 J 值 > 13 → 排除"""
    result = ind.get("_df")
    if result is None:
        return True
    j_val = result['J'].iloc[-1]
    return not (pd.notna(j_val) and j_val <= 20)


EXCLUSION_FILTERS = {
    "b1_data_error": {
        "desc": "B1指标计算失败",
        "enabled": True,
        "func": lambda ind, klines: ind.get("_error", False),
    },
    "b1_kdj_filter": {
        "desc": "KDJ超卖硬门(当日J<=20)",
        "enabled": True,
        "func": _exclude_no_kdj_oversold,
    },
}


# ═══════════════════════════════════════════════════════════════
# 筛选条件（满分100）
# ═══════════════════════════════════════════════════════════════

def _get_df(ind: dict, klines: list[dict]) -> pd.DataFrame:
    """优先使用缓存 DataFrame，否则重新计算"""
    df = ind.get("_df")
    if df is not None:
        return df
    return calculate_tongdaxin_signals(_to_df(klines))


def _check_b1_fund_flow(ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """资金吸筹：21日或14日阳量>1.5倍阴量"""
    result = _get_df(ind, klines)
    df = _to_df(klines)
    if len(df) < 21:
        return 0, {"reason": "数据不足(需>=21日)"}
    close = df['close']; open_ = df['open']; volume = df['volume']
    real_yang = (close > open_) & ~(close < close.shift(1))
    real_yin = (close < open_) & ~(close > close.shift(1))
    vy = volume * real_yang; vi = volume * real_yin
    yang21 = vy.rolling(window=21).sum().iloc[-1]; yin21 = vi.rolling(window=21).sum().iloc[-1]
    yang14 = vy.rolling(window=14).sum().iloc[-1]; yin14 = vi.rolling(window=14).sum().iloc[-1]
    r21 = yang21 / yin21 if yin21 > 0 else 999
    r14 = yang14 / yin14 if yin14 > 0 else 999
    if r21 > 1.5 or r14 > 1.5:
        return weight, {"ratio21": round(r21, 1), "ratio14": round(r14, 1),
                        "reason": f"阳/阴 21日={r21:.1f} 14日={r14:.1f}"}
    if r21 > 1.0 or r14 > 1.0:
        return weight // 2, {"ratio21": round(r21, 1), "reason": f"阳略>阴 21日={r21:.1f}"}
    return 0, {"ratio21": round(r21, 1), "reason": f"阳量未超阴量 21日={r21:.1f}"}


def _check_b1_trigger(ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """异动触发：近28日放量异动>=3次"""
    result = _get_df(ind, klines)
    df = _to_df(klines)
    if len(df) < 40:
        return 0, {"reason": "数据不足(需>=40日)"}
    volume = df['volume']; close = df['close']; open_ = df['open']
    avg40 = volume.rolling(window=40).mean()
    plry = (volume > 1.8 * volume.shift(1)) & (close > open_) & (volume > avg40)
    cnt = int(plry.rolling(window=28).sum().iloc[-1])
    if cnt >= 3:
        return weight, {"count": cnt, "reason": f"近28日放量异动{cnt}次"}
    if cnt >= 1:
        return weight // 2, {"count": cnt, "reason": f"近28日放量异动{cnt}次(不足3次)"}
    return 0, {"reason": "近28日无放量异动"}


def _check_b1_defense(ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """防雷通过：28日内无高位放量阴线"""
    result = _get_df(ind, klines)
    df = _to_df(klines)
    if len(df) < 28:
        return 0, {"reason": "数据不足(需>=28日)"}
    open_ = df['open']; close = df['close']; volume = df['volume']
    real_yin = (close < open_) & ~(close > close.shift(1))
    llv_o = open_.rolling(window=28).min(); hhv_o = open_.rolling(window=28).max()
    o85 = llv_o + 0.925 * (hhv_o - llv_o)
    top15o = open_ >= o85
    fd15 = (close < close.shift(1)) & (close <= open_) & (volume >= 1.15 * volume.shift(1))
    g28 = int((top15o & fd15).rolling(window=28).sum().iloc[-1])
    maxv28 = volume.rolling(window=28).max()
    mv = int(((volume == maxv28) & real_yin).rolling(window=28).sum().iloc[-1])
    if g28 == 0 and mv == 0:
        return weight, {"reason": "防雷通过(无高位放量阴线)"}
    issues = []
    if g28 > 0: issues.append(f"高位放量阴{g28}次")
    if mv > 0:  issues.append(f"天量阴{mv}次")
    return 0, {"reason": "防雷未通过: " + ", ".join(issues)}


def _check_b1_trend(ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """均线趋势共振：WL>YL 且 收盘>YL"""
    result = _get_df(ind, klines)
    df = _to_df(klines)
    if len(df) < 114:
        return 0, {"reason": "数据不足(需>=114日)"}
    close = df['close']
    wl = close.ewm(span=10, adjust=False).mean().ewm(span=10, adjust=False).mean()
    yl = (close.rolling(window=14).mean() + close.rolling(window=28).mean() +
          close.rolling(window=57).mean() + close.rolling(window=114).mean()) / 4
    wv, yv, pc = float(wl.iloc[-1]), float(yl.iloc[-1]), float(close.iloc[-1])
    if wv > yv and pc > yv:
        return weight, {"wl": round(wv, 2), "yl": round(yv, 2),
                        "reason": f"WL{wv:.2f}>YL{yv:.2f} 收盘{pc:.2f}>YL"}
    if wv > yv:
        return weight // 2, {"wl": round(wv, 2), "yl": round(yv, 2),
                             "reason": f"WL>YL但收盘{pc:.2f}<=YL{yv:.2f}"}
    return 0, {"wl": round(wv, 2), "yl": round(yv, 2), "reason": f"WL{wv:.2f}<=YL{yv:.2f}"}


def _check_b1_composite(ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """B1综合信号：最近5日触发XG_Signal"""
    result = _get_df(ind, klines)
    recent = result.iloc[-5:] if len(result) >= 5 else result
    if 'XG_Signal' in recent.columns and recent['XG_Signal'].any():
        dates = recent[recent['XG_Signal']]['date'].tolist()
        return weight, {"dates": dates, "reason": f"B1综合信号触发 ({len(dates)}次)"}
    return 0, {"reason": "近5日未触发B1综合信号"}


CRITERIA = {
    "b1_fund_flow": {
        "weight": 20, "desc": "资金吸筹(阳量>1.5x阴量)",
        "params": {}, "func": _check_b1_fund_flow,
    },
    "b1_trigger": {
        "weight": 20, "desc": "异动触发(28日放量>=3次)",
        "params": {}, "func": _check_b1_trigger,
    },
    "b1_defense": {
        "weight": 20, "desc": "防雷通过(无高位放量阴线)",
        "params": {}, "func": _check_b1_defense,
    },
    "b1_trend": {
        "weight": 15, "desc": "均线趋势共振(WL>YL)",
        "params": {}, "func": _check_b1_trend,
    },
    "b1_composite": {
        "weight": 15, "desc": "B1综合信号(全条件满足)",
        "params": {}, "func": _check_b1_composite,
    },
    "waterfall": {
        "weight": 10, "desc": "瀑布线向上发散(MA30>MA60>MA120>MA240)",
        "params": {}, "func": check_waterfall_divergence,
    },
}


# ═══════════════════════════════════════════════════════════════
# 结果展示配置
# ═══════════════════════════════════════════════════════════════

RESULT_INDICATORS = [
    {"key": "b1_j",  "label": "KDJ-J",    "format": ".1f", "source": "b1.j"},
    {"key": "b1_wl", "label": "DEMA10",   "format": ".2f", "source": "b1.wl"},
    {"key": "b1_yl", "label": "QuadMA",   "format": ".2f", "source": "b1.yl"},
]

LATEST_INFO_EXTRA = [
    {"key": "b1_j",  "label": "KDJ-J",  "format": ".1f", "source": "b1.j"},
    {"key": "b1_wl", "label": "DEMA10", "format": ".2f", "source": "b1.wl"},
    {"key": "b1_yl", "label": "QuadMA", "format": ".2f", "source": "b1.yl"},
]

REPORT_CATEGORIES = [
    {
        "name": "B1量价共振信号",
        "criteria_keys": [
            "b1_fund_flow", "b1_trigger",
            "b1_defense", "b1_trend", "b1_composite", "waterfall",
        ],
    },
]


# ═══════════════════════════════════════════════════════════════
# 内部辅助
# ═══════════════════════════════════════════════════════════════

def _to_df(klines: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(klines)
    if 'date' in df.columns:
        df = df.sort_values(by='date').reset_index(drop=True)
    return df
