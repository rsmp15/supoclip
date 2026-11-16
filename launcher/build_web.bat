@echo off
rem Production build of the website for SupoClip.exe (faster than the dev server). Re-run after updating the frontend.
rem Builds into frontend\.next-prod so a running dev server (frontend\.next) is not disturbed.
cd /d "%~dp0\..\frontend"
set NEXT_BUILD_DIR=.next-prod
set NEXT_TELEMETRY_DISABLED=1
call npx prisma generate
call npx next build
if errorlevel 1 (echo Website build failed & exit /b 1)
rem The standalone server needs the static files and public folder next to it.
for /f "delims=" %%S in ('dir /s /b ".next-prod\standalone\server.js"') do set SERVER=%%S
for %%D in ("%SERVER%") do set SDIR=%%~dpD
xcopy /e /i /y /q ".next-prod\static" "%SDIR%.next-prod\static" >nul
xcopy /e /i /y /q "public" "%SDIR%public" >nul
echo Website built: %SERVER%
