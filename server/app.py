"""
波浪交易看板 — 独立服务

启动: uvicorn server.app:app --host 127.0.0.1 --port 8002

API 端点:
    GET  /                        看板页面
    GET  /stock_dashboard         看板页面
    GET  /stock_selection         选股页面（同模板，JS 切换 Tab）

    GET  /api/pools               可用股票池列表
    GET  /api/strategies          可用选股策略列表
    POST /api/scan                提交选股扫描任务
    GET  /api/scan/tasks          所有扫描任务摘要
    GET  /api/scan/{task_id}      任务状态/进度
    GET  /api/scan/{task_id}/result  任务结果
    POST /api/scan/test/{code}    单票诊断
    GET  /api/reports             历史选股报告列表
    GET  /api/reports/{date}      查看指定日期报告
"""

import os
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader

# ── 路径处理：确保 server/ 下能导入项目根目录的模块 ──
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

from server.wave_parser import (
    parse_holdings, parse_action_items, parse_daily_logs,
    load_backtest, get_update_date,
)
from server.scan_manager import get_manager
from server.tracker import get_tracker

app = FastAPI(title="波浪交易看板")

_template_dir = os.path.join(os.path.dirname(__file__), "templates")
_jinja_env = Environment(loader=FileSystemLoader(_template_dir), autoescape=True)

# ── 页面路由 ──────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
@app.get("/stock_dashboard", response_class=HTMLResponse)
def stock_dashboard():
    """波浪交易看板"""
    holdings = parse_holdings()
    actions = parse_action_items()
    logs = parse_daily_logs(days=7)
    backtest = load_backtest()
    update_date = get_update_date()

    cash_h = [h for h in holdings if h["is_cash"]]
    cash_pct = cash_h[0]["position"] if cash_h else "?"
    cash_value = _parse_pct(cash_pct)
    cash_alert_level = None
    if cash_value < 15:
        cash_alert_level = "danger"
    elif cash_value < 30:
        cash_alert_level = "warning"

    death_stocks = [h["name"] for h in holdings if h["is_death"]]

    template = _jinja_env.get_template("wave.html")
    return HTMLResponse(template.render(
        holdings=holdings,
        actions=actions,
        logs=logs,
        backtest=backtest,
        update_date=update_date,
        cash_pct=cash_pct,
        cash_alert_level=cash_alert_level,
        death_stocks=death_stocks,
    ))


@app.get("/stock_selection", response_class=HTMLResponse)
def stock_selection_page():
    """选股页面（复 use 看板模板，JS 控制 Tab 切换）"""
    template = _jinja_env.get_template("wave.html")
    return HTMLResponse(template.render(
        holdings=[],
        actions=[],
        logs=[],
        backtest=None,
        update_date="",
        cash_pct="?",
        cash_alert_level=None,
        death_stocks=[],
    ))


# ── API：股票池 ───────────────────────────────────────────────

POOLS_INFO = [
    {"name": "全A",     "desc": "全部A股（~5000只，耗时长）"},
    {"name": "沪深主板", "desc": "沪市+深市主板（~3300只）"},
    {"name": "沪深300",  "desc": "沪深300成分股（~300只）"},
    {"name": "中证500",  "desc": "中证500成分股（~500只）"},
    {"name": "自选",    "desc": "观察仓/watchlist.txt 自选股"},
]


@app.get("/api/pools")
def api_list_pools():
    """列出可用股票池"""
    return {"pools": POOLS_INFO}


# ── API：策略列表 ─────────────────────────────────────────────

STRATEGIES_DIR = _PROJECT_ROOT / "选股" / "strategies"
_strategies_cache = None


def _discover_strategies():
    """扫描 strategies 目录，获取所有策略元信息"""
    global _strategies_cache
    if _strategies_cache is not None:
        return _strategies_cache

    from 选股.strategy_loader import load_strategy
    result = []
    for f in sorted(STRATEGIES_DIR.glob("*.py")):
        if f.stem.startswith("_"):
            continue
        try:
            mod = load_strategy(f.stem)
            result.append({
                "name": f.stem,
                "display_name": getattr(mod, "STRATEGY_NAME", f.stem),
                "desc": getattr(mod, "STRATEGY_DESC", ""),
            })
        except Exception:
            result.append({
                "name": f.stem,
                "display_name": f.stem,
                "desc": "加载失败",
            })
    _strategies_cache = result
    return result


@app.get("/api/strategies")
def api_list_strategies():
    """列出可用选股策略"""
    return {"strategies": _discover_strategies()}


# ── API：扫描任务 ─────────────────────────────────────────────

_manager = get_manager()


@app.post("/api/scan")
def api_submit_scan(
    pool: str = Query("沪深300", description="股票池名称"),
    strategy: str = Query("b1", description="策略名称"),
    top_n: int = Query(30, description="返回前N只"),
    min_score: int = Query(25, description="最低入围分"),
    delay: float = Query(0.15, description="请求间隔秒"),
    workers: int = Query(4, description="并发线程数"),
):
    """提交选股扫描任务"""
    task_id = _manager.submit_scan(
        pool_name=pool,
        strategy_name=strategy,
        top_n=top_n,
        min_score=min_score,
        delay=delay,
        workers=workers,
    )
    return {"task_id": task_id, "status": "submitted"}


@app.get("/api/scan/tasks")
def api_list_tasks():
    """所有扫描任务摘要"""
    return {"tasks": _manager.list_tasks()}


