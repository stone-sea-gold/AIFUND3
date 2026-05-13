@echo off
REM 波浪交易看板启动脚本
cd /d "E:\PyCharmprogram\AIFUND3"
echo 启动波浪交易看板服务器...
echo 地址: http://0.0.0.0:8002
echo 局域网访问: http://192.168.31.239:8002
echo.
PYTHONIOENCODING=utf-8 python -m uvicorn server.app:app --host 0.0.0.0 --port 8002
pause
