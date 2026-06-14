"""
盯盘策略加载器 — 动态 import 盯盘策略模块并校验协议完整性
"""

import importlib.util
from pathlib import Path

# 盯盘策略协议要求的顶层导出
REQUIRED_EXPORTS = [
    "STRATEGY_NAME",
    "STRATEGY_DESC",
    "NEED_MINUTE_KLINE",
    "MINUTE_PERIOD",
    "SIGNALS",
]

MONITOR_STRATEGIES_DIR = Path(__file__).resolve().parent.parent / "盯盘策略"


def load_monitor_strategy(name: str):
    """
    加载盯盘策略模块。

    Args:
        name: 策略文件名（不含 .py），如 "example_monitor"

    Returns:
        策略模块对象

    Raises:
        FileNotFoundError: 策略文件不存在
        AttributeError: 策略缺少必要导出
    """
    module_path = MONITOR_STRATEGIES_DIR / f"{name}.py"
    if not module_path.exists():
        available = [f.stem for f in MONITOR_STRATEGIES_DIR.glob("*.py") if not f.stem.startswith("_")]
        raise FileNotFoundError(
            f"盯盘策略文件不存在: {module_path}\n"
            f"可用策略: {', '.join(available) if available else '(无)'}"
        )

    spec = importlib.util.spec_from_file_location(
        f"盯盘策略.{name}", str(module_path)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    missing = [exp for exp in REQUIRED_EXPORTS if not hasattr(module, exp)]
    if missing:
        raise AttributeError(
            f"盯盘策略 '{name}' 缺少必要导出: {', '.join(missing)}"
        )

    return module


def discover_monitor_strategies() -> list[dict]:
    """
    扫描盯盘策略目录，返回所有可用策略的摘要。

    Returns:
        list of dict，每个 dict 包含:
        - name: 文件名（不含 .py）
        - strategy_name: 策略显示名称
        - strategy_desc: 策略描述
        - need_minute_kline: 是否需要分钟K线
        - minute_period: 分钟K线周期
        - signal_count: 信号数量
        - signals: 信号名称列表
    """
    if not MONITOR_STRATEGIES_DIR.exists():
        return []

    result = []
    for f in sorted(MONITOR_STRATEGIES_DIR.glob("*.py")):
        if f.stem.startswith("_"):
            continue
        try:
            module = load_monitor_strategy(f.stem)
            signals = getattr(module, "SIGNALS", [])
            signal_names = [s.get("name", "") for s in signals if isinstance(s, dict)]
            result.append({
                "name": f.stem,
                "strategy_name": module.STRATEGY_NAME,
                "strategy_desc": module.STRATEGY_DESC,
                "need_minute_kline": module.NEED_MINUTE_KLINE,
                "minute_period": module.MINUTE_PERIOD,
                "signal_count": len(signals),
                "signals": signal_names,
            })
        except Exception:
            continue
    return result
