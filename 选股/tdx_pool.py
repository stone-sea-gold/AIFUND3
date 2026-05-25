"""
TDX TCP 持久连接池
===================

维护一条到最快通达信行情服务器的长连接，消除重复建连开销（~220ms → 0ms）。
线程安全（RLock），自动探活 + 重连 + 服务器排序。

用法:
    from 选股.tdx_pool import get_pool

    pool = get_pool()
    bars = pool.get_security_bars("000021", count=20)       # 日K线
    quotes = pool.get_quotes_batch(["000021", "600519"])    # 实时报价
    minute = pool.get_minute_data("000021")                 # 1分钟分时

数据源: 通达信 TCP 行情协议（mootdx TdxHq_API）
"""

import time
import json
import threading
from pathlib import Path
from datetime import date

# ── 延迟导入，避免循环依赖 ──
_mootdx_api = None
_mootdx_quotes = None


def _lazy_import():
    global _mootdx_api, _mootdx_quotes
    if _mootdx_api is None:
        from mootdx.quotes import TdxHq_API
        _mootdx_api = TdxHq_API
        _mootdx_quotes = True


# ═══════════════════════════════════════════════════════════════
# TCP Bar 归一化（从 kline_source.py 迁移至此）
# ═══════════════════════════════════════════════════════════════

