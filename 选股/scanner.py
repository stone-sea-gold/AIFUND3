"""
扫描引擎 — 批量拉取K线 + 计算指标 + 筛选打分 + 排序输出
"""

import json
import os
import sys
import time
import traceback
import socket as _sock
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 强制 IPv4：东方财富 CDN 的 IPv6 节点不回 HTTP 响应
_orig_getaddrinfo = _sock.getaddrinfo
def _getaddrinfo_v4(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, _sock.AF_INET, type, proto, flags)
_sock.getaddrinfo = _getaddrinfo_v4

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "脚本"
sys.path.insert(0, str(SCRIPTS_DIR))

from fetch_wave_data import fetch_kline

from 选股.config import (
    STRATEGY,
    SCAN_COUNT, SCAN_PERIOD, REQUEST_DELAY,
    EXCLUDE_DEATH_CROSS, EXCLUDE_BELOW_YELLOW,
    MIN_LISTING_DAYS, MIN_VOLUME_RATIO,
    MIN_SCORE, TOP_N, SAVE_CACHE,
    USE_TDX_DATA,
)
from 选股.strategy_loader import load_strategy
from 选股.pool import get_stock_pool, filter_stocks

# ── 加载策略 ──
_strategy = load_strategy(STRATEGY)


def scan_one(code: str, name: str, strategy=None) -> dict | None:
    """
    对单只股票执行完整分析 → 过滤 → 打分

    Args:
        code: 股票代码
        name: 股票名称
        strategy: 策略模块（可选），不传则使用模块默认策略

    返回:
        {"code", "name", "score", "details", "latest_info", "klines", "indicators"}
        或 None（被排除/异常）
    """
    s = strategy if strategy is not None else _strategy

    # ── 1. 抓取K线 ──
    raw = _fetch_with_retry(code)
    if raw is None:
        return None
    stock_name, klines = raw
    # 通达信数据源不含名称（返回 code），回退到调用方传入的 name
    if stock_name == code:
        stock_name = name

    # ── 2. 基础过滤（策略无关）──
    if len(klines) < MIN_LISTING_DAYS:
        return None
    if not _check_volume_valid(klines):
        return None

    closes = [b["close"] for b in klines]

    # ── 3. 策略指标计算 ──
    try:
        ind = s.build_indicators(klines, closes)
    except Exception:
        return None

    # ── 4. 策略排除过滤 ──
    for exc_key, exc_cfg in s.EXCLUSION_FILTERS.items():
        if not exc_cfg.get("enabled", True):
            continue
        # 检查 config 级开关
        if exc_key == "death_cross" and not EXCLUDE_DEATH_CROSS:
            continue
        if exc_key == "below_yellow" and not EXCLUDE_BELOW_YELLOW:
            continue
        try:
            if exc_cfg["func"](ind, klines):
                return None
        except Exception:
            return None

    # ── 5. 逐条件打分 ──
    total_score = 0
    details = []
    for crit_key, crit_cfg in s.CRITERIA.items():
        weight = crit_cfg.get("weight", 0)
        params = crit_cfg.get("params", {})
        func = crit_cfg.get("func")
        if func is None:
            continue
        try:
            score, detail = _call_criterion(func, ind, klines, weight, params)
        except Exception:
            score, detail = 0, {"reason": "计算异常"}
        if score > 0:
            total_score += score
            details.append({
                "criterion": crit_key,
                "desc": crit_cfg.get("desc", crit_key),
                "score": score,
                "weight": weight,
                "detail": detail,
            })

    if total_score < MIN_SCORE:
        return None

    # ── 6. 构建结果 ──
    live_k = klines[-1]

    # 基础信息（策略无关）
    latest_info = {
        "date": live_k["date"],
        "close": live_k["close"],
        "pct_chg": live_k["pct_chg"],
        "volume": int(live_k["volume"]),
    }

    # 策略指定的附加字段
    for spec in s.LATEST_INFO_EXTRA:
        latest_info[spec["key"]] = _extract_from_ind(ind, spec)

    # 策略指定的结果指标
    result_indicators = {}
    for spec in s.RESULT_INDICATORS:
        result_indicators[spec["key"]] = _extract_from_ind(ind, spec)

    # 板块信息（从缓存中获取，缓存未加载则跳过）
    industry = ""
    concepts = []
    try:
        from 选股 import block_source
        if block_source._sector_map_cache and code in block_source._sector_map_cache:
            sector = block_source._sector_map_cache[code]
            industry = sector.get("industry", "")
            concepts = sector.get("concepts", [])
    except Exception:
        pass

    return {
        "code": code,
        "name": stock_name,
        "score": total_score,
        "details": details,
        "latest_info": latest_info,
        "klines": klines,
        "indicators": result_indicators,
        "industry": industry,
        "concepts": concepts,
    }


