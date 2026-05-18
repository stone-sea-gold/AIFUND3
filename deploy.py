#!/usr/bin/env python3
"""
波浪交易看板 - 部署管理工具（含进程保活）

用法:
    python deploy.py start          # 启动看护模式服务器（自动重启）
    python deploy.py stop           # 停止服务器
    python deploy.py restart        # 重启服务器
    python deploy.py status         # 查看运行状态
    python deploy.py tunnel         # 启动 ngrok 公网隧道
    python deploy.py logs           # 查看最近日志
    python deploy.py install-ngrok  # 安装 ngrok + 配置 token
"""
import sys, os, subprocess, time, signal, json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
HOST = "0.0.0.0"
PORT = 8002
PID_FILE = PROJECT_ROOT / ".server.pid"
WATCHDOG_PID_FILE = PROJECT_ROOT / ".watchdog.pid"
NGROK_PID_FILE = PROJECT_ROOT / ".ngrok.pid"
LOG_FILE = PROJECT_ROOT / "server.log"


def start():
    watchdog_pid = read_watchdog_pid()
    if watchdog_pid and _pid_alive(watchdog_pid):
        print(f"[WARN]   看护进程已在运行 (PID: {watchdog_pid})")
        print(f"   http://localhost:{PORT}")
        return

    print(f"[启动] 启动看护模式服务器...")
    print(f"   地址: http://{HOST}:{PORT}")
    print(f"   LAN: http://{_get_lan_ip()}:{PORT}")
    print(f"   日志: {LOG_FILE}")
    print(f"   看护: 服务器异常退出后自动重启")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.Popen(
        [sys.executable, "server/watchdog.py"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )

    time.sleep(3)

    if _pid_alive(proc.pid):
        print(f"[OK]  看护进程已启动 (PID: {proc.pid})")
        print(f"   http://localhost:{PORT}")
    else:
        print("[FAIL] 看护进程启动失败，请查看日志:")
        _print_log_tail()


def stop():
    # 先看护，再 uvicorn
    watchdog_pid = read_watchdog_pid()
    if watchdog_pid and _pid_alive(watchdog_pid):
        _kill_pid(watchdog_pid)
        print(f"[OK]  看护进程已停止 (PID: {watchdog_pid})")

    uvicorn_pid = read_pid()
    if uvicorn_pid and _pid_alive(uvicorn_pid):
        _kill_pid(uvicorn_pid)
        print(f"[OK]  服务器已停止 (PID: {uvicorn_pid})")

    if not watchdog_pid and not uvicorn_pid:
        print("[WARN]  服务器未运行")

    stop_ngrok()
    PID_FILE.unlink(missing_ok=True)
    WATCHDOG_PID_FILE.unlink(missing_ok=True)


def stop_ngrok():
    pid = read_ngrok_pid()
    if pid:
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
            else:
                os.kill(pid, signal.SIGTERM)
            print(f"[OK]  ngrok tunnel stopped")
        except:
            pass
        NGROK_PID_FILE.unlink(missing_ok=True)


def restart():
    stop()
    time.sleep(2)
    start()


def status():
    watchdog_pid = read_watchdog_pid()
    uvicorn_pid = read_pid()
    ngrok_pid = read_ngrok_pid()

    print(f"{'═' * 50}")
    print(f"  Wave Trading Dashboard - Status")
    print(f"{'═' * 50}")

    watchdog_ok = watchdog_pid and _pid_alive(watchdog_pid)
    uvicorn_ok = uvicorn_pid and _pid_alive(uvicorn_pid)

    if watchdog_ok:
        print(f"\n  [OK]  看护进程: Running (PID: {watchdog_pid})")
        print(f"     → 服务器异常后自动重启")
    else:
        print(f"\n  [INFO] 看护进程: 未启动")

    if uvicorn_ok:
        print(f"  [OK]  Web服务器: Running (PID: {uvicorn_pid})")
        print(f"     Local:   http://localhost:{PORT}")
        print(f"     LAN: http://{_get_lan_ip()}:{PORT}")
    else:
        print(f"  [FAIL] Web服务器: Not running")

    if ngrok_pid:
        print(f"  [OK]  ngrok隧道: Running (PID: {ngrok_pid})")
        ngrok_url = get_ngrok_url()
        if ngrok_url:
            print(f"     Public:   {ngrok_url}")
    else:
        print(f"  [INFO] ngrok隧道: Not running")

    print()

    # 日志文件大小
    if LOG_FILE.exists():
        size = LOG_FILE.stat().st_size
        print(f"  日志文件: {LOG_FILE} ({_fmt_size(size)})")


def logs(lines: int = 30):
    """查看最近 N 行日志"""
    if not LOG_FILE.exists():
        print("[WARN]  日志文件不存在")
        return
    content = LOG_FILE.read_text(encoding="utf-8").strip()
    if not content:
        print("[WARN]  日志为空")
        return
    log_lines = content.split("\n")
    for line in log_lines[-lines:]:
        print(line)


def tunnel():
    """启动ngrokPublic隧道"""
    ngrok_path = get_ngrok_path()
    if not ngrok_path:
        print("[FAIL]  ngrok not installed，Please run first: python deploy.py install-ngrok")
        return

    if not has_ngrok_token():
        print("[FAIL]  ngrok auth token not configured")
        print("   1. 访问 https://dashboard.ngrok.com/signup Sign up (免费)")
        print("   2. 获取token: https://dashboard.ngrok.com/get-started/your-authtoken")
        print(f"   3. 运行: \"{ngrok_path}\" config add-authtoken YOUR_TOKEN")
        return

    stop_ngrok()

    print("🚇 启动ngrokPublic隧道...")
    proc = subprocess.Popen(
        [ngrok_path, "http", str(PORT), "--log=stdout"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )

    write_ngrok_pid(proc.pid)
    time.sleep(3)

    url = get_ngrok_url()
    if url:
        print(f"[OK]  ngrok tunnel started successfully!")
        print(f"   Public地址: {url}")
    else:
        print("[WARN]   ngrok starting, check later...")


def get_ngrok_path():
    """查找ngrok可执行文件"""
    candidates = [
        Path.home() / "AppData/Local/ngrok/ngrok.exe",
        PROJECT_ROOT / "ngrok.exe",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    # Try PATH
    try:
        result = subprocess.run(["where", "ngrok"], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip().split("\n")[0]
    except:
        pass
    return None


def has_ngrok_token():
    try:
        ngrok_path = get_ngrok_path()
        if not ngrok_path:
            return False
        result = subprocess.run([ngrok_path, "config", "check"], capture_output=True, text=True)
        return "authtoken" in result.stdout.lower()
    except:
        return False


def get_ngrok_url():
    """从ngrok API获取PublicURL"""
    try:
        import urllib.request
        resp = urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=3)
        data = json.loads(resp.read())
        for t in data.get("tunnels", []):
            if t.get("public_url", "").startswith("https://"):
                return t["public_url"]
    except:
        pass
    return None


def install_ngrok():
    """安装并配置ngrok"""
    try:
        from pyngrok import ngrok
        ngrok.install_ngrok()
        print("[OK]  ngrok installed!")
        print(f"   路径: {get_ngrok_path()}")

        ngrok_path = get_ngrok_path()
        if ngrok_path:
            print()
            print("[TASK]  Next: configure Auth Token")
            print("   1. 访问 https://dashboard.ngrok.com/signup Sign up(免费)")
            print("   2. Login to get token: https://dashboard.ngrok.com/get-started/your-authtoken")
            print(f'   3. 运行: "{ngrok_path}" config add-authtoken YOUR_TOKEN')
            print('   4. 运行: python deploy.py tunnel')
    except Exception as e:
        print(f"[FAIL]  Install failed: {e}")


# ── PID 文件  ──────────────────────────────────────────────

def write_pid(pid):
    PID_FILE.write_text(str(pid))

def read_pid():
    try:
        return int(PID_FILE.read_text().strip())
    except:
        return None

def read_watchdog_pid():
    try:
        return int(WATCHDOG_PID_FILE.read_text().strip())
    except:
        return None

def write_ngrok_pid(pid):
    NGROK_PID_FILE.write_text(str(pid))

def read_ngrok_pid():
    try:
        return int(NGROK_PID_FILE.read_text().strip())
    except:
        return None

def is_running():
    """兼容旧版：检查是否有 uvicorn 在运行"""
    return read_pid() is not None and _pid_alive(read_pid())


# ── 系统级工具  ─────────────────────────────────────────────

def _get_lan_ip() -> str:
    """获取本机局域网 IP"""
    import re
    try:
        result = subprocess.run(['ipconfig'], capture_output=True, text=True, timeout=5)
        match = re.search(r'IPv4[^:]*:\s*(\d+\.\d+\.\d+\.\d+)', result.stdout)
        if match:
            return match.group(1)
    except:
        pass
    return "127.0.0.1"


def _pid_alive(pid: int) -> bool:
    """检查 PID 是否存在"""
    if not pid:
        return False
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True, text=True, timeout=5,
            )
            return str(pid) in result.stdout
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


def _kill_pid(pid: int):
    """强制杀进程"""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=5)
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def _fmt_size(size: int) -> str:
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def _print_log_tail(n: int = 6):
    """打印日志最后几行"""
    if LOG_FILE.exists():
        lines = LOG_FILE.read_text(encoding="utf-8").strip().split("\n")
        for line in lines[-n:]:
            print(f"  {line}")


# ── 入口  ──────────────────────────────────────────────────

def print_help():
    print(__doc__)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print_help()
        sys.exit(1)

    cmd = sys.argv[1]

    # logs [行数]
    if cmd == "logs":
        n = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 30
        logs(n)
        sys.exit(0)

    dispatch = {
        "start": start,
        "stop": stop,
        "restart": restart,
        "status": status,
        "tunnel": tunnel,
        "install-ngrok": install_ngrok,
    }
    fn = dispatch.get(cmd, print_help)
    fn()
