"""
盯盘任务管理器

管理盯盘任务的生命周期：开启 → 后台轮询 → 信号触发 → 停止
每3秒轮询一次实时报价，按策略的 evaluate/invalidation 函数判断信号。

用法:
    manager = get_monitor_manager()
    manager.start(strategies=["example_monitor"])
    status = manager.get_status()
    manager.stop()
"""

import hashlib
import inspect
import sys
import time
import threading
import traceback
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── 路径处理 ──
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


class MonitorManager:
    """盯盘任务管理器（线程安全）"""

    POLL_INTERVAL = 3       # 轮询间隔（秒）
    MINUTE_CACHE_TTL = 30   # 分钟K线缓存有效期（秒）
    MAX_CONSECUTIVE_ERRORS = 10  # 连续错误次数上限

    # 函数签名缓存（避免重复 inspect）
    _sig_cache: dict[int, set[str]] = {}

    def __init__(self):
        self._lock = threading.Lock()
        self._task = None  # 当前任务状态（全局唯一任务）

    # ── 公共 API ──────────────────────────────────────────────

    def start(self, strategies: list[str]) -> dict:
        """
        开启盯盘任务。

        Args:
            strategies: 要激活的策略文件名列表

        Returns:
            {"status": "running", ...} 或 {"error": "..."}
        """
        with self._lock:
            if self._task and self._task["status"] == "running":
                return {"error": "盯盘任务已在运行中，请先停止"}

        # 验证策略
        from server.monitor_strategy_loader import load_monitor_strategy
        loaded_strategies = {}
        for name in strategies:
            try:
                module = load_monitor_strategy(name)
                loaded_strategies[name] = module
            except Exception as e:
                return {"error": f"加载策略 '{name}' 失败: {e}"}

        if not loaded_strategies:
            return {"error": "未选择有效策略"}

        # 验证目标池
        from server.monitor_pool import get_monitor_pool
        pool = get_monitor_pool()
        targets = pool.get_targets()
        if not targets:
            return {"error": "目标池为空，请先添加盯盘目标"}

        # 创建任务
        task = {
            "status": "running",
            "selected_strategies": list(loaded_strategies.keys()),
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "stopped_at": None,
            "triggered_signals": {},  # {signal_key: {id, stock_code, stock_name, ...}}
            "stats": {
                "total_ticks": 0,
                "total_signals": 0,
                "last_tick_at": None,
                "errors": 0,
            },
            "_cancelled": threading.Event(),
            "_thread": None,
            "_loaded_strategies": loaded_strategies,
        }

        with self._lock:
            self._task = task

        # 启动后台线程
        t = threading.Thread(target=self._run_loop, daemon=True)
        t.start()
        task["_thread"] = t

        return {
            "status": "running",
            "strategies": list(loaded_strategies.keys()),
            "target_count": len(targets),
        }

    def stop(self) -> dict:
        """停止盯盘任务，清空触发记录。"""
        with self._lock:
            task = self._task
            if not task or task["status"] != "running":
                return {"error": "没有运行中的盯盘任务"}

        task["_cancelled"].set()
        thread = task.get("_thread")
        if thread:
            thread.join(timeout=self.POLL_INTERVAL + 2)

        with self._lock:
            task["status"] = "stopped"
            task["stopped_at"] = datetime.now().isoformat(timespec="seconds")
            # 清空触发记录
            task["triggered_signals"] = {}
            task["_loaded_strategies"] = None
            self._task = task

        return {"status": "stopped"}

    def get_status(self) -> dict:
        """获取任务状态和触发信号。"""
        with self._lock:
            task = self._task
            if not task:
                return {
                    "status": "idle",
                    "selected_strategies": [],
                    "started_at": None,
                    "stats": {},
                    "triggered": {},
                }

            return {
                "status": task["status"],
                "selected_strategies": task["selected_strategies"],
                "started_at": task["started_at"],
                "stopped_at": task.get("stopped_at"),
                "stats": dict(task["stats"]),
                "triggered": self._serialize_triggered(task["triggered_signals"]),
            }

    def get_triggereds(self) -> dict:
        """获取已触发信号（按策略分组）。"""
        with self._lock:
            task = self._task
            if not task:
                return {}
            return self._group_triggered(task["triggered_signals"])

    def remove_triggered(self, signal_id: str) -> bool:
        """删除单条触发记录。"""
        with self._lock:
            task = self._task
            if not task:
                return False
            signals = task["triggered_signals"]
            key_to_remove = None
            for key, sig in signals.items():
                if sig.get("id") == signal_id:
                    key_to_remove = key
                    break
            if key_to_remove:
                del signals[key_to_remove]
                return True
            return False

    def is_running(self) -> bool:
        with self._lock:
            return self._task is not None and self._task["status"] == "running"

    # ── 内部轮询逻辑 ──────────────────────────────────────────

    def _run_loop(self):
        """后台轮询主循环。"""
        task = self._task
        cancelled = task["_cancelled"]
        loaded_strategies = task["_loaded_strategies"]
        consecutive_errors = 0

        # 分钟K线缓存: {(code, period): (timestamp, bars)}
        minute_cache = {}

        while not cancelled.is_set():
            try:
                tick_start = time.time()

                # 1. 获取目标池
                from server.monitor_pool import get_monitor_pool
                targets = get_monitor_pool().get_targets()
                if not targets:
                    self._wait(cancelled, self.POLL_INTERVAL)
                    continue

                codes = [t["code"] for t in targets]
                code_to_target = {t["code"]: t for t in targets}

                # 2. 批量拉取实时报价
                quotes = self._fetch_quotes(codes)
                if quotes is None:
                    consecutive_errors += 1
                    with self._lock:
                        task["stats"]["errors"] += 1
                    if consecutive_errors >= self.MAX_CONSECUTIVE_ERRORS:
                        print(f"[monitor] 连续 {consecutive_errors} 次错误，自动停止")
                        break
                    self._wait(cancelled, self.POLL_INTERVAL)
                    continue

                consecutive_errors = 0
                code_to_quote = {q["code"]: q for q in quotes}

                # 3. 按需拉取分钟K线（带缓存，按 period 分 key）
                now_ts = time.time()
                need_minute = set()   # (code, period) pairs
                strategy_periods = {}  # {strategy_name: period}
                for name, module in loaded_strategies.items():
                    if module.NEED_MINUTE_KLINE:
                        period = getattr(module, "MINUTE_PERIOD", "1")
                        strategy_periods[name] = period
                        for code in codes:
                            cache_key = (code, period)
                            cached = minute_cache.get(cache_key)
                            if cached is None or (now_ts - cached[0]) >= self.MINUTE_CACHE_TTL:
                                need_minute.add(cache_key)

                if need_minute:
                    # 并行拉取分钟K线
                    def _fetch_minute_item(code: str, period: str):
                        bars = self._fetch_minute_data(code, period)
                        return code, period, bars

                    with ThreadPoolExecutor(max_workers=8) as executor:
                        futures = {
                            executor.submit(_fetch_minute_item, code, period): (code, period)
                            for code, period in need_minute
                        }
                        for future in as_completed(futures):
                            try:
                                code, period, bars = future.result()
                                if bars:
                                    minute_cache[(code, period)] = (now_ts, bars)
                            except Exception:
                                pass

                # 4. 遍历评估
                new_triggers = []
                with self._lock:
                    triggered = task["triggered_signals"]

                    for code in codes:
                        quote = code_to_quote.get(code)
                        if not quote:
                            continue
                        target = code_to_target.get(code, {})

                        for name, module in loaded_strategies.items():
                            minute_bars = None
                            if module.NEED_MINUTE_KLINE:
                                period = strategy_periods.get(name, "1")
                                cached = minute_cache.get((code, period))
                                if cached:
                                    minute_bars = cached[1]

                            for signal in module.SIGNALS:
                                sig_key = self._make_signal_key(code, signal["name"], name)
                                existing = triggered.get(sig_key)

                                try:
                                    if existing:
                                        # 检查是否失效
                                        inv_func = signal.get("invalidation")
                                        if inv_func and self._call_signal_func(inv_func, quote, minute_bars, target):
                                            del triggered[sig_key]
                                            task["stats"]["total_signals"] -= 1
                                    else:
                                        # 检查是否触发
                                        eval_func = signal.get("evaluate")
                                        if eval_func and self._call_signal_func(eval_func, quote, minute_bars, target):
                                            signal_id = f"sig_{int(time.time())}_{hashlib.md5(sig_key.encode()).hexdigest()[:6]}"
                                            triggered[sig_key] = {
                                                "id": signal_id,
                                                "stock_code": code,
                                                "stock_name": target.get("name", ""),
                                                "signal_name": signal["name"],
                                                "signal_desc": signal.get("desc", ""),
                                                "level": signal.get("level", ""),
                                                "strategy_name": name,
                                                "strategy_display": module.STRATEGY_NAME,
                                                "score": target.get("score", 0),
                                                "industry": target.get("industry", ""),
                                                "price": quote.get("price", 0),
                                                "pct_chg": quote.get("pct_chg", 0),
                                                "triggered_at": datetime.now().isoformat(timespec="seconds"),
                                            }
                                            task["stats"]["total_signals"] += 1
                                            new_triggers.append(triggered[sig_key])
                                except Exception as e:
                                    print(f"[monitor] evaluate error: {code}/{signal['name']}: {e}")

                    # 更新统计
                    task["stats"]["total_ticks"] += 1
                    task["stats"]["last_tick_at"] = datetime.now().isoformat(timespec="seconds")

            except Exception as e:
                consecutive_errors += 1
                with self._lock:
                    task["stats"]["errors"] += 1
                print(f"[monitor] tick error: {e}")
                traceback.print_exc()

            self._wait(cancelled, self.POLL_INTERVAL)

        # 循环结束，标记停止
        with self._lock:
            if task["status"] == "running":
                task["status"] = "stopped"
                task["stopped_at"] = datetime.now().isoformat(timespec="seconds")

    def _fetch_quotes(self, codes: list[str]) -> list[dict] | None:
        """批量拉取实时报价。"""
        try:
            from 选股.tdx_pool import get_pool
            pool = get_pool()
            result = pool.get_quotes_batch(codes)
            return result
        except Exception as e:
            print(f"[monitor] get_quotes_batch error: {e}")
            return None

    def _fetch_minute_data(self, code: str, period: str = "1") -> list | None:
        """拉取单只分钟K线数据。"""
        try:
            from 选股.tdx_pool import get_pool
            pool = get_pool()
            if period == "5":
                return pool.get_minute5_data(code)
            return pool.get_minute_data(code)
        except Exception as e:
            print(f"[monitor] fetch_minute_data({code}, {period}min) error: {e}")
            return None

    def _serialize_triggered(self, triggered: dict) -> dict:
        """将触发信号按策略分组序列化。"""
        grouped = {}
        for sig_key, sig in triggered.items():
            strategy = sig.get("strategy_name", "unknown")
            if strategy not in grouped:
                grouped[strategy] = {
                    "strategy_display": sig.get("strategy_display", strategy),
                    "signals": [],
                }
            grouped[strategy]["signals"].append({
                "id": sig["id"],
                "stock_code": sig["stock_code"],
                "stock_name": sig["stock_name"],
                "signal_name": sig["signal_name"],
                "signal_desc": sig.get("signal_desc", ""),
                "level": sig.get("level", ""),
                "score": sig.get("score", 0),
                "industry": sig.get("industry", ""),
                "price": sig.get("price", 0),
                "pct_chg": sig.get("pct_chg", 0),
                "triggered_at": sig.get("triggered_at", ""),
            })
        return grouped

    def _group_triggered(self, triggered: dict) -> dict:
        """同 _serialize_triggered，用于外部查询。"""
        return self._serialize_triggered(triggered)

    @staticmethod
    def _make_signal_key(code: str, signal_name: str, strategy_name: str) -> str:
        """生成信号唯一键。"""
        return f"{code}_{signal_name}_{strategy_name}"

    @classmethod
    def _call_signal_func(cls, func, quote: dict, minute_bars: list | None, target: dict) -> bool:
        """调用信号评估/失效函数，根据签名自动传入 target（向后兼容）。"""
        func_id = id(func)
        if func_id not in cls._sig_cache:
            params = set(inspect.signature(func).parameters.keys())
            cls._sig_cache[func_id] = params
        kwargs = {"quote": quote, "minute_bars": minute_bars}
        if "target" in cls._sig_cache[func_id]:
            kwargs["target"] = target
        return func(**kwargs)

    @staticmethod
    def _wait(cancelled: threading.Event, seconds: float):
        """可中断的等待。"""
        cancelled.wait(timeout=seconds)


# ── 全局单例 ──────────────────────────────────────────────────

_manager = MonitorManager()


def get_monitor_manager() -> MonitorManager:
    return _manager
