"""
命令行接口：运行回测任务。

用法:
    python -m 选股.backtest.cli --strategy b1 --pool 沪深300 --start 2026-01-05 --end 2026-06-01
    python -m 选股.backtest.cli --strategy brick --pool 沪深主板 --top-n 15 --hold 10 --html
"""
import argparse
import json
import sys
from pathlib import Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="回测系统 CLI")
    parser.add_argument("--strategy", default="b1", help="策略名 (b1/brick/ruthless_wave)")
    parser.add_argument("--pool", default="沪深300", help="股票池")
    parser.add_argument("--top-n", type=int, default=10, help="每轮选股数")
    parser.add_argument("--min-score", type=int, default=25, help="最低入围分")
    parser.add_argument("--hold", type=int, default=5, help="持有天数")
    parser.add_argument("--capital", type=float, default=10000, help="每只投入资金")
    parser.add_argument("--start", default=None, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--skip", type=int, default=250, help="跳过预热天数")
    parser.add_argument("--output", default=None, help="结果 JSON 输出路径")
    parser.add_argument("--html", action="store_true", help="同时输出 HTML 报告")
    parser.add_argument("--verbose", action="store_true", default=True, help="打印详细进度")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None):
    args = parse_args(argv)

    from 选股.backtest.engine import BacktestEngine

    engine = BacktestEngine(
        strategy_name=args.strategy,
        pool_name=args.pool,
        top_n=args.top_n,
        min_score=args.min_score,
        holding_days=args.hold,
        capital_per_stock=args.capital,
    )

    result = engine.run(
        start_date=args.start,
        end_date=args.end,
        skip_first=args.skip,
        verbose=args.verbose,
    )

    m = result["metrics"]
    dr = result.get("date_range", {})
    print(f"\n{'=' * 60}")
    print(f"  策略: {result['strategy_name']} @ {result['pool_name']}")
    print(f"  周期: {dr.get('start', '?')} ~ {dr.get('end', '?')}")
    print(f"  轮次: {result['total_rounds']} | 交易: {m.get('total_trades', 0)} 笔")
    print(f"  总收益: {m.get('total_return_pct', 0):+.2f}%")
    print(f"  平均每笔: {m.get('avg_return_pct', 0):+.2f}%")
    print(f"  胜率: {m.get('win_rate', 0):.1f}% ({m.get('wins', 0)}胜/{m.get('losses', 0)}负)")
    print(f"  最大收益: {m.get('max_return_pct', 0):+.2f}% | 最小: {m.get('min_return_pct', 0):+.2f}%")
    print(f"  耗时: {result['elapsed']:.0f}s")
    print(f"{'=' * 60}")

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\n结果已保存: {output_path}")

    if args.html:
        from 选股.backtest.report import generate_report
        html = generate_report(result)
        if args.output:
            html_path = Path(args.output).with_suffix(".html")
        else:
            html_path = Path(f"backtest_{args.strategy}_{args.pool}.html")
        html_path.write_text(html, encoding="utf-8")
        print(f"HTML 报告: {html_path}")


if __name__ == "__main__":
    main()
