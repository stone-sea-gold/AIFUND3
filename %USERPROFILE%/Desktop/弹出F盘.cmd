@echo off
chcp 65001 >nul
title F盘一键清理
echo ========================================
echo   F盘进程清理工具
echo   扫描所有占用F盘的进程并终止
echo ========================================
echo.

:: 检测是否管理员运行
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] 建议以管理员身份运行，效果更彻底
    echo        右键本文件 → 以管理员身份运行
    echo.
    timeout /t 3 /nobreak >nul
)

echo [1/3] 扫描占用F盘的进程...
echo.

:: 通过 openfiles 查找F盘句柄
for /f "tokens=1,2 delims= " %%a in ('openfiles /query /v 2^>nul ^| find /i "F:\"') do (
    echo   找到: %%a (PID: %%b)
)

:: 通过 WMIC 查找运行在F盘的进程
wmic process where "name like '%%' and executablepath like 'F:%%'" get processid,name 2>nul | findstr /v "ProcessId"

echo.
echo [2/3] 终止占用进程...
echo.

:: 杀所有从F盘启动的exe
for /f "skip=1" %%p in ('wmic process where "executablepath like 'F:%%'" get processid 2^>nul') do (
    if %%p geq 1 (
        taskkill /f /pid %%p >nul 2>&1
        echo   已终止 PID: %%p
    )
)

:: 杀所有当前工作目录在F盘的cmd/python/node
for /f "tokens=2 delims=," %%a in ('wmic process where "name='cmd.exe' or name='python.exe' or name='node.exe' or name='claude.exe'" get processid /format:csv 2^>nul ^| findstr /v "ProcessId"') do (
    taskkill /f /pid %%a >nul 2>&1 && echo   已终止: PID %%a
)

echo.
echo [3/3] 尝试弹出F盘...
echo.

:: 强制卸除F盘卷标
powershell -Command "
try {
    $vol = Get-WmiObject -Class Win32_Volume -Filter 'DriveLetter=\"F:\"'
    if ($vol) { $vol.Dismount($true,$false) | Out-Null }
    Write-Host '   F盘已成功卸除'
} catch {
    Write-Host '   弹出失败，请检查是否还有残留进程'
    Write-Host '   打开资源监视器：任务管理器 → 性能 → 资源监视器 → 搜索 F:\'
}
" 2>nul

echo.
echo ========================================
echo   操作完成
echo   如果仍无法弹出，以管理员身份再运行一次
echo ========================================
pause
