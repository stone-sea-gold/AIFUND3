"""
选股跟踪持久化模块

管理 tracker.json 的读写、FIFO覆盖（最多7次扫描）、价格刷新。

用法:
    tracker = get_tracker()
    tracker.add_entry(...)       # 扫描完成时调用
    tracker.get_entries()        # 获取所有记录
    tracker.get_entries_grouped() # 按策略分组
    tracker.refresh_prices()     # 刷新所有标的的最新价格
    tracker.delete_entry(id)     # 删除指定记录
"""

import os
import sys
import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
from pathlib import Path

# ── 路径处理 ──
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
_SCRIPTS_DIR = _PROJECT_ROOT / "脚本"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


class Tracker:
    """选股结果持久化追踪器"""

    def __init__(self, data_dir: Path | None = None):
        if data_dir is None:
            data_dir = _PROJECT_ROOT / "选股" / "选股结果"
        self.data_dir = Path(data_dir)
        self.file_path = self.data_dir / "tracker.json"
        self.max_entries = 7
        self._lock = threading.Lock()

    # ── 读取 ──────────────────────────────────────────────

    def _load(self) -> dict:
        """加载 tracker.json，文件不存在返回空结构"""
        try:
            if self.file_path.exists():
                data = json.loads(self.file_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "entries" in data:
                    return data
        except (json.JSONDecodeError, Exception):
            pass
        return {"max_entries": self.max_entries, "entries": []}

    def _save(self, data: dict):
        """写回 tracker.json"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.file_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.file_path)

    def get_entries(self) -> list[dict]:
        """获取所有跟踪记录（按创建时间升序）"""
        return self._load().get("entries", [])

    def get_entries_grouped(self) -> dict:
        """按策略分组，每组内按日期降序"""
        entries = self.get_entries()
        grouped = {}
        for e in reversed(entries):  # 最新在前
            strat = e.get("strategy", "unknown")
            if strat not in grouped:
                grouped[strat] = {
                    "strategy_name": e.get("strategy_name", strat),
                    "entries": [],
                }
            grouped[strat]["entries"].append(e)
        return grouped

    # ── 写入 ──────────────────────────────────────────────

    def add_entry(
        self,
        task_id: str,
        scan_date: str,
        strategy: str,
        strategy_name: str,
        pool_name: str,
        top_n: int,
        stocks: list[dict],
    ) -> str:
        """
        添加新扫描记录，自动 FIFO 淘汰最旧记录。

        Args:
            task_id: 任务 ID
            scan_date: 扫描日期 (YYYY-MM-DD)
            strategy: 策略模块名 (如 "b1")
            strategy_name: 策略展示名 (如 "B1量价共振")
            pool_name: 股票池名称
            top_n: 输出前 N 名
            stocks: 扫描结果列表，每项含 code/name/score/latest_info

        Returns:
            entry_id (task_id)
        """
        with self._lock:
            data = self._load()

            entry = {
                "id": task_id,
                "scan_date": scan_date,
                "strategy": strategy,
                "strategy_name": strategy_name,
                "pool_name": pool_name,
                "top_n": top_n,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "stocks": [
                    {
                        "code": s["code"],
                        "name": s["name"],
                        "score": s["score"],
                        "scan_price": s["latest_info"]["close"],
                        "scan_date": s["latest_info"].get("date", scan_date),
                        "latest_price": None,
                        "latest_date": None,
                        "pct_change": None,
                        "industry": s.get("industry", ""),
                        "concepts": s.get("concepts", []),
                    }
                    for s in stocks
                ],
            }

            entries = data.get("entries", [])
            entries.append(entry)

            # FIFO: 只保留最近 max_entries 条
            while len(entries) > self.max_entries:
                removed = entries.pop(0)

            data["entries"] = entries
            data["max_entries"] = self.max_entries
            self._save(data)

            return task_id

    def delete_entry(self, entry_id: str) -> bool:
        """删除指定记录，返回是否成功"""
        with self._lock:
            data = self._load()
            old_len = len(data.get("entries", []))
            data["entries"] = [e for e in data.get("entries", []) if e["id"] != entry_id]
            if len(data["entries"]) == old_len:
                return False
            self._save(data)
            return True

    # ── 价格刷新 ──────────────────────────────────────────

    def refresh_prices(self) -> dict:
        """
        刷新所有跟踪标的最新收盘价。

        数据源优先级: TDX TCP 批量报价（快速）→ 逐只 get_klines（降级）
        """
        from 选股.kline_source import get_klines

        entries = self.get_entries()
        if not entries:
            return {
                "refreshed": 0, "failed": 0, "skipped": 0,
                "refresh_time": datetime.now().strftime("%m-%d %H:%M"),
            }

        today_str = date.today().strftime("%Y-%m-%d")

        # 收集所有跟踪代码（盘中价格实时变化，每次全部重取）
        all_codes = set()
        skip_count = 0
        for e in entries:
            for s in e.get("stocks", []):
                all_codes.add(s["code"])
                if s.get("latest_date") == today_str:
                    skip_count += 1

        codes_to_fetch = list(all_codes)

        if not codes_to_fetch:
            return {
                "refreshed": 0, "failed": 0, "skipped": 0,
                "refresh_time": datetime.now().strftime("%m-%d %H:%M"),
            }

        price_map: dict[str, dict] = {}
        refreshed = 0
        failed = 0

        # ── 快速路径：TDX TCP 批量报价 ──
        tcp_ok = False
        try:
            from 选股.tdx_pool import get_pool
            pool = get_pool()
            quotes = pool.get_quotes_batch(codes_to_fetch)
            if quotes:
                tcp_ok = True
                for q in quotes:
                    code = q.get("code", "")
                    if code:
                        price_map[code] = {
                            "price": q.get("price", 0),
                            "date": today_str,
                        }
                refreshed = len(price_map)
        except Exception:
            pass

        # ── 降级路径：逐只 get_klines ──
        if not tcp_ok:
            def _fetch_one(code: str) -> tuple[str, dict | None]:
                try:
                    _, klines = get_klines(code, count=5, period="day")
                    if klines:
                        last_k = klines[-1]
                        return code, {
                            "price": round(float(last_k.get("close", 0)), 2),
                            "date": last_k.get("date", ""),
                        }
                except Exception:
                    pass
                return code, None

            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {executor.submit(_fetch_one, c): c for c in codes_to_fetch}
                for future in as_completed(futures):
                    try:
                        code, result = future.result()
                        if result:
                            price_map[code] = result
                            refreshed += 1
                        else:
                            failed += 1
                    except Exception:
                        failed += 1

        if not price_map:
            return {
                "refreshed": 0, "failed": failed, "skipped": skip_count,
                "refresh_time": datetime.now().strftime("%m-%d %H:%M"),
            }

        # 写回文件
        with self._lock:
            data = self._load()
            for e in data.get("entries", []):
                for s in e.get("stocks", []):
                    p = price_map.get(s["code"])
                    if p is None:
                        continue
                    s["latest_price"] = p["price"]
                    s["latest_date"] = p["date"]
                    scan_p = s.get("scan_price")
                    if scan_p and scan_p > 0 and p["price"] > 0:
                        s["pct_change"] = round((p["price"] - scan_p) / scan_p * 100, 2)
            self._save(data)

        return {
            "refreshed": refreshed,
            "failed": failed,
            "skipped": skip_count,
            "refresh_time": datetime.now().strftime("%m-%d %H:%M"),
        }


# ── 全局单例 ──────────────────────────────────────────────

_tracker = None


def get_tracker() -> Tracker:
    global _tracker
    if _tracker is None:
        _tracker = Tracker()
    return _tracker
