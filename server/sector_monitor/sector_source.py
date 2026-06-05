"""
板块数据获取层

数据源优先级:
  板块列表/成分股: TDX本地文件 → TDX TCP → 东方财富HTTP
  板块指数K线:    TDX本地文件 → TDX TCP → 东方财富HTTP
  涨跌家数/涨停:  东方财富HTTP（TDX无此数据）
"""

import json
import time
import os
import sys
import threading
from datetime import date, datetime
from pathlib import Path

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 强制 IPv4（复用 pool.py 的模式）
import socket as _sock
_orig_getaddrinfo = _sock.getaddrinfo
def _getaddrinfo_v4(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, _sock.AF_INET, type, proto, flags)
_sock.getaddrinfo = _getaddrinfo_v4

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_CACHE_FILE = _DATA_DIR / "sector_cache.json"
_CONSTITUENTS_FILE = _DATA_DIR / "sector_constituents.json"
_SECTOR_NAME_MAP_FILE = _DATA_DIR / "sector_name_map.json"

_sector_name_map: dict[str, str] | None = None


def _load_sector_name_map() -> dict[str, str]:
    """加载板块代码→名称映射表（EM不可用时的降级方案）"""
    global _sector_name_map
    if _sector_name_map is not None:
        return _sector_name_map
    try:
        with open(_SECTOR_NAME_MAP_FILE, "r", encoding="utf-8") as f:
            _sector_name_map = json.load(f)
    except Exception:
        _sector_name_map = {}
    return _sector_name_map


def _is_trading_hours() -> bool:
    """判断当前是否为A股交易时间（9:15-15:00，周一至周五）"""
    from datetime import datetime
    now = datetime.now()
    if now.weekday() >= 5:  # 周末
        return False
    t = now.hour * 100 + now.minute
    return 915 <= t <= 1500

# TDX本地数据目录
try:
    from 选股.config import TDX_DATA_DIR
except Exception:
    TDX_DATA_DIR = os.environ.get("TDX_DATA_DIR", "D:/BaiduNetdiskDownload")

HEADERS_EM = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.eastmoney.com/",
}

# 东方财富板块类型映射
_EM_SECTOR_TYPES = {
    "industry": {"fs": "m:90+t:2", "name": "行业板块"},
    "concept":  {"fs": "m:90+t:3", "name": "概念板块"},
}


# ═══════════════════════════════════════════════════════════════
# 通达信数据源（首选）
# ═══════════════════════════════════════════════════════════════

def _get_tdx_pool():
    """延迟获取 TDX 连接池"""
    from 选股.tdx_pool import get_pool
    return get_pool()


def _get_tdx_reader():
    """延迟获取 TDX 本地文件 Reader"""
    from mootdx.reader import Reader
    tdx_dir = None
    try:
        from 选股.config import TDX_DATA_DIR
        tdx_dir = TDX_DATA_DIR
    except Exception:
        pass
    if not tdx_dir:
        tdx_dir = os.environ.get("TDX_DATA_DIR", "D:/BaiduNetdiskDownload")
    return Reader.factory(market='std', tdxdir=tdx_dir)


def fetch_block_list_tdx(block_type: str = "industry") -> list[dict] | None:
    """
    从通达信获取板块列表（含板块指数代码）。

    Args:
        block_type: "industry" 或 "concept"

    Returns:
        list[dict]: [{"code": "880001", "name": "银行", "block_type": N}, ...]
        失败返回 None
    """
    block_file = "block_zs.dat" if block_type == "industry" else "block_gn.dat"

    # 方式1: 本地文件读取（最快）
    try:
        reader = _get_tdx_reader()
        df = reader.block(block_file, group=True)
        if df is not None and len(df) > 0:
            result = []
            for _, row in df.iterrows():
                result.append({
                    "name": row.get("blockname", ""),
                    "block_type": row.get("block_type", 0),
                    "code_index": row.get("code_index", ""),
                    "stock_count": row.get("stock_count", 0),
                })
            return [s for s in result if s["name"]]
    except Exception:
        pass

    # 方式2: TCP协议获取
    try:
        pool = _get_tdx_pool()
        df = pool.get_block_list(block_file)
        if df is not None and len(df) > 0:
            # TCP 返回 FLAT 格式，需要按板块名分组
            grouped = {}
            for _, row in df.iterrows():
                name = row.get("blockname", "")
                if not name:
                    continue
                if name not in grouped:
                    grouped[name] = {
                        "name": name,
                        "block_type": row.get("block_type", 0),
                        "code_index": row.get("code_index", ""),
                        "stock_count": 0,
                    }
                grouped[name]["stock_count"] += 1
            return list(grouped.values())
    except Exception:
        pass

    return None


