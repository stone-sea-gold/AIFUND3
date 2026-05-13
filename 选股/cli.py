#!/usr/bin/env python3
"""
选股模块 — CLI 命令行入口

用法:
    python -m 选股.cli scan                     # 全量扫描（默认沪深300）
    python -m 选股.cli scan --pool 全A          # 全A股扫描
    python -m 选股.cli scan --pool 自选         # 只扫观察仓自选股
    python -m 选股.cli scan --pool 沪深300 --top 30 --min-score 30
    python -m 选股.cli scan --pool 中证500 --delay 0.3
    python -m 选股.cli report --date 2026-05-09 # 查看某日报告
    python -m 选股.cli list-pools               # 列出可用股票池
    python -m 选股.cli test 600519              # 单票诊断
"""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from 选股.config import (
    STOCK_POOL, TOP_N, MIN_SCORE, REQUEST_DELAY, OUTPUT_DIR,
)
from 选股.pool import INDEX_CONFIG
from 选股.scanner import scan_all, scan_one
from 选股.report import generate_markdown, save_report, print_summary


def cmd_scan(args):
    """执行选股扫描"""
    pool_name = args.pool or STOCK_POOL
    top_n = args.top or TOP_N
    min_score = args.min_score or MIN_SCORE

    # 允许命令行覆盖请求间隔
    import 选股.config as cfg
    if args.delay is not None:
        cfg.REQUEST_DELAY = args.delay

    print(f"{'═' * 60}")
    print(f"  选股扫描")
    print(f"  股票池: {pool_name}  |  Top: {top_n}  |  最低分: {min_score}")
    print(f"  请求间隔: {cfg.REQUEST_DELAY}s")
    print(f"{'═' * 60}\n")

    results = scan_all(
        pool_name=pool_name,
        top_n=top_n,
        min_score=min_score,
        workers=args.workers or 4,
        verbose=not args.quiet,
    )

    if not results:
        print("\n无符合条件的标的。")
        return

    print_summary(results)

    # 生成报告
    md = generate_markdown(results, pool_name, top_n)
    path = save_report(md, pool_name)
    print(f"报告已保存: {path}")

    if not args.no_print:
        print(f"\n{'─' * 60}")
        print("报告预览 (前 80 行):")
        print(f"{'─' * 60}")
        for line in md.split("\n")[:80]:
            print(line)


def cmd_report(args):
    """查看指定日期的选股报告"""
    date_str = args.date or date.today().strftime("%Y-%m-%d")
    out_dir = PROJECT_ROOT / OUTPUT_DIR
    files = sorted(out_dir.glob(f"{date_str}*.md"))
    if not files:
        print(f"未找到 {date_str} 的选股报告")
        available = sorted(out_dir.glob("*.md"))
        if available:
            print(f"可用报告: {', '.join(f.stem for f in available[-10:])}")
        return
    for f in files:
        print(f.read_text(encoding="utf-8"))


def cmd_list_pools(args):
    """列出可用股票池"""
    print("可用股票池:")
    print(f"  {'名称':<12} {'说明':>6}  {'覆盖范围'}")
    print(f"  {'─' * 12} {'─' * 6}  {'─' * 40}")
    print(f"  {'全A':<12} {'~5000只'}  全部A股（主板+创业板+科创板，耗时长）")
    print(f"  {'沪深主板':<12} {'~3300只'} 沪市+深市主板（剔除创业板/科创板/北交所）")
    print(f"  {'沪深300':<12} {'~300只'}  沪深300成分股")
    print(f"  {'中证500':<12} {'~500只'}  中证500成分股")
    print(f"  {'自选':<12} {'自定义'}  观察仓/watchlist.txt 中的自选股")
    print()
    print("用法: python -m 选股.cli scan --pool <名称>")


def cmd_test(args):
    """单票诊断：测试所有条件"""
    code = args.code.strip()
    print(f"正在分析 {code} ...\n")
    r = scan_one(code, code)
    if r is None:
        print(f"{code} 被排除：不满足基础条件或数据不可用。")
        return

    print_summary([r])
    md = generate_markdown([r], "单票测试", 1)
    path = save_report(md, "_test")
    print(f"诊断报告: {path}")


def main():
    parser = argparse.ArgumentParser(
        description="选股模块 — A股技术形态筛选系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m 选股.cli scan --pool 沪深300 --top 20
  python -m 选股.cli scan --pool 自选
  python -m 选股.cli test 600519
  python -m 选股.cli report --date 2026-05-09
        """,
    )
    sub = parser.add_subparsers(dest="command", help="子命令")

    # scan
    p_scan = sub.add_parser("scan", help="执行选股扫描")
    p_scan.add_argument("--pool", default=STOCK_POOL, help="股票池名称 (默认: 沪深300)")
    p_scan.add_argument("--top", type=int, default=TOP_N, help=f"输出前N只 (默认: {TOP_N})")
    p_scan.add_argument("--min-score", type=int, default=MIN_SCORE, help=f"最低入围分 (默认: {MIN_SCORE})")
    p_scan.add_argument("--delay", type=float, help=f"请求间隔秒 (默认: {REQUEST_DELAY})")
    p_scan.add_argument("--workers", type=int, default=4, help="并行线程数 (默认: 4)")
    p_scan.add_argument("--quiet", action="store_true", help="静默模式")
    p_scan.add_argument("--no-print", action="store_true", help="不打印报告预览")

    # report
    p_report = sub.add_parser("report", help="查看选股报告")
    p_report.add_argument("--date", help="日期 (YYYY-MM-DD)，默认今天")

    # list-pools
    sub.add_parser("list-pools", help="列出可用股票池")

    # test
    p_test = sub.add_parser("test", help="单票诊断")
    p_test.add_argument("code", help="股票代码")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    dispatch = {
        "scan": cmd_scan,
        "report": cmd_report,
        "list-pools": cmd_list_pools,
        "test": cmd_test,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
