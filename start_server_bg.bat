@echo off
REM 波浪交易看板 - 后台静默启动（无窗口）
cd /d "E:\PyCharmprogram\AIFUND3"
start /B "" python -m uvicorn server.app:app --host 0.0.0.0 --port 8002
echo 服务器已后台启动
echo 地址: http://localhost:8002
echo.
