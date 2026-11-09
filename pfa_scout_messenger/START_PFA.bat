@echo off
setlocal
cd /d "%~dp0"
title PFA Scout Messenger

echo ==================================================
echo              PFA Scout Messenger
echo ==================================================
echo.

rem --- Refuse to run from inside the ZIP (Windows runs it from a temp folder) ---
echo "%~dp0" | find /i "\AppData\Local\Temp\" >nul
if not errorlevel 1 goto :fail_zip

rem --- 1. Find Python 3.10 or newer ---
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if defined PY goto :python_ok
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set "PY=python"
if not defined PY goto :fail_python
:python_ok

rem --- 2. Check that Google Chrome is installed (warning only) ---
set "CHROME_FOUND="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME_FOUND=1"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME_FOUND=1"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME_FOUND=1"
if not defined CHROME_FOUND echo [WARNING] Google Chrome was not found. Install it from https://www.google.com/chrome/
if not defined CHROME_FOUND echo.

rem --- 3. Private Python environment for this app (created on the first run) ---
if exist ".venv\Scripts\python.exe" goto :venv_ok
echo First run: preparing the environment. This can take a few minutes...
%PY% -m venv .venv
if errorlevel 1 goto :fail_venv
:venv_ok

echo Checking required libraries (the first run downloads them, please wait)...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto :fail_install

rem --- 4. Start the app and open it in the browser ---
echo.
echo Starting the app. The browser will open in a few seconds.
echo If it does not open, browse to: http://localhost:8501
echo.
echo KEEP THIS WINDOW OPEN while using the app. Close it to quit.
echo.
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 6; Start-Process 'http://localhost:8501'"
".venv\Scripts\python.exe" -m streamlit run app.py --server.headless true --browser.gatherUsageStats false
echo.
echo The app has stopped.
pause
exit /b 0

:fail_zip
echo [ERROR] The program is running from inside the ZIP file.
echo Right-click the ZIP file, choose "Extract All", and run START_PFA from the extracted folder.
pause
exit /b 1

:fail_python
echo [ERROR] Python 3.10 or newer was not found.
echo Install it from https://www.python.org/downloads/
echo During installation, check the box "Add python.exe to PATH".
echo Then run START_PFA again. See step 1 in PFA_Guide.pdf.
pause
exit /b 1

:fail_venv
echo [ERROR] Could not create the Python environment in this folder.
echo Try moving the folder to "Documents" and run START_PFA again.
pause
exit /b 1

:fail_install
echo [ERROR] Installing the required libraries failed.
echo Check the internet connection and run START_PFA again.
pause
exit /b 1
