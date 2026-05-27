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
# TCP 补缺（走 tdx_pool 持久连接）
# ═══════════════════════════════════════════════════════════════

from 选股.tdx_pool import get_pool, normalize_tcp_bars


def _filter_complete_bars(bars: list[dict], since_date: str) -> list[dict]:
    """过滤出 since_date 之后的完整日K。"""
    fresh = [k for k in bars if k["date"] >= since_date]

    # 盘中安全：15:00 前排除今日不完整 bar；15:00 后视为完整日线边界。
    today_str = date.today().strftime("%Y-%m-%d")
    if not _is_market_closed_now() and _is_weekday(date.today()):
        fresh = [k for k in fresh if k["date"] != today_str]

    return fresh


def _fetch_recent_bars_with_source(code: str, since_date: str, max_bars: int = 20) -> tuple[list[dict], str] | None:
    """
    补缺最近 K 线：优先 mootdx TCP，失败降级到东方财富 HTTP。

    Args:
        code: 股票代码
        since_date: 只保留此日期及之后的 bar
        max_bars: 最多拉取根数

    Returns:
        (list[dict], source) 或 None
    """
    source = "tdx_tcp"
    bars = _fetch_via_tcp(code, max_bars)
    if bars is None:
        source = "eastmoney_http"
        bars = _fetch_via_http(code, max_bars)
    if bars is None:
        return None

    fresh = _filter_complete_bars(bars, since_date)
    return fresh, source


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
    result = _fetch_recent_bars_with_source(code, since_date, max_bars)
    if result is None:
        return None
    return result[0]


def _fetch_via_tcp(code: str, count: int) -> list[dict] | None:
    """通过 tdx_pool 持久连接拉取 K 线，失败返回 None"""
    try:
        pool = get_pool()
        raw = pool.get_security_bars(code, start=0, count=count)
        if not raw:
            return None
        return normalize_tcp_bars(raw)
    except Exception:
        return None


def _fetch_via_http(code: str, count: int) -> list[dict] | None:
    """降级：通过东方财富 HTTP 拉取 K 线"""
    try:
        _, api_klines = fetch_kline(code, count=count, period="day")
        return api_klines
    except Exception:
        return None


def _supplement_today_from_tcp(klines: list[dict], code: str):
    """盘中：用 TDX TCP 实时行情刷新今日 bar 的价格字段"""
    try:
        from 选股.tdx_pool import get_pool
        pool = get_pool()
        quotes = pool.get_quotes_batch([code])
        if quotes and len(quotes) > 0:
            q = quotes[0]
            today_bar = klines[-1]
            prev_close = klines[-2]["close"] if len(klines) >= 2 else today_bar.get("close", 0)

            tcp_price = float(q.get("price", today_bar["close"]))
            today_bar["close"] = tcp_price
            today_bar["high"] = max(float(today_bar["high"]), float(q.get("high", today_bar["high"])))
            today_bar["low"] = min(float(today_bar["low"]), float(q.get("low", today_bar["low"])))
            today_bar["volume"] = max(float(today_bar["volume"]), float(q.get("volume", today_bar["volume"])))

            if prev_close > 0:
                today_bar["pct_chg"] = round((tcp_price - prev_close) / prev_close * 100, 2)
                today_bar["change"] = round(tcp_price - prev_close, 2)
                today_bar["amplitude"] = round(
                    (today_bar["high"] - today_bar["low"]) / prev_close * 100, 2
                )
    except Exception:
        pass


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

def _is_market_closed_now() -> bool:
    """A 股是否已收盘（15:00 之后视为已收盘）"""
    from datetime import datetime as _dt
    now = _dt.now()
    return (now.hour > 15) or (now.hour == 15 and now.minute >= 0)


