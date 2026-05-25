"""
波浪交易看板 — 独立服务

启动: uvicorn server.app:app --host 127.0.0.1 --port 8002

API 端点:
    GET  /                        看板页面
    GET  /stock_dashboard         看板页面

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

from fastapi import Body, FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader

# ── 路径处理：确保 server/ 下能导入项目根目录的模块 ──
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

from server.scan_manager import get_manager
from server.tracker import get_tracker
from server.holdings_manager import get_holdings_manager

app = FastAPI(title="波浪交易看板")

_template_dir = os.path.join(os.path.dirname(__file__), "templates")
_jinja_env = Environment(loader=FileSystemLoader(_template_dir), autoescape=True)

# ── 页面路由 ──────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
@app.get("/stock_dashboard", response_class=HTMLResponse)
def stock_dashboard():
    """波浪交易看板"""
    template = _jinja_env.get_template("wave.html")
    return HTMLResponse(template.render())


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


# ── API：实时行情 ────────────────────────────────────────────────

@app.get("/api/quotes")
def api_get_quotes(
    codes: str = Query("", description="逗号分隔的股票代码"),
):
    """
    获取实时行情快照（TDX TCP 主源，东方财富降级）。

    GET /api/quotes?codes=000021,600519,000001
    """
    if not codes:
        return JSONResponse({"error": "请提供股票代码，如 ?codes=000021,600519"}, status_code=400)

    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not code_list:
        return JSONResponse({"error": "无效代码"}, status_code=400)

    try:
        from 选股.tdx_pool import get_pool
        pool = get_pool()
        quotes = pool.get_quotes_batch(code_list)
        if quotes:
            return {"quotes": quotes, "source": "tdx_tcp", "count": len(quotes)}
    except Exception:
        pass

    # 降级：东方财富 HTTP 批量行情
    try:
        import requests
        markets = []
        for c in code_list:
            markets.append("1" if c.startswith("6") else "0")
        secids = [f"{m}.{c}" for m, c in zip(markets, code_list)]
        url = "http://push2.eastmoney.com/api/qt/ulist.np/get"
        params = {
            "secids": ",".join(secids[:50]),
            "fields": "f2,f3,f12,f14,f15,f16,f17,f18",
            "fltt": "2",
        }
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.eastmoney.com/"}
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            items = data.get("diff", [])
            quotes = []
            for item in (items or []):
                quotes.append({
                    "code": item.get("f12", ""),
                    "name": item.get("f14", ""),
                    "price": item.get("f2", 0),
                    "pct_chg": item.get("f3", 0),
                    "high": item.get("f15", 0),
                    "low": item.get("f16", 0),
                    "open": item.get("f17", 0),
                    "volume": item.get("f18", 0),
                })
            return {"quotes": quotes, "source": "eastmoney", "count": len(quotes)}
    except Exception:
        pass

    return JSONResponse({"error": "行情数据不可用"}, status_code=503)


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


# ── API：持仓管理 ────────────────────────────────────────────────

_holdings_mgr = get_holdings_manager()


@app.get("/api/holdings")
def api_get_holdings(status: str = Query("all", description="过滤状态: open/closed/all")):
    """获取持仓交易列表"""
    trades = _holdings_mgr.get_trades(status_filter=status)
    return {"trades": trades}


@app.post("/api/holdings")
def api_add_trade(body: dict = Body(...)):
    """新增持仓交易"""
    required = {"stock_name", "stock_code", "buy_date", "cost_price", "shares", "buy_strategy"}
    missing = required - set(body.keys())
    if missing:
        return JSONResponse({"error": f"缺少字段: {', '.join(missing)}"}, status_code=422)
    trade = _holdings_mgr.add_trade(
        stock_name=body["stock_name"],
        stock_code=body["stock_code"],
        buy_date=body["buy_date"],
        cost_price=float(body["cost_price"]),
        shares=int(body["shares"]),
        buy_strategy=body["buy_strategy"],
    )
    return {"trade": trade}


@app.put("/api/holdings/{trade_id}")
def api_edit_trade(trade_id: str, body: dict = Body(...)):
    """编辑持仓交易"""
    if _holdings_mgr.edit_trade(trade_id, body):
        return {"status": "ok"}
    return JSONResponse({"error": "交易不存在或已关闭"}, status_code=400)


@app.post("/api/holdings/{trade_id}/close")
def api_close_trade(trade_id: str, body: dict = Body(...)):
    """清仓"""
    required = {"sell_date", "sell_price", "sell_strategy"}
    missing = required - set(body.keys())
    if missing:
        return JSONResponse({"error": f"缺少字段: {', '.join(missing)}"}, status_code=422)
    trade = _holdings_mgr.close_trade(
        trade_id=trade_id,
        sell_date=body["sell_date"],
        sell_price=float(body["sell_price"]),
        sell_strategy=body["sell_strategy"],
    )
    if trade is None:
        return JSONResponse({"error": "交易不存在或已关闭"}, status_code=400)
    return {"trade": trade}


@app.delete("/api/holdings/{trade_id}")
def api_delete_trade(trade_id: str):
    """删除交易记录"""
    if _holdings_mgr.delete_trade(trade_id):
        return {"status": "deleted"}
    return JSONResponse({"error": "交易不存在"}, status_code=404)


@app.get("/api/holdings/strategies")
def api_get_strategies():
    """获取买入/卖出策略下拉选项"""
    return _holdings_mgr.get_strategies()


# ── API：账户净值 ────────────────────────────────────────────────

@app.get("/api/nav")
def api_get_nav():
    """获取净值历史"""
    return _holdings_mgr.get_nav()


@app.post("/api/nav/init")
def api_init_nav(body: dict = Body(...)):
    """设置初始净值"""
    if "initial_nav" not in body or "initial_date" not in body:
        return JSONResponse({"error": "缺少 initial_nav 或 initial_date"}, status_code=422)
    nav = _holdings_mgr.init_nav(
        initial_nav=float(body["initial_nav"]),
        initial_date=body["initial_date"],
    )
    return nav


@app.post("/api/nav/adjust")
def api_adjust_nav(body: dict = Body(...)):
    """出入金调整"""
    required = {"amount", "direction", "date"}
    missing = required - set(body.keys())
    if missing:
        return JSONResponse({"error": f"缺少字段: {', '.join(missing)}"}, status_code=422)
    if body["direction"] not in ("deposit", "withdraw"):
        return JSONResponse({"error": "direction 必须为 deposit 或 withdraw"}, status_code=422)
    nav = _holdings_mgr.adjust_nav(
        amount=float(body["amount"]),
        direction=body["direction"],
        date=body["date"],
        note=body.get("note", ""),
    )
    return nav


# ── API：数据源诊断 ────────────────────────────────────────────

@app.get("/api/sources")
def api_data_sources():
    """数据源状态诊断"""
    from pathlib import Path
    from 选股.config import TDX_DATA_DIR

    tdx_local = Path(TDX_DATA_DIR).exists() if TDX_DATA_DIR else False
    vipdoc = Path(TDX_DATA_DIR) / "vipdoc" if TDX_DATA_DIR else None
    lday_sh = (vipdoc / "sh" / "lday").exists() if vipdoc else False
    lday_sz = (vipdoc / "sz" / "lday").exists() if vipdoc else False

    tdx_tcp = {"connected": False, "server": None, "healthy_servers": 0}
    try:
        from 选股.tdx_pool import get_pool
        pool = get_pool()
        tdx_tcp["connected"] = pool.is_connected()
        tdx_tcp["server"] = f"{pool._host}:{pool._port}" if pool._host else None
        tdx_tcp["healthy_servers"] = len(pool._healthy_servers)
    except Exception:
        pass

    return {
        "tdx_local": {
            "dir": TDX_DATA_DIR,
            "exists": tdx_local,
            "vipdoc_sh_lday": lday_sh,
            "vipdoc_sz_lday": lday_sz,
        },
        "tdx_tcp": tdx_tcp,
        "fallback": {
            "eastmoney_http": True,
            "sina_http": True,
            "use_tdx": True,
        },
    }