def normalize_tcp_bar(bar: dict, prev_close: float) -> dict:
    """
    将 mootdx TCP 返回的 K 线记录转为项目标准 dict 格式。

    TCP 字段: open, close, high, low, vol(手), amount, year, month, day, hour, minute
    标准字段: date, open, close, high, low, volume, amount, amplitude, pct_chg, change, turnover
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


def normalize_tcp_bars(bars: list[dict]) -> list[dict]:
    """批量归一化 TCP K 线列表"""
    klines = []
    prev_close = None
    for bar in bars:
        k = normalize_tcp_bar(bar, prev_close or float(bar.get('close', 0)))
        klines.append(k)
        prev_close = k["close"]
    return klines


# ═══════════════════════════════════════════════════════════════
# 连接池
# ═══════════════════════════════════════════════════════════════

# 硬编码保底快速服务器（benchmark 验证 ~220ms 建连，其他全部超时 3s）
_FALLBACK_SERVER = ("110.41.147.114", 7709)

# 服务器探测超时（秒）
_PROBE_TIMEOUT = 0.5


def _load_server_list() -> list[tuple[str, int]]:
    """从 mootdx config 加载 HQ 服务器列表"""
    servers = []
    try:
        config_path = Path.home() / ".mootdx" / "config.json"
        if config_path.exists():
            data = json.loads(config_path.read_text(encoding="utf-8"))
            for s in data.get("SERVER", {}).get("HQ", []):
                servers.append((s[1], int(s[2])))
    except Exception:
        pass
    return servers


class TdxConnectionPool:
    """
    TDX TCP 持久连接管理器（单例）。

    特性:
      - 维护一条到最快服务器的长连接
      - 线程安全（RLock）
      - 启动时探测全部服务器，按响应时间排序
      - 查询前自动检测连接健康，断线自动重连
      - 支持日K线、批量实时报价、分钟线
    """

    def __init__(self):
        _lazy_import()
        self._api = None
        self._host: str | None = None
        self._port: int | None = None
        self._lock = threading.RLock()
        self._last_used: float = 0.0
        self._healthy_servers: list[tuple[str, int, float]] = []  # (host, port, latency_ms)
        self._probe_done = False

    # ── 服务器探测 ──────────────────────────────────────────

    def _probe_server(self, host: str, port: int, timeout: float = _PROBE_TIMEOUT) -> float | None:
        """探测单台服务器，成功返回延迟(ms)，失败返回 None"""
        api = None
        try:
            api = _mootdx_api()
            t0 = time.time()
            api.connect(host, port, time_out=timeout)
            elapsed = (time.time() - t0) * 1000
            api.disconnect()
            return elapsed
        except Exception:
            return None
        finally:
            if api:
                try:
                    api.disconnect()
                except Exception:
                    pass

    def _health_check(self):
        """探测全部服务器，按延迟排序存入 _healthy_servers"""
        servers = _load_server_list()
        if not servers:
            servers = [_FALLBACK_SERVER]

        results: list[tuple[str, int, float]] = []

        # 并行探测（ThreadPoolExecutor）
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=min(10, len(servers))) as executor:
            futures = {executor.submit(self._probe_server, h, p): (h, p)
                       for h, p in servers}
            for future in as_completed(futures):
                h, p = futures[future]
                try:
                    latency = future.result()
                    if latency is not None:
                        results.append((h, p, latency))
                except Exception:
                    pass

        # 确保保底服务器在列表中
        if not any(r[0] == _FALLBACK_SERVER[0] for r in results):
            # 单独探测保底服务器
            lat = self._probe_server(_FALLBACK_SERVER[0], _FALLBACK_SERVER[1], timeout=3.0)
            if lat is not None:
                results.append((_FALLBACK_SERVER[0], _FALLBACK_SERVER[1], lat))

        results.sort(key=lambda x: x[2])
        self._healthy_servers = results
        self._probe_done = True

    # ── 连接管理 ────────────────────────────────────────────

    def _is_alive(self) -> bool:
        """快速检测当前连接是否存活"""
        if self._api is None:
            return False
        try:
            # 发送轻量请求验证连接
            count = self._api.get_security_count(0)
            return count is not None and count > 0
        except Exception:
            return False

    def _ensure_connected(self):
        """确保连接存活（断线自动重连）"""
        if self._api is not None and self._is_alive():
            self._last_used = time.time()
            return

        # 首次使用：执行服务器探测
        if not self._probe_done:
            self._health_check()

        # 依次尝试健康服务器
        if not self._healthy_servers:
            self._healthy_servers = [(_FALLBACK_SERVER[0], _FALLBACK_SERVER[1], 0.0)]

        for host, port, _lat in self._healthy_servers:
            try:
                api = _mootdx_api()
                api.connect(host, port, time_out=5)
                self._api = api
                self._host = host
                self._port = port
                self._last_used = time.time()
                return
            except Exception:
                continue

        raise ConnectionError("所有 TDX 行情服务器均不可达")

    def close(self):
        """关闭连接"""
        with self._lock:
            if self._api:
                try:
                    self._api.disconnect()
                except Exception:
                    pass
            self._api = None
            self._host = None
            self._port = None

    def is_connected(self) -> bool:
        """当前是否有可用连接"""
        with self._lock:
            return self._api is not None and self._is_alive()

    @property
    def server_info(self) -> tuple[str | None, int | None]:
        """返回当前连接的 (host, port)，未连接返回 (None, None)"""
        return (self._host, self._port)

    # ── 数据接口 ────────────────────────────────────────────

    def get_security_bars(
        self, code: str, start: int = 0, count: int = 800
    ) -> list[dict] | None:
        """
        获取日K线数据（通过持久连接）。

        Args:
            code: 股票代码，如 "000021"
            start: 起始位置，0=最早
            count: 拉取根数，max ~800

        Returns:
            list[OrderedDict] 原始 bar 列表（含 year/month/day/open/close/...），或 None
        """
        market = 1 if code.startswith("6") else 0
        with self._lock:
            try:
                self._ensure_connected()
                return self._api.get_security_bars(9, market, code, start, count)
            except Exception:
                # 连接可能已断，标记失效，下次自动重连
                self._api = None
                return None

    def get_quotes_batch(self, codes: list[str]) -> list[dict] | None:
        """
        批量获取实时行情快照。

        Args:
            codes: 股票代码列表，如 ["000021", "600519"]

        Returns:
            list[dict] 每只包含:
              code, price, last_close, open, high, low, volume, amount,
              pct_chg, servertime
            或 None（连接失败）
        """
        if not codes:
            return []

        # 转换为 [(market, code), ...] 格式
        stocks = [(1 if c.startswith("6") else 0, c) for c in codes]

        # TDX 单次查询有上限（实测~80只），分片处理
        chunk_size = 50
        all_quotes = []

        with self._lock:
            try:
                self._ensure_connected()

                for i in range(0, len(stocks), chunk_size):
                    chunk = stocks[i:i + chunk_size]
                    raw = self._api.get_security_quotes(chunk)
                    if raw:
                        for item in raw:
                            price = float(item.get("price", 0))
                            last_close = float(item.get("last_close", 0))
                            pct = (
                                ((price - last_close) / last_close * 100)
                                if last_close > 0
                                else 0.0
                            )
                            all_quotes.append({
                                "code": item.get("code", ""),
                                "price": price,
                                "last_close": last_close,
                                "open": float(item.get("open", 0)),
                                "high": float(item.get("high", 0)),
                                "low": float(item.get("low", 0)),
                                "volume": int(item.get("vol", 0)),
                                "amount": float(item.get("amount", 0)),
                                "pct_chg": round(pct, 2),
                                "servertime": item.get("servertime", ""),
                            })

                return all_quotes
            except Exception:
                self._api = None
                return None

    def get_minute_data(self, code: str) -> list[dict] | None:
        """
        获取1分钟K线（当日分时数据）。

        Returns:
            list[OrderedDict] 或 None
        """
        market = 1 if code.startswith("6") else 0
        with self._lock:
            try:
                self._ensure_connected()
                return self._api.get_security_bars(8, market, code, 0, 240)
            except Exception:
                self._api = None
                return None

    def get_minute5_data(self, code: str) -> list[dict] | None:
        """获取5分钟K线"""
        market = 1 if code.startswith("6") else 0
        with self._lock:
            try:
                self._ensure_connected()
                return self._api.get_security_bars(7, market, code, 0, 100)
            except Exception:
                self._api = None
                return None


# ═══════════════════════════════════════════════════════════════
# 全局单例
# ═══════════════════════════════════════════════════════════════

_pool_instance = None
_pool_lock = threading.Lock()


def get_pool() -> TdxConnectionPool:
    """获取全局 TDX 连接池单例"""
    global _pool_instance
    if _pool_instance is None:
        with _pool_lock:
            if _pool_instance is None:
                _pool_instance = TdxConnectionPool()
    return _pool_instance
