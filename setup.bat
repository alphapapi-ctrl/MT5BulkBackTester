@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
where py >nul 2>nul
if errorlevel 1 goto python_fallback
py -3 -c "import sys; sys.exit(sys.version_info < (3,11))"
if errorlevel 1 goto missing_python
py -3 -m venv .venv
if errorlevel 1 goto failed
goto install
:python_fallback
python -c "import sys; sys.exit(sys.version_info < (3,11))"
if errorlevel 1 goto missing_python
python -m venv .venv
if errorlevel 1 goto failed
:install
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Setup complete. Double-click start.bat to launch the app.
pause
exit /b 0
:missing_python
echo Install Python 3.11 or newer from python.org, including the launcher or PATH option.
:failed
echo Setup failed. Review the error above.
pause
exit /b 1
