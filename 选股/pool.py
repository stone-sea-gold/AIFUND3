"""
股票池管理 — 获取全A股 / 指数成分股 / 自定义选股池
数据来源：新浪财经 API（沪深主板）、东方财富 API（指数成分股）
"""

import json
import os
import sys
import time
import socket as _sock
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
CACHE_FILE = PROJECT_ROOT / "选股" / "stock_pool.json"
STATIC_DIR = PROJECT_ROOT / "选股" / "static"
STATIC_MAIN_BOARD = STATIC_DIR / "stock_list_main_board.json"
WATCHLIST_FILE = PROJECT_ROOT / "观察仓" / "watchlist.txt"

# 沪深主板自动刷新间隔（秒）
MAIN_BOARD_REFRESH_SEC = 7 * 86400  # 每 7 天

# ── 东方财富成分股数据接口（沪深300 / 中证500 / 全A）─────────

HEADERS_EM = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.eastmoney.com/",
}

INDEX_CONFIG = {
    "沪深300": {"fs": "m:0+t:6,m:1+t:2"},
    "中证500": {"fs": "b:BK0701"},
    "全A":     {"fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"},
}


def _fetch_paginated(fs: str, page_size: int = 500) -> list[dict]:
    """分页拉取东方财富成分股列表（HTTP优先）"""
    all_items = []
    page = 1
    max_pages = 30
    while page <= max_pages:
        params = {
            "pn": page, "pz": page_size, "fs": fs,
            "fields": "f12,f14", "np": 1, "fltt": 2, "invt": 2,
        }
        raw = None
        for scheme in ("http", "https"):
            try:
                u = f"{scheme}://push2.eastmoney.com/api/qt/clist/get"
                resp = requests.get(u, params=params, headers=HEADERS_EM, timeout=30, verify=False)
                if resp.status_code == 200 and len(resp.text) > 50:
                    raw = resp.json()
                    break
            except Exception:
                continue
        if raw is None:
            break
        items = raw.get("data", {}).get("diff", [])
        if not items:
            break
        all_items.extend(items)
        total = raw.get("data", {}).get("total", 0)
        if page * page_size >= total:
            break
        page += 1
    return all_items


def _api_fetch_stock_list_em(pool_name: str) -> list[tuple[str, str]] | None:
    """从东方财富 API 获取股票列表，失败返回 None"""
    config = INDEX_CONFIG.get(pool_name)
    if not config:
        return None
    items = _fetch_paginated(config["fs"])
    if not items:
        return None
    # 过滤：仅保留A股个股，排除ETF/LOF/港股
    result = []
    for item in items:
        code = item.get("f12", "")
        name = item.get("f14", "")
        if not code or not name:
            continue
        if len(code) != 6:
            continue  # 排除港股等非6位代码
        if code.startswith(("159", "16", "51", "513", "56", "58")):
            continue  # 排除ETF/LOF基金
        result.append((code, name))
    return result if result else None


# ── 新浪财经 API（沪深主板精确列表）───────────────────────────

SINA_API = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"

HEADERS_SINA = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.sina.com.cn/",
}


def _fetch_sina_page(node: str, page: int, num: int = 80) -> list[dict]:
    """拉取新浪行情中心单页"""
    params = {
        "page": page, "num": num, "sort": "symbol", "asc": 1,
        "node": node, "symbol": "", "_s_r_a": "init",
    }
    try:
        resp = requests.get(SINA_API, params=params, headers=HEADERS_SINA, timeout=30, verify=False)
        if resp.status_code != 200:
            return []
        return resp.json()
    except Exception:
        return []


def _fetch_sina_all_a() -> list[tuple[str, str]] | None:
    """从新浪 API 拉取全A股列表，失败返回 None"""
    all_stocks = []
    for node in ("sh_a", "sz_a"):
        page = 1
        node_stocks = 0
        while True:
            items = _fetch_sina_page(node, page, 80)
            if not items:
                break
            for item in items:
                code = item.get("code", "")
                name = item.get("name", "")
                if code and name:
                    all_stocks.append((code, name))
            node_stocks += len(items)
            if len(items) < 80:
                break
            page += 1
        if node_stocks == 0:
            return None  # 任一节点拉取失败则整体失败
    return all_stocks if all_stocks else None


