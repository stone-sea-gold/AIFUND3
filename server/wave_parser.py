"""
波浪交易看板 — 数据解析模块
从 WaveformTheory Obsidian 笔记库中提取持仓、操作记录、策略等结构化数据
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

VAULT = Path(__file__).resolve().parent.parent


def _safe_read(path: Path) -> str | None:
    """安全读取文件，不存在返回 None"""
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return None


# ── 通用表格解析 ───────────────────────────────────────────────
def _parse_frontmatter(text: str) -> dict[str, str]:
    """解析 markdown frontmatter 的简单键值。"""
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    frontmatter = text[4:end].splitlines()
    data: dict[str, str] = {}
    for line in frontmatter:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def _parse_table(text: str, anchor: str | None = None):
    """从 markdown 文本中按表头关键词定位并解析表格，返回 list[dict]"""
    lines = text.split("\n")
    rows, headers = [], []
    found = anchor is None
    in_table = False
    for line in lines:
        stripped = line.strip()
        if not found:
            if anchor and anchor in stripped:
                found = True
                # anchor line is the header — parse it now
                headers = [c.strip() for c in stripped.split("|")[1:-1]]
                in_table = True
            continue
        if stripped.startswith("|") and "---" in stripped:
            continue
        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if not in_table:
                headers = cells
                in_table = True
            else:
                rows.append(dict(zip(headers, cells)))
        elif in_table:
            break
    return rows


# ── 持仓 ──────────────────────────────────────────────────────
def _load_stock_profiles() -> tuple[dict[str, dict], dict[str, dict]]:
    stock_dir = VAULT / "持仓"
    by_code: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    if not stock_dir.is_dir():
        return by_code, by_name
    skip = {"仓位总览.md", "持仓操作报告.md", "持仓看板.base"}
    for fp in stock_dir.glob("*.md"):
        if fp.name in skip:
            continue
        meta = _parse_frontmatter(fp.read_text(encoding="utf-8"))
        if not meta:
            continue
        name = fp.stem
        code = meta.get("code", "").strip('"')
        profile = {
            "strategy": meta.get("strategy", ""),
            "backtest_return": meta.get("backtest_return", ""),
            "win_rate": meta.get("win_rate", ""),
            "stop_loss": meta.get("stop_loss", ""),
            "stop_loss_hard": meta.get("stop_loss_hard", ""),
            "target": meta.get("target", ""),
            "risk_level": meta.get("risk_level", ""),
        }
        if code:
            by_code[code] = profile
        by_name[name] = profile
    return by_code, by_name


def parse_holdings() -> list[dict]:
    content = _safe_read(VAULT / "持仓/仓位总览.md")
    if content is None:
        return []
    raw = _parse_table(content, "| 股票 |")
    profiles_by_code, profiles_by_name = _load_stock_profiles()
    holdings = []
    for r in raw:
        name = r.get("股票", "").replace("**", "").strip()
        code = r.get("代码", "").strip()
        macd_raw = r.get("MACD(5,34,5)", "").replace("**", "").strip()
        pnl_str = r.get("盈亏", "").strip()
        profile = profiles_by_code.get(code) or profiles_by_name.get(name) or {}
        holdings.append(
            {
                "name": name,
                "code": code,
                "cost": r.get("成本", "").strip(),
                "price": _get_price(r),
                "pnl": pnl_str,
                "pnl_val": _pct_to_float(pnl_str),
                "macd": macd_raw,
                "position": r.get("仓位", "").replace("**", "").strip(),
                "is_cash": "现金" in name or code == "—",
                "is_golden": "金叉" in macd_raw,
                "is_death": "死叉" in macd_raw,
                "strategy": profile.get("strategy", ""),
                "backtest_return": profile.get("backtest_return", ""),
                "win_rate": profile.get("win_rate", ""),
                "stop_loss": profile.get("stop_loss", ""),
                "stop_loss_hard": profile.get("stop_loss_hard", ""),
                "target": profile.get("target", ""),
                "risk_level": profile.get("risk_level", ""),
            }
        )
    return holdings


def parse_macd_detail() -> list[dict]:
    content = _safe_read(VAULT / "持仓/仓位总览.md")
    if content is None:
        return []
    return _parse_table(content, "| 股票 |")  # second table has DIF


def parse_position_plan() -> list[dict]:
    content = _safe_read(VAULT / "持仓/仓位总览.md")
    if content is None:
        return []
    return _parse_table(content, "| 股票 | 当前仓位")


# ── 操作要点 ──────────────────────────────────────────────────
def parse_action_items() -> list[dict]:
    content = _safe_read(VAULT / "持仓/仓位总览.md")
    if content is None:
        return []
    match = re.search(
        r"## (?:下周|本周)操作要点(.*?)\n(.*?)(?=\n## |\Z)", content, re.DOTALL
    )
    if not match:
        return []
    title_line = match.group(1).strip()
    section = match.group(2)
    actions = []
    parts = re.split(r"### (\d+\..+)", section)
    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        table = _parse_table(body)
        bullets = [
            ln.strip().lstrip("- ")
            for ln in body.split("\n")
            if ln.strip().startswith("- ")
        ]
        actions.append({"title": title, "table": table, "notes": bullets})
    return actions


# ── 日志 ──────────────────────────────────────────────────────
def parse_daily_logs(days: int = 7) -> list[dict]:
    log_dir = VAULT / "日志"
    if not log_dir.is_dir():
        return []
    logs = sorted(log_dir.glob("*.md"), reverse=True)[:days]
    result = []
    for fp in logs:
        content = _safe_read(fp)
        if content is None:
            continue
        date = fp.stem
        wd_m = re.search(r"weekday:\s*(.*)", content)
        weekday = wd_m.group(1).strip() if wd_m else ""

        ops = _parse_table(content, "| 操作 |")

        summary_m = re.search(r"## 日总结\n+(.*?)(?=\n#|\Z)", content, re.DOTALL)
        summary = summary_m.group(1).strip() if summary_m else ""

        risk_m = re.search(r"## 风险提示\n+(.*?)(?=\n##|\Z)", content, re.DOTALL)
        risks = []
        if risk_m:
            risks = [
                ln.strip().lstrip("- ")
                for ln in risk_m.group(1).split("\n")
                if ln.strip().startswith("- ")
            ]

        good_m = re.search(r"## 利好信号\n+(.*?)(?=\n##|\Z)", content, re.DOTALL)
        goods = []
        if good_m:
            goods = [
                ln.strip().lstrip("- ")
                for ln in good_m.group(1).split("\n")
                if ln.strip().startswith("- ")
            ]

        result.append(
            {
                "date": date,
                "weekday": weekday,
                "operations": ops,
                "summary": summary,
                "risks": risks,
                "positives": goods,
            }
        )
    return result


# ── 回测 ──────────────────────────────────────────────────────
def load_backtest() -> dict | None:
    p = Path(__file__).parent / "wave_backtest.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


# ── 更新日期 ──────────────────────────────────────────────────
def get_update_date() -> str:
    """获取本地K线数据的最新日期（优先TDX，降级到持仓笔记）"""
    # 优先：读取通达信本地 .day 文件的最新日期
    try:
        from 选股.config import TDX_DATA_DIR
        import os as _os
        import struct as _struct
        # 用平安银行(000001)作为样本，检测数据新鲜度
        fpath = _os.path.join(TDX_DATA_DIR, "vipdoc", "sz", "lday", "sz000001.day")
        if _os.path.exists(fpath):
            with open(fpath, 'rb') as f:
                f.seek(-32, 2)  # 最后一条 32 字节记录
                data = f.read(32)
                date_int = _struct.unpack('<I', data[0:4])[0]
                return f"{date_int // 10000}-{(date_int % 10000) // 100:02d}-{date_int % 100:02d}"
    except Exception:
        pass

    # 降级：读取持仓笔记中的手动日期
    content = _safe_read(VAULT / "持仓/仓位总览.md")
    if content is None:
        return "—"
    m = re.search(r"更新至.*?\[\[(\d{4}-\d{2}-\d{2})\]\]", content)
    return m.group(1) if m else "未知"


# ── 工具 ──────────────────────────────────────────────────────
def _pct_to_float(s: str) -> float:
    m = re.search(r"([+-]?\d+\.?\d*)%", s)
    return float(m.group(1)) if m else 0.0


def _get_price(row: dict) -> str:
    """从表格行中提取现价，兼容 现价(4/10)、现价(4/14) 等动态列名"""
    for key in row:
        if key.startswith("现价"):
            return row[key].strip()
    return ""
