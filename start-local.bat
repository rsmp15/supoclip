@echo off
rem SupoClip native Windows launcher (local single-user mode): API + worker + frontend, opens the Live page.
rem Needs PostgreSQL and Redis already running (ports 5432 / 6379), backend\.venv and frontend\node_modules installed.
cd /d "%~dp0"

netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul || start "SupoClip API" /min cmd /c "cd backend && .venv\Scripts\python.exe -m uvicorn src.main:app --host 127.0.0.1 --port 8000"
tasklist /V /FI "WINDOWTITLE eq SupoClip worker*" | findstr /I "cmd.exe" >nul || start "SupoClip worker" /min cmd /c "cd backend && .venv\Scripts\python.exe -m src.workers.run_workers"
netstat -ano | findstr /R /C:":3107 .*LISTENING" >nul || start "SupoClip frontend" /min cmd /c "cd frontend && pnpm dev"

echo Waiting for SupoClip to come up...
:wait
timeout /t 3 >nul
netstat -ano | findstr /R /C:":3107 .*LISTENING" >nul || goto wait

set EDGE="%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if exist %EDGE% (start "" %EDGE% --app=http://localhost:3107/live --window-size=1440,920) else (start http://localhost:3107/live)
echo SupoClip is running. Close the "SupoClip API / worker / frontend" windows to stop it.
