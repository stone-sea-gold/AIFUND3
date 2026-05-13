"""
报告生成器 — 将扫描结果输出为 Markdown 报告
"""

import os
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from 选股.config import OUTPUT_DIR, STRATEGY
from 选股.strategy_loader import load_strategy

# ── 加载策略 ──
_strategy = load_strategy(STRATEGY)


def generate_markdown(results: list[dict], pool_name: str, top_n: int) -> str:
    """生成 Markdown 格式选股报告"""
    today = date.today().strftime("%Y-%m-%d")
    categories = _strategy.REPORT_CATEGORIES

    # 按策略分类信号
    category_results = {}
    for cat in categories:
        cat_name = cat["name"]
        cat_results = []
        for r in results:
            for d in r["details"]:
                if d["criterion"] in cat["criteria_keys"]:
                    cat_results.append(r)
                    break
        category_results[cat_name] = cat_results

    lines = []
    lines.append(f"# 选股结果 {today}")
    lines.append("")
    lines.append(f"> 扫描范围：{pool_name} | 合格：{len(results)} 只 | 前 {top_n} 名")
    lines.append("")
    lines.append("---")
    lines.append("")

    if not results:
        lines.append("## 无符合条件的标的")
        lines.append("")
        lines.append("当前市场条件下，暂无触发买入信号的标的。")
        lines.append("")
        return "\n".join(lines)

    # ═══ Top N 排行 ═══
    lines.append("## Top 排名")
    lines.append("")
    lines.append("| # | 股票 | 代码 | 得分 | 现价 | 涨跌 | 核心信号 |")
    lines.append("|---|------|------|------|------|------|----------|")
    for i, r in enumerate(results, 1):
        info = r["latest_info"]
        sign = "+" if info["pct_chg"] >= 0 else ""
        top_signals = sorted(r["details"], key=lambda x: x["score"], reverse=True)[:2]
        signal_desc = " + ".join(
            d["desc"].replace("（", "").replace("）", "")
            for d in top_signals
        )
        lines.append(
            f"| {i} | {r['name']} | {r['code']} | **{r['score']}** | "
            f"{info['close']:.2f} | {sign}{info['pct_chg']:.2f}% | {signal_desc} |"
        )
    lines.append("")

    # ═══ 逐票详析 ═══
    lines.append("---")
    lines.append("")
    lines.append("## 逐票分析")
    lines.append("")

    for i, r in enumerate(results, 1):
        info = r["latest_info"]
        ind = r["indicators"]
        sign = "+" if info["pct_chg"] >= 0 else ""

        lines.append(f"### {i}. {r['name']}（{r['code']}） 总分：{r['score']}")
        lines.append("")

        lines.append("| 项目 | 数值 |")
        lines.append("|------|------|")
        lines.append(f"| 现价 | {info['close']:.2f} |")
        lines.append(f"| 涨跌 | {sign}{info['pct_chg']:.2f}% |")
        lines.append(f"| 日期 | {info['date']} |")

        # 策略指定的附加摘要字段
        for spec in _strategy.LATEST_INFO_EXTRA:
            val = info.get(spec["key"])
            if val is not None:
                fmt = spec.get("format", ".4f")
                lines.append(f"| {spec['label']} | {val:{fmt}} |")

        # 策略指定的指标字段
        for spec in _strategy.RESULT_INDICATORS:
            val = ind.get(spec["key"])
            if val is not None:
                fmt = spec.get("format", ".2f")
                lines.append(f"| {spec['label']} | {val:{fmt}} |")
            else:
                lines.append(f"| {spec['label']} | — |")
        lines.append("")

        lines.append("**匹配的条件：**")
        lines.append("")
        lines.append("| 条件 | 得分 | 权重 | 说明 |")
        lines.append("|------|------|------|------|")
        for d in sorted(r["details"], key=lambda x: x["score"], reverse=True):
            detail_reason = d["detail"].get("reason", "—")
            lines.append(f"| {d['desc']} | {d['score']}/{d['weight']} | {d['weight']} | {detail_reason} |")
        lines.append("")
        lines.append("---")
        lines.append("")

    # ═══ 按策略分类 ═══
    lines.append("## 按策略分类")
    lines.append("")

    for cat in categories:
        cat_name = cat["name"]
        cat_results = category_results.get(cat_name, [])
        if not cat_results:
            continue
        lines.append(f"### {cat_name}")
        lines.append("")
        for r in cat_results:
            info = r["latest_info"]
            ind = r["indicators"]
            parts = [f"- **{r['name']}({r['code']})** 得分{r['score']} | 价{info['close']:.2f}"]
            for spec in _strategy.LATEST_INFO_EXTRA:
                val = info.get(spec["key"])
                if val is not None:
                    fmt = spec.get("format", ".4f")
                    parts.append(f"{spec['label']}={val:{fmt}}")
            for spec in _strategy.RESULT_INDICATORS:
                val = ind.get(spec["key"])
                if val is not None:
                    fmt = spec.get("format", ".2f")
                    parts.append(f"{spec['label']}={val:{fmt}}")
            lines.append(" | ".join(parts))
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"*自动生成于 {today} | 选股模块 v2.0*")
    lines.append("")
    lines.append("> 风险提示：本报告仅供学习研究参考，不构成投资建议。")

    return "\n".join(lines)


def save_report(markdown: str, pool_name: str = "") -> str:
    """保存报告到 选股结果/ 目录，返回文件路径"""
    today = date.today().strftime("%Y-%m-%d")
    out_dir = PROJECT_ROOT / OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    name_parts = [today]
    if pool_name:
        name_parts.append(pool_name.replace(" ", "_"))
    filename = f"{'_'.join(name_parts)}.md"
    filepath = out_dir / filename
    filepath.write_text(markdown, encoding="utf-8")
    return str(filepath)


def print_summary(results: list[dict]):
    """终端打印摘要"""
    print(f"\n{'═' * 60}")
    print(f"  选股结果 Top {len(results)}")
    print(f"{'═' * 60}")
    for i, r in enumerate(results, 1):
        info = r["latest_info"]
        sign = "+" if info["pct_chg"] >= 0 else ""
        # 策略指定的附加信息
        extra_parts = []
        for spec in _strategy.LATEST_INFO_EXTRA:
            val = info.get(spec["key"])
            if val is not None:
                fmt = spec.get("format", ".4f")
                extra_parts.append(f"{spec['label']}={val:{fmt}}")
        extra_str = "  |  " + " ".join(extra_parts) if extra_parts else ""
        print(f"\n  {i:2d}. {r['name']} ({r['code']})  得分: {r['score']}")
        print(f"      现价: {info['close']:.2f}  {sign}{info['pct_chg']:.2f}%{extra_str}")
        for d in r["details"]:
            detail_reason = d["detail"].get("reason", "—")
            print(f"      [{d['score']}/{d['weight']}] {d['desc']}: {detail_reason}")
    print(f"\n{'═' * 60}\n")
