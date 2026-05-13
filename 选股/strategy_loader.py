"""
策略加载器 — 动态 import 策略模块并校验协议完整性
"""

import importlib.util
import os
from pathlib import Path

# 策略协议要求的顶层导出
REQUIRED_EXPORTS = [
    "STRATEGY_NAME",
    "STRATEGY_DESC",
    "build_indicators",
    "EXCLUSION_FILTERS",
    "CRITERIA",
    "RESULT_INDICATORS",
    "LATEST_INFO_EXTRA",
    "REPORT_CATEGORIES",
]

STRATEGIES_DIR = Path(__file__).resolve().parent / "strategies"


def load_strategy(name: str):
    """
    加载策略模块。

    Args:
        name: 策略文件名（不含 .py），如 "ruthless_wave"

    Returns:
        策略模块对象

    Raises:
        FileNotFoundError: 策略文件不存在
        AttributeError: 策略缺少必要导出
    """
    module_path = STRATEGIES_DIR / f"{name}.py"
    if not module_path.exists():
        available = [f.stem for f in STRATEGIES_DIR.glob("*.py") if not f.stem.startswith("_")]
        raise FileNotFoundError(
            f"策略文件不存在: {module_path}\n"
            f"可用策略: {', '.join(available) if available else '(无)'}"
        )

    spec = importlib.util.spec_from_file_location(
        f"选股.strategies.{name}", str(module_path)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    missing = [exp for exp in REQUIRED_EXPORTS if not hasattr(module, exp)]
    if missing:
        raise AttributeError(
            f"策略 '{name}' 缺少必要导出: {', '.join(missing)}"
        )

    return module
