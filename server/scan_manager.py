"""
后台扫描任务管理器

管理选股扫描的生命周期：
  提交 → 后台线程执行 → 进度追踪 → 结果获取

用法:
    manager = ScanManager()
    task_id = manager.submit_scan(pool_name="沪深300", strategy_name="b1")
    # 轮询:
    task = manager.get_task(task_id)
    task.status        # "pending" | "running" | "completed" | "failed"
    task.progress      # {"scanned": N, "total": N, "passed": N, "current_stock": "...", ...}
    task.results       # list[dict] — 扫描完成后的结果
"""

import os
import sys
import time
import uuid
import threading
from datetime import datetime
from pathlib import Path

# ── 路径处理：确保能从 server/ 下导入 选股/ 模块 ──
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


class ScanCancelled(Exception):
    """扫描被用户主动取消"""
    pass


class ScanTask:
    """单个扫描任务的状态容器"""

    def __init__(self, task_id: str, pool_name: str, strategy_name: str,
                 top_n: int, min_score: int, delay: float, workers: int):
        self.task_id = task_id
        self.pool_name = pool_name
        self.strategy_name = strategy_name
        self.top_n = top_n
        self.min_score = min_score
        self.delay = delay
        self.workers = workers

        self.status = "pending"  # pending → running → completed | failed | cancelled
        self.progress = {
            "scanned": 0,
            "total": 0,
            "passed": 0,
            "current_stock": "",
            "elapsed": 0,
            "eta": 0,
        }
        self.results = None
        self.error = None
        self.created_at = datetime.now()
        self.completed_at = None
        self._started_at = None
        self._cancelled = threading.Event()

    def cancel(self):
        """标记任务为已取消"""
        self._cancelled.set()


class ScanManager:
    """扫描任务管理器（线程安全）"""

    def __init__(self):
        self._tasks: dict[str, ScanTask] = {}
        self._lock = threading.Lock()
        self._max_tasks = 20

    # ── 公共 API ──────────────────────────────────────────────

    def submit_scan(
        self,
        pool_name: str = "沪深300",
        strategy_name: str = "b1",
        top_n: int = 30,
        min_score: int = 25,
        delay: float = 0.15,
        workers: int = 4,
    ) -> str:
        """提交扫描任务，返回 task_id"""
        task_id = uuid.uuid4().hex[:8]

        task = ScanTask(task_id, pool_name, strategy_name,
                        top_n, min_score, delay, workers)

        with self._lock:
            self._tasks[task_id] = task
            self._cleanup_locked()

        # 启动后台线程
        t = threading.Thread(target=self._run_scan, args=(task_id,), daemon=True)
        t.start()

        return task_id

    def get_task(self, task_id: str) -> ScanTask | None:
        """获取任务状态/结果"""
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self) -> list[dict]:
        """列出所有任务摘要"""
        with self._lock:
            return [
                {
                    "task_id": t.task_id,
                    "status": t.status,
                    "pool_name": t.pool_name,
                    "strategy_name": t.strategy_name,
                    "progress": dict(t.progress),
                    "created_at": t.created_at.isoformat(),
                }
                for t in sorted(
                    self._tasks.values(),
                    key=lambda x: x.created_at,
                    reverse=True,
                )[:self._max_tasks]
            ]

    def stop_task(self, task_id: str) -> bool:
        """停止正在运行的扫描任务"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
        if task.status != "running":
            return False
        task.cancel()
        return True

    def delete_task(self, task_id: str) -> bool:
        """删除已完成/已取消/已失败的任务"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.status in ("completed", "failed", "cancelled"):
                del self._tasks[task_id]
                return True
            return False

    # ── 内部 ──────────────────────────────────────────────────

    def _run_scan(self, task_id: str):
        """后台执行扫描（在独立线程中运行）"""
        task = self._get_task(task_id)
        if not task:
            return

        try:
            # 懒加载策略模块
            from 选股.strategy_loader import load_strategy
            strategy = load_strategy(task.strategy_name)

            from 选股.scanner import scan_all

            task.status = "running"
            task._started_at = time.time()

            # 进度回调（闭包捕获 task）
            def _on_progress(scanned, total, passed, current_stock):
                if task._cancelled.is_set():
                    raise ScanCancelled()
                elapsed = time.time() - task._started_at
                rate = scanned / elapsed if elapsed > 0 else 0
                eta = (total - scanned) / rate if rate > 0 else 0
                with self._lock:
                    task.progress.update(
                        scanned=scanned,
                        total=total,
                        passed=passed,
                        current_stock=current_stock,
                        elapsed=round(elapsed, 1),
                        eta=round(eta, 0),
                    )

            results = scan_all(
                pool_name=task.pool_name,
                top_n=task.top_n,
                min_score=task.min_score,
                delay=task.delay,
                workers=task.workers,
                verbose=False,
                strategy=strategy,
                progress_callback=_on_progress,
            )

            # 修剪结果（移除 klines 大数据）
            trimmed = []
            for r in results:
                trimmed.append({
                    "code": r["code"],
                    "name": r["name"],
                    "score": r["score"],
                    "details": [
                        {
                            "criterion": d["criterion"],
                            "desc": d["desc"],
                            "score": d["score"],
                            "weight": d["weight"],
                            "detail": d["detail"],
                        }
                        for d in r["details"]
                    ],
                    "latest_info": dict(r["latest_info"]),
                    "indicators": dict(r.get("indicators", {})),
                    "industry": r.get("industry", ""),
                    "concepts": r.get("concepts", []),
                })

            with self._lock:
                task.results = trimmed
                task.status = "completed"
                task.completed_at = datetime.now()

            # ── 持久化到选股跟踪看板 ──
            try:
                from server.tracker import get_tracker
                tracker = get_tracker()
                _strat_name = getattr(strategy, "STRATEGY_NAME", task.strategy_name)
                print(f"[tracker] 保存扫描结果: task={task_id}, strategy={task.strategy_name}, strategy_name={_strat_name}, stocks={len(trimmed)}")
                tracker.add_entry(
                    task_id=task_id,
                    scan_date=task.created_at.strftime("%Y-%m-%d"),
                    strategy=task.strategy_name,
                    strategy_name=_strat_name,
                    pool_name=task.pool_name,
                    top_n=task.top_n,
                    stocks=trimmed,
                )
                print(f"[tracker] 保存成功: task={task_id}")
            except Exception as e:
                print(f"[tracker] 保存失败: task={task_id}, error={e}")
                import traceback; traceback.print_exc()

        except ScanCancelled:
            with self._lock:
                task.status = "cancelled"
                task.completed_at = datetime.now()
        except Exception as e:
            with self._lock:
                task.status = "failed"
                task.error = str(e)
                task.completed_at = datetime.now()
            import traceback
            traceback.print_exc()

    def _get_task(self, task_id: str) -> ScanTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def _cleanup_locked(self):
        """清理过期任务（完成/失败超过30分钟）"""
        now = datetime.now()
        expired = [
            tid for tid, t in self._tasks.items()
            if t.status in ("completed", "failed", "cancelled")
            and t.completed_at
            and (now - t.completed_at).total_seconds() > 1800
        ]
        for tid in expired:
            del self._tasks[tid]

    def __len__(self):
        with self._lock:
            return len(self._tasks)


# 全局单例
_manager = ScanManager()


def get_manager() -> ScanManager:
    return _manager