def _fetch_with_retry(code: str, max_retries: int = 1) -> tuple[str, list] | None:
    """带重试的数据抓取，优先使用通达信本地数据源"""
    # 优先：通达信本地 .day 文件 + API 增量补充
    if USE_TDX_DATA:
        try:
            from 选股.kline_source import get_klines
            return get_klines(code, count=SCAN_COUNT + 200, period=SCAN_PERIOD)
        except Exception:
            pass  # 降级到全量 API

    # 降级：东方财富 API 全量拉取
    for attempt in range(max_retries):
        try:
            return fetch_kline(code, SCAN_COUNT + 200, SCAN_PERIOD)
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(0.5)
    return None


def _check_volume_valid(klines: list[dict]) -> bool:
    """成交量有效性检查（排除停牌/无交易）"""
    recent = klines[-20:]
    vols = [b["volume"] for b in recent]
    if not vols:
        return False
    avg_vol = sum(vols) / len(vols)
    min_vol = min(vols)
    if avg_vol == 0 or min_vol / avg_vol < MIN_VOLUME_RATIO:
        return False
    return True


_criterion_klines_cache: dict[int, bool] = {}


def _call_criterion(func, ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """调用条件函数，根据函数签名自动传参（结果已缓存，避免重复 inspect）"""
    func_id = id(func)
    has_klines = _criterion_klines_cache.get(func_id)
    if has_klines is None:
        import inspect
        has_klines = "klines" in inspect.signature(func).parameters
        _criterion_klines_cache[func_id] = has_klines
    kwargs = {"ind": ind, "weight": weight, "params": params}
    if has_klines:
        kwargs["klines"] = klines
    return func(**kwargs)


def _extract_from_ind(ind: dict, spec: dict):
    """按 source 路径从指标字典中提取值，路径如 'macd.dif' 表示 ind['macd']['dif'][-1]"""
    source = spec.get("source", spec["key"])
    parts = source.split(".")
    val = ind
    for part in parts:
        if isinstance(val, dict):
            val = val.get(part)
        else:
            return None
    # 若提取到的是列表，取最后一个元素
    if isinstance(val, list) and len(val) > 0:
        val = val[-1]
    return val


def _scan_sequential(
    stocks: list, top_n: int, min_score: int, delay: float,
    verbose: bool, strategy, progress_callback, total: int,
) -> list[dict]:
    """单线程顺序扫描（HTTP 反爬保护 + sleep 间隔）"""
    results = []
    scanned = 0
    passed = 0
    t0 = time.time()

    for code, name in stocks:
        scanned += 1
        current_label = f"{code} {name}"
        try:
            r = scan_one(code, name, strategy=strategy)
        except Exception:
            if progress_callback:
                progress_callback(scanned, total, passed, current_label)
            continue

        if r is not None:
            results.append(r)
            passed += 1

        if progress_callback:
            progress_callback(scanned, total, passed, current_label)

        if verbose and scanned % 50 == 0:
            elapsed = time.time() - t0
            rate = scanned / elapsed if elapsed > 0 else 0
            eta = (total - scanned) / rate if rate > 0 else 0
            print(f"  [{scanned}/{total}] 扫描中... 合格:{passed} | {rate:.1f}只/秒 | ETA:{eta:.0f}s")

        time.sleep(delay)

    elapsed = time.time() - t0
    if verbose:
        print(f"\n扫描完成: {scanned} 只 | 合格: {passed} 只 | 耗时: {elapsed:.0f}s (顺序)")

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_n]


