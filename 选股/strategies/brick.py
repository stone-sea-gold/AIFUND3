"""
砖型图趋势拐点选股策略（通达信公式移植）

基于通达信砖型图公式的趋势拐点选股策略，包含：
- 核心动量指标 (VAR1A-VAR6A 动量系统)
- V型拐点识别 (AA & CC)
- 反弹力度判定 (红柱 > 前绿柱 * 2/3)
- 趋势护航 (短期线 > 多空线 且 收盘 > 多空线)

指标字典结构:
  ind["_df"]              → 完整的砖型图结果 DataFrame（缓存，各条件函数复用）
  ind["brick"]["value"]    → 最新砖型图值
  ind["brick"]["short"]    → 最新短期线 (DEMA10)
  ind["brick"]["long"]     → 最新多空线 (四均线)
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_CODE_DIR = Path(__file__).resolve().parent.parent.parent / "选股代码"
sys.path.insert(0, str(_CODE_DIR))
from 砖型图 import calculate_brick_inflection_signals


# ═══════════════════════════════════════════════════════════════
# 元信息
# ═══════════════════════════════════════════════════════════════

STRATEGY_NAME = "砖型图趋势拐点"
STRATEGY_DESC = "通达信砖型图趋势拐点选股：V型拐点+反弹力度+趋势护航，4项条件满分100"


# ═══════════════════════════════════════════════════════════════
# 指标构建
# ═══════════════════════════════════════════════════════════════

def build_indicators(klines: list[dict], closes: list[float]) -> dict:
    """从K线计算砖型图全套指标，结果 DataFrame 缓存在 ind['_df'] 中"""
    df = _to_df(klines)
    try:
        result_df = calculate_brick_inflection_signals(df)
    except Exception:
        return {"_error": True}

    latest = result_df.iloc[-1]

    brick_val = float(latest.get('brick_value', 0)) if pd.notna(latest.get('brick_value')) else 0.0
    short_val = float(latest.get('short_line', 0)) if pd.notna(latest.get('short_line')) else 0.0
    long_val  = float(latest.get('long_line', 0)) if pd.notna(latest.get('long_line')) else 0.0

    return {
        "_error": False,
        "_df": result_df,
        "brick": {
            "value": brick_val,
            "short": short_val,
            "long":  long_val,
        },
    }


# ═══════════════════════════════════════════════════════════════
# 排除过滤
# ═══════════════════════════════════════════════════════════════

EXCLUSION_FILTERS = {
    "brick_data_error": {
        "desc": "砖型图指标计算失败",
        "enabled": True,
        "func": lambda ind, klines: ind.get("_error", False),
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
    return calculate_brick_inflection_signals(_to_df(klines))


def _check_brick_v_reversal(ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """V型拐点：砖型图值形成V型转折(AA AND CC)"""
    result = _get_df(ind, klines)
    if len(result) < 5:
        return 0, {"reason": "数据不足(需>=5日)"}
    brick = result['brick_value']
    n = len(brick)
    for offset in range(min(5, n - 2)):
        idx = n - 1 - offset
        if idx < 2: continue
        aa = bool(brick.iloc[idx] > brick.iloc[idx - 1])
        cc = bool(brick.iloc[idx - 1] < brick.iloc[idx - 2])
        if aa and cc:
            return weight, {"date": str(result['date'].iloc[idx]),
                            "brick": round(float(brick.iloc[idx]), 2),
                            "reason": f"V型拐点 砖值={brick.iloc[idx]:.2f}"}
    return 0, {"reason": "近5日无V型拐点"}


def _check_brick_rebound(ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """反弹力度：红柱长度 > 前绿柱长度 * 2/3"""
    result = _get_df(ind, klines)
    if len(result) < 5:
        return 0, {"reason": "数据不足(需>=5日)"}
    brick = result['brick_value']
    n = len(brick)
    best_ratio = 0.0
    for offset in range(min(5, n - 2)):
        idx = n - 1 - offset
        if idx < 2: continue
        red = float(brick.iloc[idx] - brick.iloc[idx - 1])
        prev = float(brick.iloc[idx - 2] - brick.iloc[idx - 1])
        if prev > 0 and red > prev * 2 / 3:
            best_ratio = max(best_ratio, red / prev)
    if best_ratio > 0:
        return weight, {"ratio": round(best_ratio, 2),
                        "reason": f"反弹力度充足 红/绿={best_ratio:.2f}"}
    # 部分得分：有反弹但不满足力度
    for offset in range(min(5, n - 1)):
        idx = n - 1 - offset
        if idx < 1: continue
        if float(brick.iloc[idx] - brick.iloc[idx - 1]) > 0:
            return weight // 3, {"reason": "有反弹但力度不足"}
    return 0, {"reason": "近5日无反弹"}


def _check_brick_trend(ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """趋势护航：短期线>多空线 且 收盘>多空线"""
    result = _get_df(ind, klines)
    if len(result) < 114:
        return 0, {"reason": "数据不足(需>=114日)"}
    short = float(result['short_line'].iloc[-1])
    long  = float(result['long_line'].iloc[-1])
    price = float(result['close'].iloc[-1])
    if short > long and price > long:
        return weight, {"short": round(short, 2), "long": round(long, 2),
                        "reason": f"趋势向上 短期{short:.2f}>多空{long:.2f} 价{price:.2f}>多空"}
    if short > long:
        return weight // 2, {"short": round(short, 2), "long": round(long, 2),
                             "reason": f"短期>多空 但价{price:.2f}<=多空{long:.2f}"}
    return 0, {"short": round(short, 2), "long": round(long, 2),
               "reason": f"短期{short:.2f}<=多空{long:.2f}"}


def _check_brick_composite(ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """砖型图综合信号：最近5日触发XG_Signal"""
    result = _get_df(ind, klines)
    recent = result.iloc[-5:] if len(result) >= 5 else result
    if 'XG_Signal' in recent.columns and recent['XG_Signal'].any():
        dates = recent[recent['XG_Signal']]['date'].tolist()
        return weight, {"dates": dates, "reason": f"砖型图综合信号触发 ({len(dates)}次)"}
    return 0, {"reason": "近5日未触发砖型图综合信号"}


CRITERIA = {
    "brick_v_reversal": {
        "weight": 30, "desc": "V型拐点(砖型图AA且CC)",
        "params": {}, "func": _check_brick_v_reversal,
    },
    "brick_rebound": {
        "weight": 25, "desc": "反弹力度(红柱>前绿柱*2/3)",
        "params": {}, "func": _check_brick_rebound,
    },
    "brick_trend": {
        "weight": 25, "desc": "趋势护航(短期>多空且价>多空)",
        "params": {}, "func": _check_brick_trend,
    },
    "brick_composite": {
        "weight": 20, "desc": "砖型图综合信号(全条件满足)",
        "params": {}, "func": _check_brick_composite,
    },
}


# ═══════════════════════════════════════════════════════════════
# 结果展示配置
# ═══════════════════════════════════════════════════════════════

RESULT_INDICATORS = [
    {"key": "brick_value", "label": "砖型图值", "format": ".2f", "source": "brick.value"},
    {"key": "brick_short", "label": "短期线",   "format": ".2f", "source": "brick.short"},
    {"key": "brick_long",  "label": "多空线",   "format": ".2f", "source": "brick.long"},
]

LATEST_INFO_EXTRA = [
    {"key": "brick_value", "label": "砖型图值", "format": ".2f", "source": "brick.value"},
    {"key": "brick_short", "label": "短期线",   "format": ".2f", "source": "brick.short"},
    {"key": "brick_long",  "label": "多空线",   "format": ".2f", "source": "brick.long"},
]

REPORT_CATEGORIES = [
    {
        "name": "砖型图趋势拐点信号",
        "criteria_keys": [
            "brick_v_reversal", "brick_rebound", "brick_trend", "brick_composite",
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
