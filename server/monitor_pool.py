"""
盯盘目标池管理模块

管理盯盘目标股票的持久化存储：新增 → 删除 → 清空
支持从选股跟踪导入和手动添加。

用法:
    pool = get_monitor_pool()
    pool.add_target(...)
    pool.get_targets()
"""

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent / "data"


class MonitorPool:
    """盯盘目标池管理器（线程安全）"""

    MAX_TARGETS = 100

    def __init__(self, data_dir: Path | None = None):
        if data_dir is None:
            data_dir = _DATA_DIR
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.file_path = self.data_dir / "monitor_pool.json"
        self._lock = threading.Lock()

    # ── 读取 ──────────────────────────────────────────────────

    def get_targets(self) -> list[dict]:
        """获取目标池列表"""
        data = self._load()
        return data.get("targets", [])

    def get_target_count(self) -> int:
        """获取目标池数量"""
        return len(self.get_targets())

    # ── 写入 ──────────────────────────────────────────────────

    def add_target(
        self,
        code: str,
        name: str,
        score: float = 0,
        scan_date: str = "",
        strategy_name: str = "",
        industry: str = "",
        concepts: list | None = None,
        added_from: str = "manual",
        anchors: dict | None = None,
        entry_id: str = "",
    ) -> dict | None:
        """
        添加单只股票到目标池。

        Args:
            anchors: 可选锚点数据，供盯盘策略使用
                {yc, ml, sl, yh, avg_vol_5d} 等
            entry_id: 来源跟踪批次 ID（从选股跟踪导入时填写）

        Returns:
            添加成功返回 target dict，重复或达到上限返回 None
        """
        with self._lock:
            data = self._load()
            targets = data.get("targets", [])

            # 去重
            if any(t.get("code") == code for t in targets):
                return None

            # 上限检查
            if len(targets) >= self.MAX_TARGETS:
                return None

            target_id = f"mp_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
            target = {
                "id": target_id,
                "code": code,
                "name": name,
                "score": score,
                "scan_date": scan_date,
                "strategy_name": strategy_name,
                "industry": industry,
                "concepts": concepts or [],
                "added_from": added_from,
                "added_at": datetime.now().isoformat(timespec="seconds"),
                "anchors": anchors or {},
                "entry_id": entry_id,
            }
            targets.append(target)
            data["targets"] = targets
            self._save(data)
            return target

    def add_targets_batch(self, stocks: list[dict], added_from: str = "tracker") -> dict:
        """
        批量添加股票到目标池。

        Args:
            stocks: 股票列表，每个 dict 至少包含 code, name
            added_from: 来源标识

        Returns:
            {"added": N, "skipped": N}
        """
        added = 0
        skipped = 0
        for stock in stocks:
            result = self.add_target(
                code=stock.get("code", ""),
                name=stock.get("name", ""),
                score=stock.get("score", 0),
                scan_date=stock.get("scan_date", ""),
                strategy_name=stock.get("strategy_name", ""),
                industry=stock.get("industry", ""),
                concepts=stock.get("concepts", []),
                added_from=added_from,
                anchors=stock.get("anchors"),
                entry_id=stock.get("entry_id", ""),
            )
            if result:
                added += 1
            else:
                skipped += 1
        return {"added": added, "skipped": skipped}

    def remove_target(self, target_id: str) -> bool:
        """删除单个目标"""
        with self._lock:
            data = self._load()
            targets = data.get("targets", [])
            new_targets = [t for t in targets if t.get("id") != target_id]
            if len(new_targets) == len(targets):
                return False
            data["targets"] = new_targets
            self._save(data)
        return True

    def clear_targets(self) -> bool:
        """清空目标池"""
        with self._lock:
            data = self._load()
            data["targets"] = []
            self._save(data)
        return True

    def import_from_tracker(self, tracker_entries: list[dict], entry_ids: list[str] | None = None) -> dict:
        """
        从选股跟踪条目导入股票到目标池。

        Args:
            tracker_entries: Tracker.get_entries() 返回的条目列表
            entry_ids: 指定要导入的 entry ID 列表，None 则导入全部

        Returns:
            {"added": N, "skipped": N}
        """
        stocks_to_add = []
        for entry in tracker_entries:
            if entry_ids and entry.get("id") not in entry_ids:
                continue
            strategy_name = entry.get("strategy_name", "")
            scan_date = entry.get("scan_date", "")
            entry_id = entry.get("id", "")
            for stock in entry.get("stocks", []):
                stocks_to_add.append({
                    "code": stock.get("code", ""),
                    "name": stock.get("name", ""),
                    "score": stock.get("score", 0),
                    "scan_date": scan_date,
                    "strategy_name": strategy_name,
                    "industry": stock.get("industry", ""),
                    "concepts": stock.get("concepts", []),
                    "entry_id": entry_id,
                })

        return self.add_targets_batch(stocks_to_add, added_from="tracker")

    # ── 文件读写 ──────────────────────────────────────────────

    def _load(self) -> dict:
        try:
            if self.file_path.exists():
                return json.loads(self.file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
        return {"max_targets": self.MAX_TARGETS, "targets": []}

    def _save(self, data: dict):
        tmp = self.file_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.file_path)


# ── 全局单例 ──────────────────────────────────────────────────

_pool = None


def get_monitor_pool() -> MonitorPool:
    global _pool
    if _pool is None:
        _pool = MonitorPool()
    return _pool
