"""
板块/行业数据统一获取层

获取个股的行业分类和概念板块信息。
数据源优先级：缓存 → 通达信 TCP（板块文件 + finance 接口）

用法:
    from 选股.block_source import get_sector_map
    sector_map = get_sector_map()
    # => {"000001": {"industry": "银行", "concepts": ["融资融券"]}, ...}
"""

import json
import sys
import threading
import time
from datetime import date
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# ── 缓存配置 ──
_CACHE_DIR = _PROJECT_ROOT / "选股" / "kline_cache"
_CACHE_FILE = _CACHE_DIR / "sector_map.json"


# ═══════════════════════════════════════════════════════════════
# 交易时间判断（复用 kline_source 的逻辑）
# ═══════════════════════════════════════════════════════════════

def _is_weekday(d) -> bool:
    return d.weekday() < 5


def _is_market_closed_now() -> bool:
    from datetime import datetime
    now = datetime.now()
    return now.hour > 15 or (now.hour == 15 and now.minute >= 0)


# ═══════════════════════════════════════════════════════════════
# 概念板块：TCP 下载 block_gn.dat
# ═══════════════════════════════════════════════════════════════

def _fetch_concept_blocks_tcp() -> dict[str, list[str]]:
    """
    通过 TDX TCP 下载 block_gn.dat，构建 code → [概念名] 反向映射。
    仅取 block_type=2 的有效条目。

    Returns:
        {"000001": ["融资融券", "高股息股"], ...}
    """
    try:
        from 选股.tdx_pool import get_pool
        pool = get_pool()
        pool._ensure_connected()
        api = pool._api
        raw = api.get_and_parse_block_info('block_gn.dat')
        if not raw:
            return {}
    except Exception:
        return {}

    # 构建反向映射
    code_to_concepts: dict[str, set[str]] = {}
    for item in raw:
        if item.get('block_type') != 2:
            continue
        name = item.get('blockname', '')
        code = item.get('code', '')
        if not name or not code:
            continue
        if code not in code_to_concepts:
            code_to_concepts[code] = set()
        code_to_concepts[code].add(name)

    return {code: sorted(concepts) for code, concepts in code_to_concepts.items()}


def _fetch_industry_blocks_tcp() -> dict[str, list[str]]:
    """
    通过 TDX TCP 下载 block_fg.dat，构建 code → [行业标签] 反向映射。
    仅取 block_type=2 的有效条目。

    Returns:
        {"000001": ["融资融券", "破净资产"], ...}
    """
    try:
        from 选股.tdx_pool import get_pool
        pool = get_pool()
        pool._ensure_connected()
        api = pool._api
        raw = api.get_and_parse_block_info('block_fg.dat')
        if not raw:
            return {}
    except Exception:
        return {}

    code_to_tags: dict[str, set[str]] = {}
    for item in raw:
        if item.get('block_type') != 2:
            continue
        name = item.get('blockname', '')
        code = item.get('code', '')
        if not name or not code:
            continue
        if code not in code_to_tags:
            code_to_tags[code] = set()
        code_to_tags[code].add(name)

    return {code: sorted(tags) for code, tags in code_to_tags.items()}


# ═══════════════════════════════════════════════════════════════
# 行业分类：finance API + F10 名称映射
# ═══════════════════════════════════════════════════════════════

def _get_finance_info_batch(stocks: list[tuple[str, str]]) -> dict[str, int]:
    """
    批量获取股票的行业代码（通过 TDX finance 接口）。

    Args:
        stocks: [(code, name), ...]

    Returns:
        {code: industry_code, ...}
    """
    try:
        from 选股.tdx_pool import get_pool
        pool = get_pool()
        pool._ensure_connected()
        api = pool._api
    except Exception:
        return {}

    result = {}
    for code, _name in stocks:
        if len(code) != 6:
            continue  # 跳过非A股代码（如港股5位代码）
        market = 1 if code.startswith('6') else 0
        try:
            info = api.get_finance_info(market, code)
            if info:
                ind = info.get('industry')
                if ind is not None and int(ind) != 0:
                    result[code] = int(ind)
        except Exception:
            continue
    return result


def _resolve_industry_names(codes: list[int]) -> dict[int, str]:
    """
    通过 F10 公司概况查询行业代码对应的行业名称。

    Args:
        codes: 行业代码列表，如 [1, 37, 43]

    Returns:
        {1: "银行-全国性银行", 37: "食品饮料-酿酒", ...}
    """
    try:
        from 选股.tdx_pool import get_pool
        pool = get_pool()
        pool._ensure_connected()
        api = pool._api
    except Exception:
        return {}

    # 用全A股的样本股来查找行业名称（沪深300可能返回港股代码）
    # 只取前300只样本即可覆盖大部分行业代码，避免遍历全部4958只
    from 选股.pool import get_stock_pool
    all_stocks = get_stock_pool("全A")
    sample_stocks = all_stocks[:300]

    # 先获取样本股的行业代码
    code_to_ind = _get_finance_info_batch(sample_stocks)

    # 为每个行业代码找一只样本股
    ind_to_sample: dict[int, tuple[int, str]] = {}
    for code, ind in code_to_ind.items():
        if ind in codes and ind not in ind_to_sample:
            market = 1 if code.startswith('6') else 0
            ind_to_sample[ind] = (market, code)

    # 通过 F10 查询行业名称
    # 对每个行业代码，取该代码对应的第一只样本股，通过 F10 获取其实际的行业名称
    result = {}
    for ind_code, (market, stock_code) in ind_to_sample.items():
        try:
            cats = api.get_company_info_category(market, stock_code)
            for cat in cats:
                if cat['name'] == '公司概况':
                    content = api.get_company_info_content(
                        market, stock_code,
                        cat['filename'], cat['start'],
                        min(cat['length'], 5000)
                    )
                    if content:
                        text = content if isinstance(content, str) else content.decode('utf-8', errors='replace')
                        for line in text.split('\n'):
                            if '通达信研究行业' in line:
                                parts = line.split('│')
                                for i, p in enumerate(parts):
                                    if '通达信研究行业' in p and i + 1 < len(parts):
                                        name = parts[i + 1].strip()
                                        if name:
                                            result[ind_code] = name
                                        break
                                break
                    break
        except Exception:
            continue
        time.sleep(0.02)

    return result


