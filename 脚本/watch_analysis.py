#!/usr/bin/env python3
"""
观察仓分析 — 手动触发
读取 观察仓/watchlist.txt，调 DeepSeek 分析，结果写入 观察仓/YYYY-MM-DD.md
用法: python3 脚本/watch_analysis.py
"""
import sys, os
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR  = PROJECT_ROOT / "脚本"
WATCH_DIR    = PROJECT_ROOT / "观察仓"
WATCHLIST    = WATCH_DIR / "watchlist.txt"
ENV_FILE     = PROJECT_ROOT / ".env"

sys.path.insert(0, str(SCRIPTS_DIR))
from daily_analysis import load_env, extract_indicators, call_deepseek, git_push
from fetch_wave_data import analyze

def read_watchlist() -> dict:
    if not WATCHLIST.exists():
        print("✗ watchlist.txt 不存在")
        return {}
    stocks = {}
    for line in WATCHLIST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        code = parts[0]
        name = parts[1] if len(parts) > 1 else code
        stocks[code] = name
    return stocks

def write_watch_log(analysis: str, indicators_list: list) -> Path:
    today = date.today().strftime("%Y-%m-%d")
    log_path = WATCH_DIR / f"{today}.md"

    summary = " | ".join(
        f"{i['name']} {'+' if i['change']>=0 else ''}{i['change']}% {i['signal']}"
        for i in indicators_list
    )
    content = f"""# {today} 观察仓分析

> {summary}

## DeepSeek 分析

{analysis}

---
*Jetson AGX 手动触发 · DeepSeek V3*
"""
    log_path.write_text(content, encoding="utf-8")
    return log_path

def main():
    load_env()
    stocks = read_watchlist()
    if not stocks:
        print("watchlist.txt 为空，请填入股票代码后再运行")
        return

    print(f"=== 观察仓分析 {date.today()} ===")
    print(f"股票: {list(stocks.values()) or list(stocks.keys())}")

    indicators_list = []
    for code, name in stocks.items():
        print(f"  拉取 {name}({code})...")
        try:
            data = analyze(code, count=60)
            ind = extract_indicators(data, name)
            indicators_list.append(ind)
            print(f"    {ind['signal']} {ind['price']} 量比{ind['vol_ratio']}")
        except Exception as e:
            print(f"    ✗ {e}")

    if not indicators_list:
        print("✗ 无数据")
        return

    print("\n调用 DeepSeek...")
    analysis = call_deepseek(indicators_list)
    print(analysis)

    log_path = write_watch_log(analysis, indicators_list)
    print(f"\n✓ 写入 {log_path.name}")
    git_push(log_path)

if __name__ == "__main__":
    main()
