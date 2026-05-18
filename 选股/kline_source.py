"""
K线数据统一获取层

优先读取通达信本地 .day 文件（通过 mootdx），滞后超过阈值时用
mootdx TCP 协议向 TDX 服务器补缺。TDX 不可用时降级到东方财富 HTTP。

用法:
    from 选股.kline_source import get_klines
    name, klines = get_klines("000021", count=370, period="day")
"""

import sys
import time
import threading
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── 路径处理 ──
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "脚本"
sys.path.insert(0, str(_SCRIPTS_DIR))

from fetch_wave_data import fetch_kline, get_market

# 延迟导入 config，避免循环
_TDX_DIR = None
_TDX_STALE_DAYS = None


def _get_config():
    """延迟加载配置，避免循环导入"""
    global _TDX_DIR, _TDX_STALE_DAYS
    if _TDX_DIR is None:
        from 选股.config import TDX_DATA_DIR, TDX_STALE_DAYS
        _TDX_DIR = TDX_DATA_DIR
        _TDX_STALE_DAYS = TDX_STALE_DAYS
    return _TDX_DIR, _TDX_STALE_DAYS


# ── mootdx Reader 非线程安全，需要锁 ──
_reader_lock = threading.Lock()
_reader_cache = None


def _get_reader():
    """获取（缓存的）mootdx Reader 实例"""
    global _reader_cache
    if _reader_cache is not None:
        return _reader_cache
    with _reader_lock:
        if _reader_cache is not None:
            return _reader_cache
        from mootdx.reader import Reader
        tdx_dir, _ = _get_config()
        _reader_cache = Reader.factory(market='std', tdxdir=tdx_dir)
        return _reader_cache


# ═══════════════════════════════════════════════════════════════
# 交易日判断
# ═══════════════════════════════════════════════════════════════

def _is_weekday(d: date = None) -> bool:
    """周一至周五视为交易日"""
    if d is None:
        d = date.today()
    return d.weekday() < 5


def _count_trading_gaps(latest_data_date: date, today: date) -> int:
    """估算 latest_data_date 到 today 之间缺失的交易日数"""
    if latest_data_date >= today:
        return 0
    gap = 0
    d = latest_data_date + timedelta(days=1)
    while d <= today:
        if _is_weekday(d):
            gap += 1
        d += timedelta(days=1)
    return gap


# ═══════════════════════════════════════════════════════════════
# TDX .day 文件读取
# ═══════════════════════════════════════════════════════════════

def _read_tdx_daily(code: str, count: int) -> tuple[str, list[dict]] | None:
    """
    从通达信 .day 文件读取日K线，返回 (name, klines_list) 或 None。

    返回的每条 kline 包含字段与 fetch_kline 完全对齐:
      date, open, close, high, low, volume, amount, amplitude, pct_chg, change, turnover
    """
    try:
        reader = _get_reader()
        with _reader_lock:
            df = reader.daily(symbol=code)
    except Exception:
        return None

    if df is None or df.empty:
        return None

    # 取最近 count 根
    df = df.sort_index().tail(count)
    klines = []
    prev_close = None

    for idx, row in df.iterrows():
        d = idx.strftime('%Y-%m-%d') if hasattr(idx, 'strftime') else str(idx)[:10]
        o = float(row['open'])
        h = float(row['high'])
        l = float(row['low'])
        c = float(row['close'])
        v = float(row['volume'])
        a = float(row['amount'])

        pct = ((c - prev_close) / prev_close * 100) if prev_close and prev_close > 0 else 0.0
        chg = (c - prev_close) if prev_close else 0.0
        amp = ((h - l) / prev_close * 100) if prev_close and prev_close > 0 else 0.0

        klines.append({
            "date": d,
            "open": o, "close": c, "high": h, "low": l,
            "volume": v, "amount": a,
            "amplitude": round(amp, 2),
            "pct_chg": round(pct, 2),
            "change": round(chg, 2),
            "turnover": 0.0,
        })
        prev_close = c

    # 通达信 .day 文件不含股票名称，返回代码作为名称
    return (code, klines)


# ═══════════════════════════════════════════════════════════════
# TCP 补缺（mootdx 连接 TDX 服务器）
# ═══════════════════════════════════════════════════════════════

# TDX 服务器池，来自 mootdx config，启动时加载一次
_tcp_servers = None
_tcp_server_lock = threading.Lock()


