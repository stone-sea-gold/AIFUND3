"""
回测任务管理器 — 后台线程执行回测，支持进度追踪和结果持久化

用法:
    manager = get_backtest_manager()
    task_id = manager.submit(strategy="b1", pool="沪深300", top_n=10, holding_days=5)
    task = manager.get_task(task_id)
"""

import json
import logging
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("backtest_manager")

_TASKS_DIR = Path(__file__).resolve().parent.parent / "选股" / "回测任务"


class BacktestCancelled(BaseException):
    """回测被用户取消（继承 BaseException 避免被 except Exception 吞掉）"""
    pass


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

    def to_dict(self) -> dict:
        """序列化为可持久化的字典（不含 result 和线程对象）"""
        return {
            "task_id": self.task_id,
            "strategy": self.strategy,
            "pool": self.pool,
            "top_n": self.top_n,
            "min_score": self.min_score,
            "holding_days": self.holding_days,
            "initial_capital": self.initial_capital,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "status": self.status,
            "progress": dict(self.progress),
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BacktestTask":
        """从持久化字典恢复任务（不含 result，需要从结果文件加载）"""
        task = cls(
            task_id=d["task_id"],
            strategy=d["strategy"],
            pool=d["pool"],
            top_n=d["top_n"],
            min_score=d["min_score"],
            holding_days=d["holding_days"],
            initial_capital=d["initial_capital"],
            start_date=d.get("start_date"),
            end_date=d.get("end_date"),
        )
        task.status = d.get("status", "completed")
        task.progress = d.get("progress", {})
        task.error = d.get("error")
        task.created_at = datetime.fromisoformat(d["created_at"]) if d.get("created_at") else datetime.now()
        if d.get("completed_at"):
            task.completed_at = datetime.fromisoformat(d["completed_at"])
        return task


class BacktestManager:
    """回测任务管理器（线程安全）"""

    def __init__(self):
        self._tasks: dict[str, BacktestTask] = {}
        self._lock = threading.Lock()
        self._max_tasks = 20
        self._load_persisted_tasks()

    def submit(self, strategy: str | None = None, pool: str | None = None,
               top_n: int | None = None, min_score: int | None = None,
               holding_days: int | None = None, initial_capital: float | None = None,
               start_date: str | None = None, end_date: str | None = None) -> str:
        """提交回测任务，返回 task_id（参数为空时取控制面板默认值）"""
        from server.settings import get_settings
        s = get_settings()
        if strategy is None:
            strategy = s.get("backtest", "strategy")
        if pool is None:
            pool = s.get("backtest", "pool")
        if top_n is None:
            top_n = s.get("backtest", "top_n")
        if min_score is None:
            min_score = s.get("backtest", "min_score")
        if holding_days is None:
            holding_days = s.get("backtest", "holding_days")
        if initial_capital is None:
            initial_capital = s.get("backtest", "initial_capital")

        task_id = uuid.uuid4().hex[:8]
        task = BacktestTask(task_id, strategy, pool, top_n, min_score,
                            holding_days, initial_capital,
                            start_date, end_date)
        # 从控制面板读取止损参数
        task.stop_loss_params = {
            "stop_loss_pct": s.get("backtest", "stop_loss_pct"),
            "gap_up_pct": s.get("backtest", "gap_up_pct"),
            "holding_days": holding_days,
        }

        with self._lock:
            self._tasks[task_id] = task
            self._cleanup_locked()
        self._persist_task(task)

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
                    "initial_capital": t.initial_capital,
                    "start_date": t.start_date,
                    "end_date": t.end_date,
                    "created_at": t.created_at.isoformat(),
                    "completed_at": t.completed_at.isoformat() if t.completed_at else None,
                    "progress": dict(t.progress),
                    "error": t.error,
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
            if task is None or task.status not in ("completed", "failed", "cancelled"):
                return False
            del self._tasks[task_id]
        self._delete_persisted_task(task_id)
        return True

    def stop_task(self, task_id: str) -> bool:
        """停止正在运行的回测任务，删除已保存的结果文件"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
        if task.status != "running":
            return False
        task.cancel()
        self._delete_saved_result(task_id)
        self._persist_task(task)
        return True

    def _run_backtest(self, task_id: str):
        task = self._get_task(task_id)
        if not task:
            return

        try:
            task.status = "running"
            task._started_at = time.time()
            self._persist_task(task)

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
                verbose=True,
                cancelled=task._cancelled,
                progress_callback=_on_progress,
            )

            self._save_result(task_id, result)

            with self._lock:
                task.result = result
                task.status = "completed"
                task.completed_at = datetime.now()
            self._persist_task(task)

            # 打印结果摘要
            m = result.get("metrics", {})
            logger.info(f"任务 {task_id} 完成: 日期范围={result.get('date_range')}, 交易={m.get('total_trades', 0)}笔, 收益={m.get('total_return_pct', 0):.2f}%, 耗时={result.get('elapsed', 0):.1f}s")

        except BacktestCancelled:
            with self._lock:
                task.status = "cancelled"
                task.completed_at = datetime.now()
            self._persist_task(task)
            self._delete_saved_result(task_id)

        except Exception as e:
            with self._lock:
                task.status = "failed"
                task.error = str(e)
                task.completed_at = datetime.now()
            self._persist_task(task)
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
        """将回测结果保存到文件（包含完整交易明细和净值曲线）"""
        try:
            save_dir = Path(__file__).resolve().parent.parent / "选股" / "回测结果"
            save_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{ts}_{task_id}_{result['strategy_name']}_{result['pool_name']}.json"
            filepath = save_dir / filename
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
                "trades": result.get("trades", []),
                "nav_history": result.get("nav_history", []),
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

    # ── 持久化 ──

    def _persist_task(self, task: BacktestTask):
        """将任务元数据写入磁盘（不包含 result，结果由 _save_result 单独管理）"""
        try:
            _TASKS_DIR.mkdir(parents=True, exist_ok=True)
            filepath = _TASKS_DIR / f"{task.task_id}.json"
            filepath.write_text(
                json.dumps(task.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _delete_persisted_task(self, task_id: str):
        """删除持久化的任务文件"""
        try:
            filepath = _TASKS_DIR / f"{task_id}.json"
            if filepath.exists():
                filepath.unlink()
        except Exception:
            pass

    def _load_persisted_tasks(self):
        """启动时从磁盘加载任务记录，优先读任务文件，再从结果文件补充"""
        # 1. 加载任务元数据文件
        if _TASKS_DIR.exists():
            for f in _TASKS_DIR.glob("*.json"):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    task = BacktestTask.from_dict(data)
                    if task.status == "running":
                        task.status = "failed"
                        task.error = "服务器重启，任务中断"
                        task.completed_at = datetime.now()
                        self._persist_task(task)
                    self._tasks[task.task_id] = task
                except Exception:
                    continue

        # 2. 从结果文件补充历史记录（兼容旧数据）
        results_dir = Path(__file__).resolve().parent.parent / "选股" / "回测结果"
        if results_dir.exists():
            for f in sorted(results_dir.glob("*.json"), reverse=True):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    tid = data.get("task_id", "")
                    if tid and tid not in self._tasks:
                        task = BacktestTask(
                            task_id=tid,
                            strategy=data.get("strategy_name", "unknown"),
                            pool=data.get("pool_name", "unknown"),
                            top_n=data.get("config", {}).get("top_n", 10),
                            min_score=data.get("config", {}).get("min_score", 25),
                            holding_days=data.get("config", {}).get("holding_days", 3),
                            initial_capital=data.get("config", {}).get("initial_capital", 100000),
                        )
                        task.status = "completed"
                        task.progress = {"elapsed": data.get("elapsed", 0)}
                        task.result = data
                        if data.get("saved_at"):
                            try:
                                task.completed_at = datetime.fromisoformat(data["saved_at"])
                                task.created_at = task.completed_at
                            except Exception:
                                pass
                        self._tasks[task.task_id] = task
                except Exception:
                    continue

        # 清理过期任务
        self._cleanup_locked()


_manager = None


def get_backtest_manager() -> BacktestManager:
    global _manager
    if _manager is None:
        _manager = BacktestManager()
    return _manager
