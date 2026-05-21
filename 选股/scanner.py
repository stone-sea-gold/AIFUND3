"""
扫描引擎 — 批量拉取K线 + 计算指标 + 筛选打分 + 排序输出
"""

import json
import os
import sys
import time
import traceback
import socket as _sock
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

    return {
        "code": code,
        "name": stock_name,
        "score": total_score,
        "details": details,
        "latest_info": latest_info,
        "klines": klines,
        "indicators": result_indicators,
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


def _call_criterion(func, ind: dict, klines: list[dict], weight: int, params: dict) -> tuple[int, dict]:
    """调用条件函数，根据函数签名自动传参"""
    import inspect
    sig = inspect.signature(func)
    kwargs = {"ind": ind, "weight": weight, "params": params}
    if "klines" in sig.parameters:
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


def scan_all(
    pool_name: str = "沪深300",
    top_n: int = TOP_N,
    min_score: int = MIN_SCORE,
    workers: int = 4,
    verbose: bool = True,
    strategy=None,
    progress_callback=None,
) -> list[dict]:
    """全量扫描入口

    Args:
        pool_name: 股票池名称
        top_n: 返回前 N 只
        min_score: 最低入围分
        workers: 并发线程数（保留参数，当前为单线程）
        verbose: 是否打印进度
        strategy: 策略模块（可选），不传则使用模块默认策略
        progress_callback: 进度回调函数(scanned, total, passed, current_stock_str)
    """
    # 获取股票池
    if verbose:
        print(f"获取股票池: {pool_name} ...")
    stocks = get_stock_pool(pool_name)
    stocks = filter_stocks(stocks)

    total = len(stocks)
    if verbose:
        print(f"有效标的: {total} 只")

    # 单线程顺序扫描（东方财富有反爬，并发容易触发封IP）
    results = []
    scanned = 0
    passed = 0
    t0 = time.time()

    for code, name in stocks:
        scanned += 1
        current_label = f"{code} {name}"
        try:
            r = scan_one(code, name, strategy=strategy)
        except Exception as e:
            if verbose and scanned % 100 == 0:
                print(f"  [{scanned}/{total}] {current_label} 异常: {e}")
            if progress_callback:
                progress_callback(scanned, total, passed, current_label)
            continue

        if r is not None:
            results.append(r)
            passed += 1

        # 进度回调
        if progress_callback:
            progress_callback(scanned, total, passed, current_label)

        if verbose and scanned % 50 == 0:
            elapsed = time.time() - t0
            rate = scanned / elapsed if elapsed > 0 else 0
            eta = (total - scanned) / rate if rate > 0 else 0
            print(f"  [{scanned}/{total}] 扫描中... 合格:{passed} | {rate:.1f}只/秒 | ETA:{eta:.0f}s")

        # 请求间隔
        time.sleep(REQUEST_DELAY)

    elapsed = time.time() - t0
    if verbose:
        print(f"\n扫描完成: {scanned} 只 | 合格: {passed} 只 | 耗时: {elapsed:.0f}s")

    # 排序取 top
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_n]


if __name__ == "__main__":
    pool = sys.argv[1] if len(sys.argv) > 1 else "自选"
    results = scan_all(pool_name=pool, top_n=20)
    print(f"\nTop {len(results)}:")
    for i, r in enumerate(results, 1):
        print(f"  {i:2d}. {r['name']}({r['code']}) 得分:{r['score']}")
