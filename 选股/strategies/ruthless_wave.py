"""
无情操盘手 + 波浪理论 综合选股策略

双系统指标：白线/黄线/KDJ/砖柱（无情操盘手）+ MACD/Fib/背离/成交量（波浪理论）
13条筛选条件，满分100，最低入围25分。
"""

import sys
from pathlib import Path

# 项目根路径
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "脚本"
sys.path.insert(0, str(_SCRIPTS_DIR))

from 选股.criteria.ruthless import (
    build_ruthless_indicators,
    is_death_cross_now,
    is_below_yellow,
    check_b1_over_sold,
    check_b_brick_reversal,
    check_white_yellow_golden,
    check_price_structure_strong,
    check_kdj_bottom_golden,
)
from 选股.criteria.wave import (
    build_wave_indicators,
    check_macd_golden_cross,
    check_bottom_divergence,
    check_fib_support,
    check_volume_shrink_stop,
    check_wave_position_fav,
    check_bull_divergence_multi,
    check_fib_cluster_support,
    check_volume_breakout_confirm,
)

# ═══════════════════════════════════════════════════════════════
# 元信息
# ═══════════════════════════════════════════════════════════════

STRATEGY_NAME = "无情操盘手+波浪理论"
STRATEGY_DESC = "双系统技术指标综合筛选：无情操盘手（白线/黄线/KDJ/砖柱）+ 波浪理论（MACD/Fib/背离/成交量），13条条件满分100"


# ═══════════════════════════════════════════════════════════════
# 指标构建
# ═══════════════════════════════════════════════════════════════

def build_indicators(klines: list[dict], closes: list[float]) -> dict:
    """从K线计算双系统全套指标，返回合并后的指标字典"""
    ruthless = build_ruthless_indicators(klines, closes)
    wave = build_wave_indicators(klines, closes)
    return {**ruthless, **wave}


# ═══════════════════════════════════════════════════════════════
# 排除过滤
# ═══════════════════════════════════════════════════════════════

EXCLUSION_FILTERS = {
    "death_cross": {
        "desc": "白黄死叉",
        "enabled": True,
        "func": lambda ind, klines: is_death_cross_now(ind),
    },
    "below_yellow": {
        "desc": "价格跌破黄线",
        "enabled": True,
        "func": lambda ind, klines: is_below_yellow(ind, klines),
    },
}


# ═══════════════════════════════════════════════════════════════
# 筛选条件（满分100）
# ═══════════════════════════════════════════════════════════════

CRITERIA = {
    # ── 无情操盘手（权重合计 45）──
    "b1_over_sold": {
        "weight": 15,
        "desc": "B1超卖(RSV<30)",
        "params": {"rsv_max": 30},
        "func": check_b1_over_sold,
    },
    "b_brick_reversal": {
        "weight": 10,
        "desc": "B砖底部反转(绿→蓝→黄)",
        "params": {},
        "func": check_b_brick_reversal,
    },
    "white_yellow_golden": {
        "weight": 10,
        "desc": "白黄线金叉近日发生",
        "params": {"lookback_days": 5},
        "func": check_white_yellow_golden,
    },
    "price_structure_strong": {
        "weight": 5,
        "desc": "价格>白线>黄线(强势排列)",
        "params": {},
        "func": check_price_structure_strong,
    },
    "kdj_bottom_golden": {
        "weight": 5,
        "desc": "KDJ低位金叉(J<30且K上穿D)",
        "params": {},
        "func": check_kdj_bottom_golden,
    },

    # ── 波浪理论（权重合计 40）──
    "macd_golden_cross": {
        "weight": 10,
        "desc": "MACD(5,34,5)金叉",
        "params": {"lookback_days": 5},
        "func": check_macd_golden_cross,
    },
    "macd_bottom_divergence": {
        "weight": 12,
        "desc": "MACD底背离",
        "params": {"lookback_days": 60},
        "func": check_bottom_divergence,
    },
    "fibonacci_support": {
        "weight": 8,
        "desc": "价格在Fib关键支撑位±2%内",
        "params": {"fib_levels": [0.382, 0.5, 0.618], "tolerance": 0.02},
        "func": check_fib_support,
    },
    "volume_shrink_stop": {
        "weight": 5,
        "desc": "缩量止跌(近期缩量+价格企稳)",
        "params": {"shrink_ratio": 0.7, "lookback_days": 20},
        "func": check_volume_shrink_stop,
    },
    "wave_position_fav": {
        "weight": 5,
        "desc": "波浪位有利(浪2底/浪4底/浪C底)",
        "params": {},
        "func": check_wave_position_fav,
    },

    # ── 加分项（权重合计 15）──
    "bull_divergence_multi": {
        "weight": 5,
        "desc": "多重底背离(不同级别)",
        "params": {},
        "func": check_bull_divergence_multi,
    },
    "fib_cluster_support": {
        "weight": 5,
        "desc": "Fib汇聚支撑(两个级别共振)",
        "params": {},
        "func": check_fib_cluster_support,
    },
    "volume_breakout_confirm": {
        "weight": 5,
        "desc": "放量阳线确认(近3日有实体≥2%+量>2x均量)",
        "params": {"body_pct": 2.0, "vol_multiple": 2.0},
        "func": check_volume_breakout_confirm,
    },
}


# ═══════════════════════════════════════════════════════════════
# 结果展示配置
# ═══════════════════════════════════════════════════════════════

RESULT_INDICATORS = [
    {"key": "white",     "label": "白线(DEMA10)", "format": ".2f", "source": "white"},
    {"key": "yellow",    "label": "黄线(quad-MA)", "format": ".2f", "source": "yellow"},
    {"key": "kdj_j",     "label": "KDJ-J",         "format": ".1f", "source": "kdj.j"},
    {"key": "macd_hist", "label": "MACD柱",        "format": ".4f", "source": "macd.histogram"},
]

LATEST_INFO_EXTRA = [
    {"key": "macd_dif",  "label": "DIF",     "format": ".4f", "source": "macd.dif"},
    {"key": "macd_dea",  "label": "DEA",     "format": ".4f", "source": "macd.dea"},
    {"key": "macd_hist", "label": "MACD柱",  "format": ".4f", "source": "macd.histogram"},
]

REPORT_CATEGORIES = [
    {
        "name": "无情操盘手信号 (B1/B砖/白黄线)",
        "criteria_keys": [
            "b1_over_sold", "b_brick_reversal", "white_yellow_golden",
            "price_structure_strong", "kdj_bottom_golden",
        ],
    },
    {
        "name": "波浪理论信号 (MACD金叉/底背离/Fib支撑)",
        "criteria_keys": [
            "macd_golden_cross", "macd_bottom_divergence", "fibonacci_support",
            "volume_shrink_stop", "wave_position_fav",
        ],
    },
]
