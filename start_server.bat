@echo off
REM 波浪交易看板启动脚本
cd /d "E:\PyCharmprogram\AIFUND3"
for /f "tokens=3 delims=: " %%i in ('netsh interface ip show address ^| find "IP Address" ^| find "10."') do set LAN_IP=%%i
if "%LAN_IP%"=="" for /f "tokens=3 delims=: " %%i in ('netsh interface ip show address ^| find "IP Address"') do set LAN_IP=%%i
echo 启动波浪交易看板服务器...
echo 地址: http://0.0.0.0:8002
echo 局域网访问: http://%LAN_IP%:8002
echo.
PYTHONIOENCODING=utf-8 python -m uvicorn server.app:app --host 0.0.0.0 --port 8002
pause
