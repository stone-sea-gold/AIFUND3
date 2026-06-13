"""
参数对比/策略对比运行器：一次运行多种配置，输出对比结果。

用法:
    from 选股.backtest.comparison import run_comparison

    configs = [
        {"strategy_name": "b1", "top_n": 5, "holding_days": 5},
        {"strategy_name": "b1", "top_n": 10, "holding_days": 5},
        {"strategy_name": "brick", "top_n": 5, "holding_days": 5},
    ]
    results = run_comparison(configs, pool_name="沪深300",
                             start_date="2026-01-05", end_date="2026-06-01")
"""
from typing import Any


def run_comparison(
    configs: list[dict[str, Any]],
    pool_name: str = "沪深300",
    start_date: str | None = None,
    end_date: str | None = None,
    verbose: bool = True,
) -> list[dict]:
    """运行多组配置的对比回测

    Args:
        configs: 配置列表，每项可含 strategy_name, top_n, min_score, holding_days, capital_per_stock
        pool_name: 公共股票池
        start_date/end_date: 公共日期范围

    Returns:
        [{config: ..., metrics: ..., total_rounds: ..., elapsed: ...}, ...]
    """
    results = []
    n = len(configs)

    for i, cfg in enumerate(configs):
        if verbose:
            print(f"\n[{i+1}/{n}] 运行: {cfg.get('strategy_name')} "
                  f"top_n={cfg.get('top_n', 10)} hold={cfg.get('holding_days', 5)}")

        from 选股.backtest.engine import BacktestEngine

        engine = BacktestEngine(
            strategy_name=cfg.get("strategy_name", "b1"),
            pool_name=pool_name,
            top_n=cfg.get("top_n", 10),
            min_score=cfg.get("min_score", 25),
            holding_days=cfg.get("holding_days", 5),
            capital_per_stock=cfg.get("capital_per_stock", 10000),
        )

        result = engine.run(
            start_date=start_date,
            end_date=end_date,
            skip_first=cfg.get("skip_first", 250),
            verbose=verbose,
        )

        results.append({
            "config": cfg,
            "metrics": result["metrics"],
            "total_rounds": result["total_rounds"],
            "total_trades": result["total_trades"],
            "elapsed": result["elapsed"],
        })

    return results
