"""
板块数据持久化层

管理板块分析结果的 JSON 持久化:
  - sector_snapshot.json: 最新一次分析的完整结果
  - sector_history.json:  历史快照（FIFO，保留最近14天）
"""

import json
import threading
from datetime import datetime
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_SNAPSHOT_FILE = _DATA_DIR / "sector_snapshot.json"
_HISTORY_FILE = _DATA_DIR / "sector_history.json"


class SectorStorage:
    """板块分析结果持久化管理器（线程安全）"""

    def __init__(self, data_dir: Path | None = None):
        if data_dir is None:
            data_dir = _DATA_DIR
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.snapshot_path = self.data_dir / "sector_snapshot.json"
        self.history_path = self.data_dir / "sector_history.json"
        self.max_history = 14
        self._lock = threading.Lock()

    def load_snapshot(self) -> dict | None:
        """加载最新分析结果"""
        try:
            if self.snapshot_path.exists():
                return json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
        return None

    def save_snapshot(self, result: dict):
        """保存最新分析结果"""
        with self._lock:
            tmp = self.snapshot_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.snapshot_path)

    def load_history(self) -> list[dict]:
        """加载历史记录（按日期升序）"""
        try:
            if self.history_path.exists():
                data = json.loads(self.history_path.read_text(encoding="utf-8"))
                return data.get("entries", [])
        except (json.JSONDecodeError, OSError):
            pass
        return []

    def add_history_entry(self, entry: dict):
        """
        添加一条历史记录，自动 FIFO 淘汰最旧记录。

        Args:
            entry: {"date": "2026-05-28", "mainline": [...], "potential": [...], "fading": [...]}
        """
        with self._lock:
            entries = self.load_history()
            entries.append(entry)
            while len(entries) > self.max_history:
                entries.pop(0)
            data = {"max_entries": self.max_history, "entries": entries}
            tmp = self.history_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.history_path)
