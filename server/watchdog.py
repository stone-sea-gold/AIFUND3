"""
进程看护 — 自动重启 uvicorn 服务器

启动方式:
    python server/watchdog.py

在 deploy.py 中的用法:
    python deploy.py start       # 启动看护（默认走看护模式）
    python deploy.py stop        # 停止服务器
"""

import subprocess
import sys
import time
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = PROJECT_ROOT / "server.log"
PID_FILE = PROJECT_ROOT / ".server.pid"
WATCHDOG_PID_FILE = PROJECT_ROOT / ".watchdog.pid"


def log(msg: str):
    """同时输出到终端和日志文件"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def main():
    watch_pid = os.getpid()
    WATCHDOG_PID_FILE.write_text(str(watch_pid))

    log("=" * 50)
    log(f"看护进程启动 (PID: {watch_pid})")
    log(f"日志文件: {LOG_FILE}")
    log("=" * 50)

    uvicorn_cmd = [
        sys.executable, "-m", "uvicorn",
        "server.app:app",
        "--host", "0.0.0.0",
        "--port", "8002",
    ]

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    restart_count = 0
    max_restarts = 20

    while True:
        log("正在启动 uvicorn 服务器...")

        log_file = open(LOG_FILE, "a", encoding="utf-8")
        proc = subprocess.Popen(
            uvicorn_cmd,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

        # 记录 uvicorn PID 供 deploy.py status/stop 使用
        PID_FILE.write_text(str(proc.pid))
        log(f"服务器已启动 (PID: {proc.pid})  http://localhost:8002")

        try:
            proc.wait()
        except KeyboardInterrupt:
            log("收到 Ctrl+C 中断信号，正在关闭服务器...")
            proc.terminate()
            proc.wait()
            break

        exit_code = proc.returncode
        log_file.close()
        log(f"服务器已退出 (PID: {proc.pid}, 退出码: {exit_code})")

        # 退出码 0 表示正常关闭，看护进程也退出
        if exit_code == 0:
            log("服务器正常关闭，看护进程退出")
            break

        # 非正常退出 → 自动重启（带指数退避）
        restart_count += 1
        if restart_count > max_restarts:
            log(f"已达到最大重启次数 ({max_restarts})，看护进程退出")
            break

        delay = min(3 * restart_count, 30)  # 3s, 6s, 9s ... 最长 30s
        log(f"服务器异常退出，{delay} 秒后自动重启 (第 {restart_count}/{max_restarts} 次)")
        for remaining in range(delay, 0, -1):
            time.sleep(1)

    # 清理 PID 文件
    PID_FILE.unlink(missing_ok=True)
    WATCHDOG_PID_FILE.unlink(missing_ok=True)
    log("看护进程退出")


if __name__ == "__main__":
    main()