def fetch_sector_klines_tdx(index_code: str, count: int = 30) -> list[dict] | None:
    """
    从通达信获取板块指数日K线。

    盘中: TCP优先（实时数据），本地降级
    盘后: 本地优先（收盘数据最快），TCP降级
    """
    if _is_trading_hours():
        # 盘中：TCP优先（实时数据）
        try:
            from 选股.tdx_pool import normalize_tcp_bars
            pool = _get_tdx_pool()
            raw = pool.get_sector_bars(index_code, count=count)
            if raw:
                return normalize_tcp_bars(raw)
        except Exception:
            pass
        return _read_tdx_daily_binary("sh", index_code, count)
    else:
        # 盘后：本地优先（收盘数据最快）
        bars = _read_tdx_daily_binary("sh", index_code, count)
        if bars:
            return bars
        try:
            from 选股.tdx_pool import normalize_tcp_bars
            pool = _get_tdx_pool()
            raw = pool.get_sector_bars(index_code, count=count)
            if raw:
                return normalize_tcp_bars(raw)
        except Exception:
            pass
        return None


# ═══════════════════════════════════════════════════════════════
# 东方财富数据源（降级）
# ═══════════════════════════════════════════════════════════════

def fetch_sector_list_em(sector_type: str = "industry") -> list[dict] | None:
    """
    从东方财富获取板块列表+行情（涨跌幅、成交额、涨跌家数等）。

    Args:
        sector_type: "industry" 或 "concept"

    Returns:
        list[dict]: 板块列表，每项含 code/name/pct_chg/amount/up_count/down_count 等
    """
    config = _EM_SECTOR_TYPES.get(sector_type)
    if not config:
        return None

    all_items = []
    page = 1
    page_size = 500
    while page <= 10:
        params = {
            "pn": page, "pz": page_size,
            "fs": config["fs"],
            "fields": "f2,f3,f6,f8,f12,f14,f104,f105,f62,f10,f11",
            "np": 1, "fltt": 2, "invt": 2,
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

    if not all_items:
        return None

    result = []
    for item in all_items:
        code = item.get("f12", "")
        name = item.get("f14", "")
        if not code or not name:
            continue
        result.append({
            "code": code,           # 板块代码 BK0XXX
            "name": name,           # 板块名称
            "pct_chg": item.get("f3", 0),       # 涨跌幅(%)
            "amount": item.get("f6", 0),        # 成交额(元)
            "turnover": item.get("f8", 0),      # 换手率(%)
            "up_count": item.get("f104", 0),    # 上涨家数
            "down_count": item.get("f105", 0),  # 下跌家数
            "net_inflow": item.get("f62", 0),   # 主力净流入(元)
        })
    return result


def fetch_sector_klines_em(sector_code: str, count: int = 30) -> list[dict] | None:
    """
    从东方财富获取指数日K线。

    secid 规则:
      - sector_code 以 "88" 开头: 通达信板块指数 → secid = "1.{code}" (如 1.880001)
      - sector_code 以 "1." 开头: 已是完整 secid → 直接使用
      - 其他(如 BK0XXX): 东方财富板块代码 → secid = "90.{code}"
    """
    if sector_code.startswith("1."):
        secid = sector_code
    elif sector_code.startswith("88"):
        secid = f"1.{sector_code}"
    else:
        secid = f"90.{sector_code}"

    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57",
        "klt": 101,   # 日K
        "fqt": 1,
        "lmt": count,
        "end": "20500101",
    }
    for scheme in ("http", "https"):
        try:
            url = f"{scheme}://push2his.eastmoney.com/api/qt/stock/kline/get"
            resp = requests.get(url, params=params, headers=HEADERS_EM, timeout=15, verify=False)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                klines_raw = data.get("klines", [])
                if klines_raw:
                    result = []
                    for line in klines_raw:
                        parts = line.split(",")
                        if len(parts) >= 7:
                            result.append({
                                "date": parts[0],
                                "open": float(parts[1]),
                                "close": float(parts[2]),
                                "high": float(parts[3]),
                                "low": float(parts[4]),
                                "volume": float(parts[5]),
                                "amount": float(parts[6]),
                            })
                    return result
        except Exception:
            continue
    return None


