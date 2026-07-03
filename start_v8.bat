@echo off
TITLE TRADER SINGH v8.0: INSTITUTIONAL COMMAND CENTER
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
chcp 65001 >nul 2>&1

echo ============================================================
echo      TRADER SINGH v8.0 - NATIVE MICROSERVICES
echo ============================================================
echo.

echo Cleaning up any existing Python and Node processes to prevent duplicates...
taskkill /F /IM python.exe /T >nul 2>&1
taskkill /F /IM node.exe /T >nul 2>&1
timeout /t 2 /nobreak >nul
echo Cleanup complete. Starting fresh session...
echo.

echo [1/3] Starting WSL Infrastructure (PostgreSQL + Redis)...
:: Starts WSL services in a minimized window
start /min "WSL Services" wsl -d Ubuntu -u root bash /mnt/c/Users/ST/Desktop/Agentic_Trader/wsl_services.sh

:: Wait a few seconds for DB and Redis to accept connections
echo Waiting for databases to initialize...
timeout /t 5 /nobreak >nul

echo.
echo [2/3] Launching Independent Python Microservices...
:: Launch Core API
start /min "Core API" .\venv\Scripts\python.exe run_api.py
timeout /t 2 /nobreak >nul

:: Launch V8 Microservices (Each gets its own minimized process)
start /min "System Control" .\venv\Scripts\python.exe run_system_control.py
start /min "Data Harvester" .\venv\Scripts\python.exe run_harvester.py
start /min "Quant Engine" .\venv\Scripts\python.exe run_quant.py
start /min "Risk Committee" .\venv\Scripts\python.exe run_risk_committee.py
start /min "Execution OMS" .\venv\Scripts\python.exe run_oms.py
echo Microservices launched successfully.

echo.
echo [3/3] Starting Frontend Dashboard...
cd frontend
start /min "React UI" npm run dev -- --host
timeout /t 5 /nobreak >nul
start http://localhost:5173
cd ..

echo.
echo ============================================================
echo                 V8 SYSTEM LAUNCHED
echo ============================================================
timeout /t 3 /nobreak >nul
.\venv\Scripts\python.exe check_health.py
echo.
echo  Dashboard:    http://localhost:5173
echo  API Gateway:  http://localhost:8000
echo.
echo  Note: Microservices are running in minimized background windows.
echo  If you need to view raw terminal logs, check the minimized windows.
echo.
echo  To STOP the entire system, run this in your terminal:
echo  taskkill /F /IM python.exe /T ^& taskkill /F /IM node.exe /T
echo ============================================================
pause
