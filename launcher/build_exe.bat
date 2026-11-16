@echo off
rem Builds SupoClip.exe (the one-click launcher) into the repo root. Needs: pip install pyinstaller pystray pillow
cd /d "%~dp0\.."
python -m PyInstaller --noconfirm --onefile --noconsole --name SupoClip --icon "%CD%\launcher\supoclip.ico" ^
  --distpath . --workpath launcher\build --specpath launcher\build launcher\supoclip_launcher.py
if errorlevel 1 (echo Build failed & exit /b 1)
echo Built %CD%\SupoClip.exe