# ═══════════════════════════════════════════════════════════════
# 缓存管理
# ═══════════════════════════════════════════════════════════════

def _load_cache() -> dict | None:
    """加载缓存（行业名称很少变动，缓存不过期）。"""
    if not _CACHE_FILE.exists():
        return None
    try:
        data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "data" in data:
            return data.get("data")
    except (json.JSONDecodeError, Exception):
        pass
    return None


def _save_cache(data: dict):
    """写入缓存。"""
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetch_date": date.today().strftime("%Y-%m-%d"),
            "data": data,
        }
        tmp = _CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_CACHE_FILE)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════

_sector_map_cache = None
_sector_map_lock = threading.Lock()
_loading_started = False


def _do_background_load():
    """后台线程：加载板块数据（TCP + finance + F10），完成后写入缓存。"""
    global _sector_map_cache
    try:
        # ── 概念板块（TCP 下载 block_gn.dat）──
        concept_map = _fetch_concept_blocks_tcp()

        # ── 行业分类（finance API + F10）──
        from 选股.pool import get_stock_pool
        all_stocks = get_stock_pool("全A")
        code_to_ind = _get_finance_info_batch(all_stocks)

        industry_map: dict[str, str] = {}
        if code_to_ind:
            unique_codes = list(set(code_to_ind.values()))
            ind_names = _resolve_industry_names(unique_codes)
            industry_map = {
                code: ind_names.get(ind, "")
                for code, ind in code_to_ind.items()
            }

        # ── 合并结果 ──
        all_codes = set(concept_map.keys()) | set(industry_map.keys())
        result = {}
        for code in all_codes:
            result[code] = {
                "industry": industry_map.get(code, ""),
                "concepts": concept_map.get(code, []),
            }

        _save_cache(result)
        with _sector_map_lock:
            _sector_map_cache = result
    except Exception:
        pass  # 后台加载失败不影响主流程


def _ensure_loading():
    """确保后台加载线程已启动。"""
    global _loading_started, _sector_map_cache
    if _loading_started:
        return
    with _sector_map_lock:
        if _loading_started:
            return
        _loading_started = True
        # 先尝试加载文件缓存（瞬间）
        if _sector_map_cache is None:
            cached = _load_cache()
            if cached is not None:
                _sector_map_cache = cached
        # 如果内存缓存仍为空，启动后台加载
        if _sector_map_cache is None:
            t = threading.Thread(target=_do_background_load, daemon=True)
            t.start()


def get_sector_map(stocks: list[tuple[str, str]] | None = None) -> dict[str, dict]:
    """
    获取板块/行业映射表。

    Args:
        stocks: [(code, name), ...] 可选。传入时用于精确查询行业代码。
                不传则仅返回概念板块映射。

    Returns:
        {"000001": {"industry": "银行", "concepts": ["融资融券", ...]}, ...}
    """
    global _sector_map_cache
    _ensure_loading()

    if _sector_map_cache is not None:
        return _sector_map_cache

    with _sector_map_lock:
        if _sector_map_cache is not None:
            return _sector_map_cache

        # 缓存未就绪，启动同步加载
        concept_map = _fetch_concept_blocks_tcp()
        from 选股.pool import get_stock_pool
        all_stocks = get_stock_pool("全A")
        code_to_ind = _get_finance_info_batch(all_stocks)
        if stocks:
            extra_ind = _get_finance_info_batch(stocks)
            code_to_ind.update(extra_ind)

        industry_map: dict[str, str] = {}
        if code_to_ind:
            unique_codes = list(set(code_to_ind.values()))
            ind_names = _resolve_industry_names(unique_codes)
            industry_map = {
                code: ind_names.get(ind, "")
                for code, ind in code_to_ind.items()
            }

        all_codes = set(concept_map.keys()) | set(industry_map.keys())
        result = {}
        for code in all_codes:
            result[code] = {
                "industry": industry_map.get(code, ""),
                "concepts": concept_map.get(code, []),
            }

        _save_cache(result)
        _sector_map_cache = result
        return result


def get_stock_sector(code: str, stocks: list[tuple[str, str]] | None = None) -> dict:
    """
    获取单只股票的板块信息。

    Returns:
        {"industry": "银行", "concepts": ["融资融券"]}
    """
    _ensure_loading()

    if _sector_map_cache is not None:
        return _sector_map_cache.get(code, {"industry": "", "concepts": []})

    # 缓存未就绪时，返回空（不阻塞）
    return {"industry": "", "concepts": []}


def invalidate_cache():
    """清除缓存（用于测试或手动刷新）。"""
    global _sector_map_cache
    _sector_map_cache = None
    try:
        _CACHE_FILE.unlink(missing_ok=True)
    except Exception:
        pass