def _refresh_main_board_from_sina() -> list[tuple[str, str]]:
    """从新浪 API 刷新沪深主板名单 → 写入静态文件 → 返回列表"""
    all_stocks = _fetch_sina_all_a()
    if not all_stocks:
        raise ConnectionError("新浪财经 API 不可达，无法刷新沪深主板名单")

    main_board = []
    for code, name in all_stocks:
        if code.startswith(("300", "301", "688", "8", "4")):
            continue
        if "ST" in name or "*ST" in name or name.startswith("N"):
            continue
        main_board.append([code, name])

    main_board.sort()
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_MAIN_BOARD.write_text(
        json.dumps({
            "ts": time.time(),
            "count": len(main_board),
            "source": "新浪财经API-自动刷新",
            "note": "仅沪深主板（已剔除创业板/科创板/北交所/ST）",
            "stocks": main_board,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return [(c, n) for c, n in main_board]


# ── 静态文件读取 ──────────────────────────────────────────────

def _load_static_main_board() -> list[tuple[str, str]] | None:
    """从静态文件加载沪深主板列表，文件不存在返回 None"""
    if not STATIC_MAIN_BOARD.exists():
        return None
    try:
        data = json.loads(STATIC_MAIN_BOARD.read_text(encoding="utf-8"))
        stocks = [(x[0], x[1]) for x in data.get("stocks", [])]
        return stocks if stocks else None
    except (json.JSONDecodeError, KeyError, FileNotFoundError):
        return None


def _static_file_age_sec() -> float | None:
    """返回静态文件的存在时长（秒），不存在返回 None"""
    if not STATIC_MAIN_BOARD.exists():
        return None
    try:
        data = json.loads(STATIC_MAIN_BOARD.read_text(encoding="utf-8"))
        ts = data.get("ts", 0)
        return time.time() - ts
    except (json.JSONDecodeError, KeyError):
        return None


# ── 紧急降级：代码区间估算 ────────────────────────────────────

def _generate_static_main_board() -> list[tuple[str, str]]:
    """
    生成本地沪深主板股票列表（基于代码区间估算）。
    仅在 API 完全不可达且无缓存文件时作为紧急降级。
    """
    sh_ranges = [
        range(600000, 601999), range(603000, 603999), range(605000, 605599),
    ]
    sz_ranges = [
        range(1, 1000), range(1200, 1400), range(2000, 3000), range(3000, 3100),
    ]
    codes = set()
    for r in sh_ranges:
        for n in r:
            codes.add(f"{n:06d}")
    for r in sz_ranges:
        for n in r:
            codes.add(f"{n:06d}")
    codes = {c for c in codes if not c.startswith(("300", "301", "688", "8"))}
    stocks = sorted([[c, c] for c in codes])
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_MAIN_BOARD.write_text(
        json.dumps({"ts": time.time(), "count": len(stocks), "stocks": stocks,
                    "source": "区间估算-紧急降级"}, ensure_ascii=False),
        encoding="utf-8",
    )
    return [(c, c) for c, _ in stocks]


# ── 沪深主板池（自动刷新）─────────────────────────────────────

def _build_main_board_pool() -> list[tuple[str, str]]:
    """
    沪深主板股票池，自动管理过期刷新：
      1. 静态文件存在且 < 7 天 → 直接加载
      2. 静态文件过期或不存在 → 自动从新浪 API 刷新
      3. 新浪 API 失败 → 使用过期静态文件（仍比无数据强）
      4. 完全无文件 → 紧急代码区间估算
    """
    age = _static_file_age_sec()

    # 情况1：文件存在且未过期
    if age is not None and age < MAIN_BOARD_REFRESH_SEC:
        return _load_static_main_board()

    # 情况2：过期或不存在 → 尝试自动刷新
    if age is not None:
        pass  # 过期，将尝试刷新

    try:
        return _refresh_main_board_from_sina()
    except Exception:
        pass

    # 情况3：刷新失败 → 使用过期静态文件兜底
    stale = _load_static_main_board()
    if stale:
        return stale

    # 情况4：无任何缓存 → 紧急降级
    return _generate_static_main_board()


# ── 通用入口 ─────────────────────────────────────────────────

def _build_all_a_pool() -> list[tuple[str, str]]:
    """全A股票池：从新浪 API 拉取全部 A 股"""
    all_stocks = _fetch_sina_all_a()
    if all_stocks:
        # 去ST
        result = []
        for code, name in all_stocks:
            if "ST" in name or "*ST" in name or name.startswith("N"):
                continue
            result.append((code, name))
        return result
    raise ConnectionError("无法获取全A股票池：新浪 API 不可达")


def fetch_stock_list(pool_name: str = "沪深300") -> list[tuple[str, str]]:
    """获取股票池列表，返回: [(代码, 名称), ...]"""
    pool_name = pool_name.strip()

    # 沪深主板：新浪主源 + 7天自动刷新
    if pool_name == "沪深主板":
        return _build_main_board_pool()

    # 全A：新浪主源
    if pool_name == "全A":
        return _build_all_a_pool()

    # 沪深300 / 中证500：东财为主（需IPv4），失败则用沪深主板兜底
    result = _api_fetch_stock_list_em(pool_name)
    if result:
        return result

    # 兜底：沪深主板包含了沪深300和中证500的绝大多数标的
    fallback = _build_main_board_pool()
    print(f"  [注意] 东方财富 {pool_name} API 不可达，已用沪深主板 ({len(fallback)} 只) 替代")
    return fallback


def get_custom_pool() -> list[tuple[str, str]]:
    """从 观察仓/watchlist.txt 读取自定义选股列表"""
    if not WATCHLIST_FILE.exists():
        return []
    stocks = []
    for line in WATCHLIST_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        code = parts[0].strip()
        name = parts[1].strip() if len(parts) > 1 else code
        stocks.append((code, name))
    return stocks


def load_cache(pool_name: str) -> list[tuple[str, str]] | None:
    """加载缓存股票池（按池名索引，24h 过期）"""
    if not CACHE_FILE.exists():
        return None
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        entry = data.get("pools", {}).get(pool_name)
        if entry and time.time() - entry.get("ts", 0) < 86400:
            return [(x[0], x[1]) for x in entry.get("stocks", [])]
    except (json.JSONDecodeError, KeyError):
        pass
    return None


def save_cache(pool_name: str, stocks: list[tuple[str, str]]):
    """缓存股票池（按池名索引）"""
    existing = {}
    try:
        if CACHE_FILE.exists():
            existing = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        pass
    pools = existing.get("pools", {})
    pools[pool_name] = {
        "ts": time.time(),
        "stocks": [[c, n] for c, n in stocks],
    }
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps({"pools": pools}, ensure_ascii=False),
        encoding="utf-8",
    )


def get_stock_pool(pool_name: str = "沪深300", use_cache: bool = True) -> list[tuple[str, str]]:
    """统一入口：获取股票池"""
    if pool_name == "自选":
        return get_custom_pool()

    if use_cache:
        cached = load_cache(pool_name)
        if cached:
            return cached

    stocks = fetch_stock_list(pool_name)
    if use_cache and stocks:
        save_cache(pool_name, stocks)
    return stocks


def filter_stocks(
    stocks: list[tuple[str, str]],
    exclude_st: bool = True,
    main_board_only: bool = False,
) -> list[tuple[str, str]]:
    """基础过滤：去ST、去退市，可选仅限主板"""
    result = []
    for code, name in stocks:
        if exclude_st and ("ST" in name or "*ST" in name or name.startswith("N")):
            continue
        if main_board_only:
            if code.startswith(("300", "301", "688", "8")):
                continue
        result.append((code, name))
    return result


if __name__ == "__main__":
    pool = sys.argv[1] if len(sys.argv) > 1 else "沪深主板"
    stocks = get_stock_pool(pool, use_cache=False)
    filtered = filter_stocks(stocks)
    print(f"股票池: {pool} | 原始: {len(stocks)} 只 | 过滤后: {len(filtered)} 只")
    for code, name in filtered[:10]:
        print(f"  {code} {name}")
    if len(filtered) > 10:
        print(f"  ... 共 {len(filtered)} 只")