@app.get("/api/scan/{task_id}")
def api_task_status(task_id: str):
    """获取扫描任务状态/进度"""
    task = _manager.get_task(task_id)
    if not task:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    return {
        "task_id": task.task_id,
        "status": task.status,
        "pool_name": task.pool_name,
        "strategy_name": task.strategy_name,
        "progress": dict(task.progress),
        "error": task.error,
    }


@app.get("/api/scan/{task_id}/result")
def api_task_result(task_id: str):
    """获取扫描任务结果"""
    task = _manager.get_task(task_id)
    if not task:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    if task.status != "completed":
        return JSONResponse({"error": f"任务尚未完成（{task.status}）"}, status_code=400)
    return {
        "task_id": task.task_id,
        "status": "completed",
        "pool_name": task.pool_name,
        "strategy_name": task.strategy_name,
        "count": len(task.results),
        "results": task.results,
    }


@app.delete("/api/scan/{task_id}")
def api_delete_task(task_id: str):
    """删除已完成/已取消/已失败的任务"""
    if _manager.delete_task(task_id):
        return {"status": "deleted"}
    return JSONResponse({"error": "任务不存在或无法删除"}, status_code=400)


@app.post("/api/scan/{task_id}/stop")
def api_stop_task(task_id: str):
    """停止正在运行的扫描任务"""
    if _manager.stop_task(task_id):
        return {"status": "cancelling"}
    return JSONResponse({"error": "任务不存在或不在运行中"}, status_code=400)


# ── API：单票诊断 ─────────────────────────────────────────────

@app.post("/api/scan/test/{code}")
def api_test_stock(code: str, strategy: str = Query("b1", description="策略名称")):
    """单票诊断：使用指定策略对单一股票进行完整分析"""
    try:
        from 选股.strategy_loader import load_strategy
        from 选股.scanner import scan_one
        from 选股.pool import get_stock_pool

        s = load_strategy(strategy)
        # 尝试从自选股获取名称，否则用代码作名称
        name = code
        try:
            for pool_name in ("自选", "沪深300", "沪深主板"):
                stocks = get_stock_pool(pool_name)
                for c, n in stocks:
                    if c == code:
                        name = n
                        break
                if name != code:
                    break
        except Exception:
            pass

        r = scan_one(code, name, strategy=s)
        if r is None:
            return JSONResponse({
                "code": code,
                "name": name,
                "passed": False,
                "reason": "被排除（不满足基础条件或数据不可用）",
            })

        return {
            "code": r["code"],
            "name": r["name"],
            "score": r["score"],
            "passed": True,
            "details": [
                {
                    "criterion": d["criterion"],
                    "desc": d["desc"],
                    "score": d["score"],
                    "weight": d["weight"],
                    "detail": d["detail"],
                }
                for d in r["details"]
            ],
            "latest_info": dict(r["latest_info"]),
            "indicators": dict(r.get("indicators", {})),
        }
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── API：历史报告 ─────────────────────────────────────────────

REPORTS_DIR = _PROJECT_ROOT / "选股" / "选股结果"


@app.get("/api/reports")
def api_list_reports():
    """列出历史选股报告"""
    if not REPORTS_DIR.exists():
        return {"reports": []}
    files = sorted(REPORTS_DIR.glob("*.md"), reverse=True)
    reports = []
    for f in files:
        stat = f.stat()
        reports.append({
            "filename": f.name,
            "date": f.stem[:10] if len(f.stem) >= 10 else f.stem,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
        })
    return {"reports": reports}


@app.get("/api/reports/{date:path}")
def api_view_report(date: str):
    """查看指定日期的选股报告"""
    if not REPORTS_DIR.exists():
        return JSONResponse({"error": "报告目录不存在"}, status_code=404)
    files = sorted(REPORTS_DIR.glob(f"{date}*.md"))
    if not files:
        return JSONResponse({"error": f"未找到 {date} 的选股报告"}, status_code=404)
    return {
        "date": date,
        "reports": [
            {
                "filename": f.name,
                "content": f.read_text(encoding="utf-8"),
            }
            for f in files
        ],
    }


# ── API：选股跟踪看板 ────────────────────────────────────────

_tracker = get_tracker()


@app.get("/api/tracker")
def api_get_tracker():
    """获取选股跟踪数据（按策略分组）"""
    entries = _tracker.get_entries()
    grouped = _tracker.get_entries_grouped()
    return {
        "count": len(entries),
        "max": _tracker.max_entries,
        "grouped": grouped,
    }


@app.post("/api/tracker/refresh")
def api_refresh_tracker():
    """刷新所有跟踪标的最新收盘价"""
    result = _tracker.refresh_prices()
    grouped = _tracker.get_entries_grouped()
    return {
        "status": "ok",
        "refreshed": result["refreshed"],
        "failed": result.get("failed", 0),
        "skipped": result.get("skipped", 0),
        "refresh_time": result.get("refresh_time", ""),
        "grouped": grouped,
    }


@app.delete("/api/tracker/{entry_id}")
def api_delete_tracker_entry(entry_id: str):
    """删除指定跟踪记录"""
    if _tracker.delete_entry(entry_id):
        return {"status": "deleted"}
    return JSONResponse({"error": "条目不存在"}, status_code=404)


# ── 辅助 ──────────────────────────────────────────────────────

def _parse_pct(text: str) -> float:
    digits = "".join(ch for ch in text if ch.isdigit() or ch == ".")
    return float(digits) if digits else 0.0
