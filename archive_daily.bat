@echo off
REM ---------------------------------------------------------------------------
REM Daily intraday archive — the standing obligation of Amendment E10.
REM Invoked by Windows Task Scheduler task "AgenticTrader-IntradayArchive".
REM
REM Why a wrapper and not the .py directly: Task Scheduler gives no console, so
REM without a log every failure is silent — and a silent failure here loses
REM option history permanently at the next expiry. This tees everything to a
REM dated log and leaves a one-line STATUS.txt that can be read at a glance.
REM
REM The Dhan token lives 24 hours. Most scheduled runs will abort with exit 2
REM until DHAN_ACCESS_TOKEN in .env is refreshed. That is expected and is the
REM whole reason the outcome is written where it can be seen.
REM ---------------------------------------------------------------------------
setlocal
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0"

if not exist "logs\archive" mkdir "logs\archive"

REM --- single-instance lock ---------------------------------------------------
REM A board run takes ~11 minutes. Without this, a scheduled fire that lands on
REM a manual run has both instances appending to the same log and hammering the
REM same rate-limited API — observed on 2026-08-14, where two concurrent runs
REM produced a stream of "process cannot access the file" and neither finished
REM cleanly. mkdir is atomic on NTFS, so it is the lock.
set LOCK=logs\archive\.run.lock
mkdir "%LOCK%" 2>nul
if errorlevel 1 (
  for /f "tokens=2 delims==" %%K in ('wmic os get localdatetime /value') do set LB=%%K
  echo SKIPPED - another archive run is already in progress ^(%LOCK% exists^). >> "logs\archive\skipped.log"
  echo An archive run is already in progress. Nothing started.
  echo If no run is actually active, remove %LOCK% and retry.
  endlocal & exit /b 3
)

for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set LDT=%%I
set STAMP=%LDT:~0,4%-%LDT:~4,2%-%LDT:~6,2%
set NOW=%LDT:~0,4%-%LDT:~4,2%-%LDT:~6,2% %LDT:~8,2%:%LDT:~10,2%:%LDT:~12,2%
set LOG=logs\archive\archive_%STAMP%.log

echo. >> "%LOG%"
echo ============================================================ >> "%LOG%"
echo RUN STARTED %NOW% >> "%LOG%"
echo ============================================================ >> "%LOG%"

".\venv\Scripts\python.exe" archive_daily.py --band 15 --expiries 4 >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%

REM Re-read the clock: a board run takes ~11 minutes, so reusing the start
REM timestamp here would date every completion to when it began.
for /f "tokens=2 delims==" %%J in ('wmic os get localdatetime /value') do set LDT2=%%J
set DONE=%LDT2:~0,4%-%LDT2:~4,2%-%LDT2:~6,2% %LDT2:~8,2%:%LDT2:~10,2%:%LDT2:~12,2%

echo. >> "%LOG%"
echo RUN FINISHED %DONE% (started %NOW%) with exit code %RC% >> "%LOG%"

if "%RC%"=="0" (
  echo %DONE%  OK  archive completed        ^(log: %LOG%^) > "logs\archive\STATUS.txt"
) else if "%RC%"=="2" (
  echo %DONE%  TOKEN EXPIRED - NOTHING ARCHIVED. Refresh DHAN_ACCESS_TOKEN in .env, then run archive_daily.bat manually.  ^(log: %LOG%^) > "logs\archive\STATUS.txt"
) else (
  echo %DONE%  FAILED exit=%RC% - check %LOG% > "logs\archive\STATUS.txt"
)

rmdir "%LOCK%" 2>nul

endlocal & exit /b %RC%