def _strip_today_incomplete(klines: list[dict]) -> list[dict]:
    """若今日未收盘，移除今日的不完整日K bar"""
    today_str = date.today().strftime("%Y-%m-%d")
    if not _is_market_closed_now() and _is_weekday(date.today()):
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

    # ── 检查当日缓存（盘中跳过，确保实时补充生效）──
    is_open = not _is_market_closed_now() and _is_weekday(date.today())
    cached = _load_cache(code, today_str)
    if cached is not None and not is_open:
        name, klines = cached
        last_k_date = klines[-1]["date"] if klines else ""
        if _is_market_closed_now() and last_k_date != today_str:
            cached = None
        else:
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
            is_open = not _is_market_closed_now() and _is_weekday(date.today())
            has_today = klines[-1]["date"] == today_str

            if is_open:
                # ── 盘中模式：保留今日 bar + TCP 实时补充 ──
                if has_today:
                    _supplement_today_from_tcp(klines, code)
                else:
                    today_bars = _fetch_via_tcp(code, 5)
                    if today_bars:
                        tb = [b for b in today_bars if b["date"] == today_str]
                        if tb:
                            klines.append(tb[-1])
                            if len(klines) > count:
                                klines = klines[-count:]
                result = (name, klines)
            else:
                # ── 盘后模式 ──
                klines = _strip_today_incomplete(klines)
                if not klines:
                    return (name, [])

                last_date_str = klines[-1]["date"]
                last_date = date.fromisoformat(last_date_str)
                gap = _count_trading_gaps(last_date, date.today())

                need_today_after_close = (
                    _is_weekday(date.today())
                    and _is_market_closed_now()
                    and last_date_str < today_str
                )

                if gap <= stale_days and not need_today_after_close:
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

    # ── 写入缓存（仅盘后写入，盘中不污染）──
    if result is not None:
        k = result[1]
        if k and not is_open:
            last_str = k[-1]["date"]
            g = _count_trading_gaps(date.fromisoformat(last_str), date.today())
            need_today_after_close = (
                _is_weekday(date.today())
                and _is_market_closed_now()
                and last_str < today_str
            )
            if g <= stale_days and not need_today_after_close:
                _save_cache(code, result[0], k)

    return result


def get_daily_data_status(sample_code: str = "000001") -> dict:
    """返回当前日线数据状态，供看板展示。"""
    today_str = date.today().strftime("%Y-%m-%d")
    is_trading_day = _is_weekday(date.today())
    market_closed = _is_market_closed_now()

    local_latest = None
    try:
        local = _read_tdx_daily(sample_code, 5)
        if local and local[1]:
            local_latest = local[1][-1]["date"]
    except Exception:
        local_latest = None

    latest_date = local_latest
    source = "tdx_local" if local_latest else "none"
    fallback_used = False
    fallback_source = None

    need_today_after_close = (
        is_trading_day
        and market_closed
        and local_latest != today_str
    )

    if need_today_after_close:
        result = _fetch_recent_bars_with_source(sample_code, local_latest or "", max_bars=10)
        if result is not None:
            fresh, src = result
            if fresh:
                fetched_latest = max(k["date"] for k in fresh)
                if latest_date is None or fetched_latest > latest_date:
                    latest_date = fetched_latest
                    source = src
                    fallback_used = True
                    fallback_source = src

    expected_date = today_str if is_trading_day and market_closed else latest_date
    complete = bool(latest_date)
    if is_trading_day and market_closed:
        complete = latest_date == today_str

    gap_days = None
    if latest_date:
        try:
            gap_days = _count_trading_gaps(date.fromisoformat(latest_date), date.today())
        except Exception:
            gap_days = None

    return {
        "sample_code": sample_code,
        "latest_date": latest_date,
        "local_latest_date": local_latest,
        "expected_date": expected_date,
        "complete": complete,
        "is_trading_day": is_trading_day,
        "market_closed": market_closed,
        "fallback_used": fallback_used,
        "fallback_source": fallback_source,
        "source": source,
        "gap_days": gap_days,
        "today": today_str,
    }


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