def _scan_parallel(
    stocks: list, top_n: int, min_score: int, workers: int,
    verbose: bool, strategy, progress_callback, total: int,
) -> list[dict]:
    """并行扫描（TDX TCP 持久连接 + 本地 .day 文件，无 sleep 限制）"""
    results = []
    scanned = [0]
    passed = [0]
    t0 = time.time()
    lock = threading.Lock()

    def _scan_item(code: str, name: str):
        try:
            r = scan_one(code, name, strategy=strategy)
        except Exception:
            r = None

        with lock:
            scanned[0] += 1
            if r is not None and r.get("score", 0) >= min_score:
                results.append(r)
                passed[0] += 1
            if progress_callback:
                progress_callback(scanned[0], total, passed[0], f"{code} {name}")

        return r

    # 并行扫描：ScanCancelled 从 progress_callback 抛出时需立即停止
    try:
        from server.scan_manager import ScanCancelled
    except ImportError:
        ScanCancelled = Exception  # fallback

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_scan_item, c, n): (c, n) for c, n in stocks}
        for future in as_completed(futures):
            try:
                future.result()
            except ScanCancelled:
                executor.shutdown(wait=False, cancel_futures=True)
                raise
            except Exception:
                pass

    elapsed = time.time() - t0
    if verbose:
        print(f"\n扫描完成: {scanned[0]} 只 | 合格: {passed[0]} 只 | 耗时: {elapsed:.0f}s (并行 x{workers})")

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_n]


def _tdx_pool_available() -> bool:
    """检测 TDX TCP 持久连接是否可用（主动连接验证）"""
    try:
        from 选股.tdx_pool import get_pool
        pool = get_pool()
        # 尝试拉取 1 根 bar 来验证连接
        bars = pool.get_security_bars("000001", start=0, count=1)
        return bars is not None and len(bars) > 0
    except Exception:
        return False


def scan_all(
    pool_name: str = "沪深300",
    top_n: int = TOP_N,
    min_score: int | None = None,
    workers: int = 4,
    delay: float | None = None,
    verbose: bool = True,
    strategy=None,
    progress_callback=None,
) -> list[dict]:
    """全量扫描入口。

    Args:
        pool_name: 股票池名称
        top_n: 返回前 N 只
        min_score: 最低入围分，None 则用 config 默认值
        workers: 并发线程数（TDX TCP 可用时生效）
        delay: 请求间隔秒数，None 则用 config 默认值
        verbose: 是否打印进度
        strategy: 策略模块（可选）
        progress_callback: 进度回调(scanned, total, passed, current_stock_str)
    """
    if min_score is None:
        min_score = MIN_SCORE
    if delay is None:
        delay = REQUEST_DELAY

    # 获取股票池
    if verbose:
        print(f"获取股票池: {pool_name} ...")
    stocks = get_stock_pool(pool_name)
    stocks = filter_stocks(stocks)

    total = len(stocks)
    if verbose:
        print(f"有效标的: {total} 只")

    # 选择扫描路径：TDX TCP 可用 → 并行，否则 → 顺序（兼容 HTTP 反爬）
    use_parallel = USE_TDX_DATA and workers > 1 and _tdx_pool_available()

    if use_parallel:
        if verbose:
            print(f"数据源: TDX TCP 并行 (workers={workers})")
        return _scan_parallel(stocks, top_n, min_score, workers, verbose, strategy,
                             progress_callback, total)
    else:
        if verbose:
            print(f"数据源: 顺序拉取 (delay={delay}s)")
        return _scan_sequential(stocks, top_n, min_score, delay, verbose, strategy,
                               progress_callback, total)


if __name__ == "__main__":
    pool = sys.argv[1] if len(sys.argv) > 1 else "自选"
    results = scan_all(pool_name=pool, top_n=20)
    print(f"\nTop {len(results)}:")
    for i, r in enumerate(results, 1):
        print(f"  {i:2d}. {r['name']}({r['code']}) 得分:{r['score']}")