def fetch_sector_constituents_em(sector_code: str) -> list[tuple[str, str]] | None:
    """从东方财富获取板块成分股列表"""
    params = {
        "pn": 1, "pz": 2000,
        "fs": f"b:{sector_code}",
        "fields": "f12,f14",
        "np": 1, "fltt": 2, "invt": 2,
    }
    for scheme in ("http", "https"):
        try:
            u = f"{scheme}://push2.eastmoney.com/api/qt/clist/get"
            resp = requests.get(u, params=params, headers=HEADERS_EM, timeout=30, verify=False)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                items = data.get("diff", [])
                if items:
                    return [(it["f12"], it["f14"]) for it in items if "f12" in it and "f14" in it]
        except Exception:
            continue
    return None


# ═══════════════════════════════════════════════════════════════
# 统一入口（TDX优先，东方财富降级）
# ═══════════════════════════════════════════════════════════════

def fetch_sector_list(sector_type: str = "industry") -> list[dict]:
    """
    获取板块列表（含行情数据）。

    盘中: EM优先（实时涨跌幅/涨跌家数），本地降级
    盘后: EM优先（收盘数据完整），本地降级

    index_code通过BK→88映射直接计算（BK0437→880437），不依赖TDX block文件。

    Returns:
        list[dict]: 每项含 name, code, index_code, pct_chg, up_count 等
    """
    import os

    em_sectors = fetch_sector_list_em(sector_type)

    if em_sectors:
        # EM可用：以EM数据为主体，补充index_code
        tdx_dir = os.path.join(TDX_DATA_DIR, "vipdoc", "sh", "lday")
        result = []
        for s in em_sectors:
            code = s["code"]  # e.g. "BK0437"
            # BK→88映射: BK0437 → 880437
            index_code = "88" + code[2:] if code.startswith("BK") and len(code) >= 4 else ""
            # 验证本地文件是否存在
            if index_code and not os.path.exists(os.path.join(tdx_dir, f"sh{index_code}.day")):
                index_code = ""  # 本地无文件，后续走TCP/EM
            result.append({
                "name": s["name"],
                "code": code,
                "index_code": index_code,
                "pct_chg": s.get("pct_chg", 0),
                "amount": s.get("amount", 0),
                "turnover": s.get("turnover", 0),
                "up_count": s.get("up_count", 0),
                "down_count": s.get("down_count", 0),
                "net_inflow": s.get("net_inflow", 0),
            })
        return result

    # EM不可用：扫描本地TDX文件构建板块列表
    return _build_sector_list_from_local_tdx()


def _build_sector_list_from_local_tdx() -> list[dict]:
    """
    EM不可用时的降级方案：扫描 vipdoc/sh/lday/sh88XXXX.day 文件，
    用最新K线数据构建板块列表。使用本地名称映射表获取板块中文名。
    """
    import struct

    name_map = _load_sector_name_map()

    lday_dir = os.path.join(TDX_DATA_DIR, "vipdoc", "sh", "lday")
    if not os.path.isdir(lday_dir):
        return []

    result = []
    for fname in os.listdir(lday_dir):
        if not fname.startswith("sh88") or not fname.endswith(".day"):
            continue
        code = fname[2:-4]  # "sh880437.day" → "880437"
        if len(code) != 6:
            continue

        fpath = os.path.join(lday_dir, fname)
        try:
            fsize = os.path.getsize(fpath)
            if fsize < 64:  # 至少2条记录
                continue
            with open(fpath, "rb") as f:
                # 读最后一条记录
                f.seek(-32, 2)
                data = f.read(32)
                date_int = struct.unpack('<I', data[0:4])[0]
                close_last = struct.unpack('<I', data[4:8])[0] / 100.0

                # 读倒数第二条（计算涨跌幅）
                if fsize >= 64:
                    f.seek(-64, 2)
                    data2 = f.read(32)
                    close_prev = struct.unpack('<I', data2[4:8])[0] / 100.0
                else:
                    close_prev = close_last

                # 数据合理性校验：跳过价格异常的文件（基金/债券等）
                if close_last < 10 or close_prev < 10:
                    continue

                pct_chg = round((close_last - close_prev) / close_prev * 100, 2) if close_prev > 0 else 0.0
                date_str = f"{date_int // 10000}-{(date_int % 10000) // 100:02d}-{date_int % 100:02d}"

                # 从映射表获取中文名称，无映射则用代码
                sector_name = name_map.get(code, f"板块{code}")

                result.append({
                    "name": sector_name,
                    "code": f"BK{code[2:]}",  # 880437 → BK0437
                    "index_code": code,
                    "pct_chg": pct_chg,
                    "amount": 0,
                    "turnover": 0,
                    "up_count": 0,
                    "down_count": 0,
                    "net_inflow": 0,
                    "_local_date": date_str,
                })
        except Exception:
            continue

    return sorted(result, key=lambda s: s.get("pct_chg", 0), reverse=True)