def _get_tcp_servers() -> list[tuple[str, int]]:
    """读取 mootdx config 中的 HQ 服务器列表"""
    global _tcp_servers
    if _tcp_servers is not None:
        return _tcp_servers
    with _tcp_server_lock:
        if _tcp_servers is not None:
            return _tcp_servers
        servers = []
        try:
            import json
            config_path = Path.home() / ".mootdx" / "config.json"
            if config_path.exists():
                data = json.loads(config_path.read_text(encoding="utf-8"))
                for s in data.get("SERVER", {}).get("HQ", []):
                    servers.append((s[1], int(s[2])))
        except Exception:
            pass
        if not servers:
            servers = [("110.41.147.114", 7709)]
        _tcp_servers = servers
        return _tcp_servers


def _normalize_tcp_bar(bar: dict, prev_close: float) -> dict:
    """
    将 mootdx TCP 返回的 K 线记录转为标准 dict 格式。

    TCP 字段: open, close, high, low, vol(手), amount, year, month, day
    标准格式: date, open, close, high, low, volume, amount, amplitude, pct_chg, change, turnover
    """
    d = f"{bar['year']}-{bar['month']:02d}-{bar['day']:02d}"
    c = float(bar['close'])
    h = float(bar['high'])
    l = float(bar['low'])
    o = float(bar['open'])
    v = float(bar['vol'])
    a = float(bar.get('amount', 0))

    pct = ((c - prev_close) / prev_close * 100) if prev_close and prev_close > 0 else 0.0
    chg = (c - prev_close) if prev_close else 0.0
    amp = ((h - l) / prev_close * 100) if prev_close and prev_close > 0 else 0.0

    return {
        "date": d,
        "open": o, "close": c, "high": h, "low": l,
        "volume": v, "amount": a,
        "amplitude": round(amp, 2),
        "pct_chg": round(pct, 2),
        "change": round(chg, 2),
        "turnover": 0.0,
    }


def _fetch_recent_bars(code: str, since_date: str, max_bars: int = 20) -> list[dict] | None:
    """
    补缺最近 K 线：优先 mootdx TCP，失败降级到东方财富 HTTP。

    Args:
        code: 股票代码
        since_date: 只保留此日期及之后的 bar
        max_bars: 最多拉取根数

    Returns:
        list[dict] 标准 kline 格式，或 None
    """
    bars = _fetch_via_tcp(code, max_bars)
    if bars is None:
        bars = _fetch_via_http(code, max_bars)
    if bars is None:
        return None

    # 过滤 since_date 之后
    fresh = [k for k in bars if k["date"] >= since_date]

    # 盘中安全：若今日未收盘，排除不完整 bar
    today_str = date.today().strftime("%Y-%m-%d")
    from datetime import datetime as _dt
    now = _dt.now()
    market_closed = (now.hour > 15) or (now.hour == 15 and now.minute >= 30)
    if not market_closed and _is_weekday(date.today()):
        fresh = [k for k in fresh if k["date"] != today_str]

    return fresh


def _fetch_via_tcp(code: str, count: int) -> list[dict] | None:
    """通过 mootdx TCP 协议拉取 K 线，失败返回 None"""
    from mootdx.quotes import TdxHq_API

    servers = _get_tcp_servers()
    for host, port in servers:
        api = None
        try:
            api = TdxHq_API()
            api.connect(host, port)

            # market: 0=深圳, 1=上海
            mkt = 1 if code.startswith("6") else 0
            raw = api.get_security_bars(9, mkt, code, 0, count)
            api.disconnect()

            if not raw:
                continue

            # 逐条归一化（需要前一日的 close 来计算 pct_chg）
            klines = []
            prev_close = None
            for bar in raw:
                k = _normalize_tcp_bar(bar, prev_close or float(bar.get('close', 0)))
                klines.append(k)
                prev_close = k["close"]

            return klines
        except Exception:
            if api:
                try:
                    api.disconnect()
                except Exception:
                    pass
            continue

    return None


def _fetch_via_http(code: str, count: int) -> list[dict] | None:
    """降级：通过东方财富 HTTP 拉取 K 线"""
    try:
        _, api_klines = fetch_kline(code, count=count, period="day")
        return api_klines
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
# 合并结果缓存（避免同日重复扫描时反复调用 API 补缺）
# ═══════════════════════════════════════════════════════════════

_CACHE_DIR = _PROJECT_ROOT / "选股" / "kline_cache" / "day"


def _get_cache_path(code: str) -> Path:
    """返回某只股票的合并缓存文件路径"""
    return _CACHE_DIR / f"{code}_day.json"


