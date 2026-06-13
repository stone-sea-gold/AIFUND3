"""
回测任务管理器 — 后台线程执行回测，支持进度追踪和结果持久化

用法:
    manager = get_backtest_manager()
    task_id = manager.submit(strategy="b1", pool="沪深300", top_n=10, holding_days=5)
    task = manager.get_task(task_id)
"""

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path


class BacktestTask:
    """单个回测任务的状态容器"""

    def __init__(self, task_id: str, strategy: str, pool: str,
                 top_n: int, min_score: int, holding_days: int,
                 initial_capital: float,
                 start_date: str | None = None, end_date: str | None = None):
        self.task_id = task_id
        self.strategy = strategy
        self.pool = pool
        self.top_n = top_n
        self.min_score = min_score
        self.holding_days = holding_days
        self.initial_capital = initial_capital
        self.stop_loss_params = {}  # 使用 stop_loss.py 中的默认参数
        self.start_date = start_date
        self.end_date = end_date

        self.status = "pending"  # pending → running → completed | failed
        self.progress = {"current_date": "", "rounds_done": 0, "elapsed": 0}
        self.result = None
        self.error = None
        self.created_at = datetime.now()
        self.completed_at = None
        self._started_at = None
        self._cancelled = threading.Event()

    def cancel(self):
        self._cancelled.set()


class BacktestManager:
    """回测任务管理器（线程安全）"""

    def __init__(self):
        self._tasks: dict[str, BacktestTask] = {}
        self._lock = threading.Lock()
        self._max_tasks = 10

    def submit(self, strategy: str = "b1", pool: str = "沪深300",
               top_n: int = 10, min_score: int = 25,
               holding_days: int = 3, initial_capital: float = 100000,
               start_date: str | None = None, end_date: str | None = None) -> str:
        """提交回测任务，返回 task_id"""
        task_id = uuid.uuid4().hex[:8]
        task = BacktestTask(task_id, strategy, pool, top_n, min_score,
                            holding_days, initial_capital,
                            start_date, end_date)

        with self._lock:
            self._tasks[task_id] = task
            self._cleanup_locked()

        t = threading.Thread(target=self._run_backtest, args=(task_id,), daemon=True)
        t.start()
        return task_id

    def get_task(self, task_id: str) -> BacktestTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "task_id": t.task_id,
                    "status": t.status,
                    "strategy": t.strategy,
                    "pool": t.pool,
                    "top_n": t.top_n,
                    "holding_days": t.holding_days,
                    "created_at": t.created_at.isoformat(),
                    "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                    "progress": dict(t.progress),
                }
                for t in sorted(
                    self._tasks.values(),
                    key=lambda x: x.created_at,
                    reverse=True,
                )[:self._max_tasks]
            ]

    def delete_task(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status in ("completed", "failed", "cancelled"):
                del self._tasks[task_id]
                return True
            return False

    def stop_task(self, task_id: str) -> bool:
        """停止正在运行的回测任务，删除已保存的结果文件"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
        if task.status != "running":
            return False
        task.cancel()
        # 删除可能已部分写入的结果文件
        self._delete_saved_result(task_id)
        return True

    def _run_backtest(self, task_id: str):
        task = self._get_task(task_id)
        if not task:
            return

        try:
            task.status = "running"
            task._started_at = time.time()

            from 选股.backtest.engine import BacktestEngine

            engine = BacktestEngine(
                strategy_name=task.strategy,
                pool_name=task.pool,
                top_n=task.top_n,
                min_score=task.min_score,
                holding_days=task.holding_days,
                initial_capital=task.initial_capital,
            )

            def _on_progress(phase: str, current: int, total: int, info: str):
                task.progress["current_date"] = f"{phase} {info}"
                task.progress["rounds_done"] = current
                task.progress["elapsed"] = round(time.time() - task._started_at, 1)

            result = engine.run(
                start_date=task.start_date,
                end_date=task.end_date,
                verbose=False,
                cancelled=task._cancelled,
                progress_callback=_on_progress,
            )

            if task._cancelled.is_set():
                with self._lock:
                    task.status = "cancelled"
                    task.completed_at = datetime.now()
                return

            # 持久化结果
            self._save_result(task_id, result)

            with self._lock:
                task.result = result
                task.status = "completed"
                task.completed_at = datetime.now()

        except Exception as e:
            with self._lock:
                task.status = "failed"
                task.error = str(e)
                task.completed_at = datetime.now()
            import traceback
            traceback.print_exc()

    def _delete_saved_result(self, task_id: str):
        """删除指定 task_id 的已保存结果文件"""
        try:
            save_dir = Path(__file__).resolve().parent.parent / "选股" / "回测结果"
            if not save_dir.exists():
                return
            for f in save_dir.glob(f"*_{task_id}_*.json"):
                f.unlink(missing_ok=True)
        except Exception:
            pass

    def _save_result(self, task_id: str, result: dict):
        """将回测结果保存到文件"""
        try:
            save_dir = Path(__file__).resolve().parent.parent / "选股" / "回测结果"
            save_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{ts}_{task_id}_{result['strategy_name']}_{result['pool_name']}.json"
            filepath = save_dir / filename
            # 保存时排除大量交易明细（太大），只存摘要
            save_data = {
                "task_id": task_id,
                "strategy_name": result["strategy_name"],
                "pool_name": result["pool_name"],
                "date_range": result.get("date_range"),
                "total_rounds": result.get("total_rounds"),
                "total_trades": result.get("total_trades"),
                "metrics": result.get("metrics"),
                "config": result.get("config"),
                "elapsed": result.get("elapsed"),
                "saved_at": datetime.now().isoformat(),
            }
            filepath.write_text(
                json.dumps(save_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _get_task(self, task_id: str) -> BacktestTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def _cleanup_locked(self):
        now = datetime.now()
        expired = [
            tid for tid, t in self._tasks.items()
            if t.status in ("completed", "failed")
            and t.completed_at
            and (now - t.completed_at).total_seconds() > 3600
        ]
        for tid in expired:
            del self._tasks[tid]


import time

_manager = None


def get_backtest_manager() -> BacktestManager:
    global _manager
    if _manager is None:
        _manager = BacktestManager()
    return _manager
