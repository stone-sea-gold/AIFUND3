"""
砖型图趋势拐点选股策略（通达信公式移植）

基于通达信砖型图公式的趋势拐点选股策略，包含：
- 核心动量指标 (VAR1A-VAR6A 动量系统)
- V型拐点识别 (AA & CC) — 仅当日
- 反弹力度判定 (红柱 / 前绿柱 比率连续打分) — 仅当日
- 趋势护航 (短期线 / 多空线 偏离幅度连续打分) — 仅当日

指标字典结构:
  ind["_df"]              → 完整的砖型图结果 DataFrame（缓存，各条件函数复用）
  ind["brick"]["value"]    → 最新砖型图值
  ind["brick"]["short"]    → 最新短期线 (EMA10双重平滑)
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
STRATEGY_DESC = "通达信砖型图趋势拐点选股：当日V型拐点+反弹力度+趋势护航，3项条件满分100"


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
# 内部辅助
# ═══════════════════════════════════════════════════════════════

def _to_df(klines: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(klines)
    if 'date' in df.columns:
        df = df.sort_values(by='date').reset_index(drop=True)
    return df


def _get_df(ind: dict, klines: list[dict]) -> pd.DataFrame:
    """优先使用缓存 DataFrame，否则重新计算"""
    df = ind.get("_df")
    if df is not None:
        return df
    return calculate_brick_inflection_signals(_to_df(klines))


def _latest_brick_values(result: pd.DataFrame) -> tuple[float, float, float]:
    """提取最近三根 bar 的砖型图值: (today, yesterday, day_before_yesterday)"""
    brick = result['brick_value']
    n = len(brick)
    if n < 3:
        return (float(brick.iloc[-1]), 0.0, 0.0)
    return (
        float(brick.iloc[-1]),
        float(brick.iloc[-2]),
        float(brick.iloc[-3]),
    )


# ═══════════════════════════════════════════════════════════════
# 筛选条件（满分100）
# ═══════════════════════════════════════════════════════════════

def _check_brick_v_reversal(ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """
    V型拐点：当日 bar 构成 V 型底部右半支

    仅检查最新一根 bar（今日），判断是否:
      AA := 砖型图 > REF(砖型图, 1)   → 今日砖值上升
      CC := REF(砖型图, 1) < REF(砖型图, 2) → 昨日砖值低于前日（形成 V 底左半支）

    打分:
      AA AND CC  → 满分 (完整 V 型拐点确认)
      仅 CC       → 1/3 分 (底部形成中，等待反转确认)
      仅 AA       → 1/4 分 (砖值上升但无 V 型结构)
      均不满足    → 0 分
    """
    result = _get_df(ind, klines)
    if len(result) < 3:
        return 0, {"reason": "数据不足(需>=3日)"}

    today, yesterday, day_before = _latest_brick_values(result)

    aa = today > yesterday
    cc = yesterday < day_before

    if aa and cc:
        return weight, {
            "date": str(result['date'].iloc[-1]),
            "brick": round(today, 2),
            "reason": f"当日V型拐点 砖值={today:.2f} (前日{day_before:.2f}→昨{yesterday:.2f}→今{today:.2f})",
        }
    elif cc:
        return weight // 3, {
            "brick": round(today, 2),
            "reason": f"砖值回落中(前日{day_before:.2f}→昨{yesterday:.2f}) 待反转确认 今{today:.2f}",
        }
    elif aa:
        return weight // 4, {
            "brick": round(today, 2),
            "reason": f"砖值上升(昨{yesterday:.2f}→今{today:.2f}) 但未形成V型结构",
        }
    else:
        return 0, {
            "brick": round(today, 2),
            "reason": f"砖值下行 今{today:.2f}<=昨{yesterday:.2f}",
        }


def _check_brick_rebound(ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """
    反弹力度：当日红柱长度 / 前绿柱长度 比率连续打分

    仅检查当日:
      红柱长度 := 砖型图 - REF(砖型图, 1)     → 今日砖值变化
      前绿柱长度 := REF(砖型图, 2) - REF(砖型图, 1) → 前日到昨日的变化量

    若红柱 > 0 且前绿柱 > 0，按倍率打分:
      ratio >= 3.0  → 满分
      ratio >= 2.0  → 4/5 分
      ratio >= 1.5  → 3/5 分
      ratio >= 1.0  → 2/5 分
      ratio >= 0.67 → 1/3 分 (通达信最低门槛)
      ratio >  0    → 1/6 分
    若红柱 > 0 但前绿柱 <= 0 (无前导下跌):  1/5 分
    若红柱 <= 0 (今日砖值未上升):          0 分
    """
    result = _get_df(ind, klines)
    if len(result) < 3:
        return 0, {"reason": "数据不足(需>=3日)"}

    today, yesterday, day_before = _latest_brick_values(result)

    red_bar = today - yesterday
    prev_green = day_before - yesterday

    if red_bar <= 0:
        return 0, {
            "red_bar": round(red_bar, 2),
            "reason": f"今日砖值未上升 红柱={red_bar:.2f}",
        }

    if prev_green <= 0.01:
        return weight // 5, {
            "red_bar": round(red_bar, 2),
            "reason": f"砖值上升(红柱={red_bar:.2f}) 但前导无显著下跌",
        }

    ratio = red_bar / prev_green

    if ratio >= 3.0:
        score = weight
        level = "极强"
    elif ratio >= 2.0:
        score = weight * 4 // 5
        level = "强势"
    elif ratio >= 1.5:
        score = weight * 3 // 5
        level = "良好"
    elif ratio >= 1.0:
        score = weight * 2 // 5
        level = "一般"
    elif ratio >= 2 / 3:
        score = weight // 3
        level = "及格"
    else:
        score = weight // 6
        level = "微弱"

    return score, {
        "ratio": round(ratio, 2),
        "red_bar": round(red_bar, 2),
        "prev_green": round(prev_green, 2),
        "reason": f"反弹{level} 红柱={red_bar:.2f} 前绿={prev_green:.2f} 倍率={ratio:.2f}",
    }


def _check_brick_trend(ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """
    趋势护航：短期线与收盘价超越多空线的幅度连续打分

    仅检查当日:
      short_above% := (短期线 - 多空线) / 多空线 * 100
      close_above% := (收盘 - 多空线) / 多空线 * 100

    取两者中较小值（保守原则）:
      >= 3.0%  → 满分
      >= 2.0%  → 4/5 分
      >= 1.0%  → 3/5 分
      >= 0.5%  → 2/5 分
      >  0%    → 1/5 分
    若短期线 > 多空线但收盘 <= 多空线: 1/10 分
    否则: 0 分
    """
    result = _get_df(ind, klines)
    if len(result) < 114:
        return 0, {"reason": "数据不足(需>=114日)"}

    short = float(result['short_line'].iloc[-1])
    long  = float(result['long_line'].iloc[-1])
    price = float(result['close'].iloc[-1])

    if pd.isna(short) or pd.isna(long) or long <= 0:
        return 0, {"reason": "趋势线数据异常"}

    short_pct = (short - long) / long * 100
    close_pct = (price - long) / long * 100

    if short_pct > 0 and close_pct > 0:
        pct = min(short_pct, close_pct)

        if pct >= 3.0:
            score = weight
            level = "极强"
        elif pct >= 2.0:
            score = weight * 4 // 5
            level = "强势"
        elif pct >= 1.0:
            score = weight * 3 // 5
            level = "良好"
        elif pct >= 0.5:
            score = weight * 2 // 5
            level = "一般"
        else:
            score = weight // 5
            level = "偏弱"

        return score, {
            "short": round(short, 2), "long": round(long, 2),
            "short_pct": round(short_pct, 2), "close_pct": round(close_pct, 2),
            "reason": f"趋势{level} 短期>{'多空' if short > long else ''} {short_pct:+.2f}% 收盘>{'多空' if price > long else ''} {close_pct:+.2f}%",
        }

    if short_pct > 0:
        return weight // 10, {
            "short": round(short, 2), "long": round(long, 2),
            "reason": f"短期>{'多空' if short > long else ''} {short_pct:+.2f}% 但收盘{price:.2f}<=多空{long:.2f}",
        }

    return 0, {
        "short": round(short, 2), "long": round(long, 2),
        "reason": f"短期{short:.2f}<=多空{long:.2f}",
    }


# ═══════════════════════════════════════════════════════════════
# 条件注册表
# ═══════════════════════════════════════════════════════════════

CRITERIA = {
    "brick_v_reversal": {
        "weight": 35, "desc": "当日V型拐点(AA且CC)",
        "params": {}, "func": _check_brick_v_reversal,
    },
    "brick_rebound": {
        "weight": 35, "desc": "反弹力度(红柱/前绿柱倍率)",
        "params": {}, "func": _check_brick_rebound,
    },
    "brick_trend": {
        "weight": 30, "desc": "趋势护航(短期/收盘超多空幅度)",
        "params": {}, "func": _check_brick_trend,
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
            "brick_v_reversal", "brick_rebound", "brick_trend",
        ],
    },
]