def _load_cache(code: str, today_str: str) -> tuple[str, list[dict]] | None:
    """加载当日缓存，若缓存日期不是今日则返回 None"""
    p = _get_cache_path(code)
    if not p.exists():
        return None
    try:
        import json
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("fetch_date") == today_str:
            return data["name"], data["klines"]
    except Exception:
        pass
    return None


def _save_cache(code: str, name: str, klines: list[dict]):
    """写入合并后的 K 线缓存（当日有效）"""
    import json
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "fetch_date": date.today().strftime("%Y-%m-%d"),
            "name": name,
            "klines": klines,
        }
        p = _get_cache_path(code)
        p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass  # 缓存写入失败不影响主流程


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

def _strip_today_incomplete(klines: list[dict]) -> list[dict]:
    """若今日未收盘，移除今日的不完整日K bar"""
    from datetime import datetime as _dt
    today_str = date.today().strftime("%Y-%m-%d")
    now = _dt.now()
    market_closed = (now.hour > 15) or (now.hour == 15 and now.minute >= 30)
    if not market_closed and _is_weekday(date.today()):
        return [k for k in klines if k["date"] != today_str]
    return klines


def get_klines(code: str, count: int = 370, period: str = "day") -> tuple[str, list[dict]]:
    """
    获取股票 K 线数据（自动选择最优数据源）。

    Args:
        code: 股票代码，如 "000021"
        count: 需要获取的 K 线根数
        period: 周期，仅 "day" 支持 TDX 本地读取

    Returns:
        (name, klines_list) — 与 fetch_kline 完全相同的格式

    Raises:
        ConnectionError: 所有数据源均不可用
    """
    # 非日K周期 → 直接走 API
    if period != "day":
        return fetch_kline(code, count, period)

    today_str = date.today().strftime("%Y-%m-%d")

    # ── 检查当日缓存（同日重复扫描直接命中）──
    cached = _load_cache(code, today_str)
    if cached is not None:
        name, klines = cached
        if len(klines) > count:
            klines = klines[-count:]
        return (name, klines)

    tdx_dir, stale_days = _get_config()
    result = None  # (name, klines) or None

    # ── 尝试 TDX 本地读取 ──
    tdx_result = _read_tdx_daily(code, count)
    if tdx_result is not None:
        name, klines = tdx_result

        if klines:
            klines = _strip_today_incomplete(klines)
            if not klines:
                return (name, [])

            last_date_str = klines[-1]["date"]
            last_date = date.fromisoformat(last_date_str)
            today = date.today()

            gap = _count_trading_gaps(last_date, today)

            if gap <= stale_days:
                result = (name, klines)
            else:
                fresh = _fetch_recent_bars(code, last_date_str, max_bars=max(10, gap + 5))
                if fresh:
                    existing_dates = {k["date"] for k in klines}
                    for bar in fresh:
                        if bar["date"] not in existing_dates:
                            klines.append(bar)
                            existing_dates.add(bar["date"])
                    klines.sort(key=lambda x: x["date"])
                    klines = klines[-count:]
                    result = (name, klines)
                else:
                    return (name, klines)

    # ── 降级：全量 API ──
    if result is None:
        result = fetch_kline(code, count, period)
        if result is not None:
            name, klines = result
            klines = _strip_today_incomplete(klines)
            result = (name, klines)

    # ── 写入缓存（仅当数据已足够新鲜）──
    if result is not None:
        k = result[1]
        if k:
            last_str = k[-1]["date"]
            g = _count_trading_gaps(date.fromisoformat(last_str), date.today())
            if g <= stale_days:
                _save_cache(code, result[0], k)

    return result


# ═══════════════════════════════════════════════════════════════
# 缓存预热（可选）
# ═══════════════════════════════════════════════════════════════

def preload_name_map(codes: list[str]) -> dict[str, str]:
    """
    批量获取股票名称映射。
    从东方财富 API 批量拉取（一次请求获取多只股票的名称）。

    Returns:
        {code: name, ...}
    """
    # 东方财富批量行情接口
    if not codes:
        return {}
    try:
        markets = [get_market(c) for c in codes]
        secids = [f"{m}.{c}" for m, c in zip(markets, codes)]
        url = "http://push2.eastmoney.com/api/qt/ulist.np/get"
        params = {
            "secids": ",".join(secids[:200]),
            "fields": "f57,f58",
            "fltt": "1",
        }
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.eastmoney.com/",
        }
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            stocks = data.get("diff", [])
            result = {}
            for s in (stocks or []):
                code = s.get("f57", "")[-6:] if len(s.get("f57", "")) >= 6 else ""
                name = s.get("f58", "")
                if code and name:
                    result[code] = name
            return result
    except Exception:
        pass
    return {}