def fetch_sector_klines(index_code: str, sector_code: str = "", count: int = 30) -> list[dict]:
    """
    获取板块指数K线。

    优化策略: 板块指数88XXXX的本地.day文件通常不存在于vipdoc中，
    直接尝试TCP（最快），失败则走东方财富HTTP，跳过本地文件读取以避免无谓等待。
    """
    # 优先TCP（板块指数K线通常不在本地文件中，但TCP可以实时获取）
    if index_code:
        bars = fetch_sector_klines_tdx(index_code, count)
        if bars:
            return bars
    # 降级东方财富
    code_to_try = sector_code or index_code
    if code_to_try:
        bars = fetch_sector_klines_em(code_to_try, count)
        if bars:
            return bars
    return []


def fetch_sector_constituents(sector_code: str) -> list[tuple[str, str]]:
    """获取板块成分股（东方财富，TDX不支持实时成分股查询）"""
    stocks = fetch_sector_constituents_em(sector_code)
    return stocks or []


def _read_tdx_daily_binary(market: str, code: str, count: int = 30) -> list[dict] | None:
    """
    直接读取通达信本地 .day 文件（二进制格式）。

    mootdx Reader 的 daily() 对 000001 会读 sz 市场（平安银行），
    而上证指数在 sh 市场。此函数直接指定市场读取，避免歧义。
    """
    import struct
    fpath = os.path.join(TDX_DATA_DIR, "vipdoc", market, "lday", f"{market}{code}.day")
    if not os.path.exists(fpath):
        return None
    try:
        with open(fpath, "rb") as f:
            f.seek(0, 2)
            total = f.tell() // 32
            start = max(0, total - count)
            bars = []
            for i in range(start, total):
                f.seek(i * 32)
                data = f.read(32)
                date_int = struct.unpack('<I', data[0:4])[0]
                close_raw = struct.unpack('<I', data[4:8])[0]
                open_raw = struct.unpack('<I', data[8:12])[0]
                high_raw = struct.unpack('<I', data[12:16])[0]
                low_raw = struct.unpack('<I', data[16:20])[0]
                vol = struct.unpack('<I', data[20:24])[0]
                amount = struct.unpack('<I', data[24:28])[0]
                bars.append({
                    "date": f"{date_int // 10000}-{(date_int % 10000) // 100:02d}-{date_int % 100:02d}",
                    "open": open_raw / 100.0,
                    "close": close_raw / 100.0,
                    "high": high_raw / 100.0,
                    "low": low_raw / 100.0,
                    "volume": float(vol),
                    "amount": float(amount),
                })
            return bars if bars else None
    except Exception:
        return None


def fetch_market_index_klines(count: int = 30) -> list[dict]:
    """
    获取上证指数日K线（用于RS计算基准）。

    盘中: EM API优先（实时数据），本地降级
    盘后: 本地优先（收盘数据最快），EM降级
    """
    if _is_trading_hours():
        # 盘中：EM优先（实时数据）
        bars = fetch_sector_klines_em("1.000001", count)
        if bars:
            return bars
        return _read_tdx_daily_binary("sh", "000001", count) or []
    else:
        # 盘后：本地优先（收盘数据最快）
        bars = _read_tdx_daily_binary("sh", "000001", count)
        if bars:
            return bars
        return fetch_sector_klines_em("1.000001", count) or []
