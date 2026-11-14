@echo off
setlocal
cd /d "%~dp0"
title PFA Scout Messenger

rem ============================================================
rem  PFA Scout Messenger - Windows launcher
rem  Python and all libraries are installed automatically into
rem  %LOCALAPPDATA%\PFA_Scout_Messenger (a local, non-synced folder).
rem  Nothing has to be installed by hand (except Google Chrome).
rem  "START_PFA.bat --check" = set up and self-test only (used by CI).
rem ============================================================

set "APP_DIR=%~dp0"
set "UV=%APP_DIR%bin\uv.exe"
set "RUNTIME=%LOCALAPPDATA%\PFA_Scout_Messenger\runtime"
set "VENV=%RUNTIME%\venv"
set "PY=%VENV%\Scripts\python.exe"
set "UV_PYTHON_INSTALL_DIR=%RUNTIME%\python"
set "UV_CACHE_DIR=%RUNTIME%\cache"
set "UV_PYTHON_PREFERENCE=only-managed"
set "SYNC_FLAGS="
set "REPAIRED="
set "CHECK_ONLY="
if /i "%~1"=="--check" set "CHECK_ONLY=1"

echo ==================================================
echo              PFA Scout Messenger
echo ==================================================
echo.

rem --- Refuse to run from inside the ZIP (Windows runs it from a temp folder) ---
echo "%~dp0" | find /i "\AppData\Local\Temp\" >nul
if not errorlevel 1 goto :fail_zip
if not exist "%UV%" goto :fail_files
if not exist "%APP_DIR%app.py" goto :fail_files

rem --- Google Chrome (warning only) ---
set "CHROME_FOUND="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME_FOUND=1"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME_FOUND=1"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME_FOUND=1"
if not defined CHROME_FOUND echo [WARNING] Google Chrome was not found. Install it from https://www.google.com/chrome/
if not defined CHROME_FOUND echo.

:setup
if exist "%PY%" goto :sync
echo Preparing the program. The first time this downloads about 150 MB
echo and takes a few minutes. Please wait and do not close this window...
echo.
if not exist "%RUNTIME%" mkdir "%RUNTIME%"
"%UV%" venv "%VENV%" --python 3.13 --clear
if errorlevel 1 goto :repair

:sync
rem Install exactly the tested library versions (fast when already installed)
"%UV%" pip sync "%APP_DIR%requirements.lock" --python "%PY%" --no-build %SYNC_FLAGS%
if errorlevel 1 goto :repair

rem Self-test: every library must load. A broken install is rebuilt once automatically.
"%PY%" -c "import pandas, streamlit, selenium, openpyxl"
if errorlevel 1 goto :repair
goto :ready

:repair
if defined REPAIRED goto :fail_setup
set "REPAIRED=1"
set "SYNC_FLAGS=--reinstall --refresh"
echo.
echo Something is wrong with the program files. Rebuilding them, please wait...
echo.
if exist "%VENV%" rmdir /s /q "%VENV%"
goto :setup

:ready
if defined CHECK_ONLY goto :check_done

echo.
echo Starting the app. The browser will open by itself in a few seconds.
echo If it does not open, browse to: http://localhost:8501
echo.
echo KEEP THIS WINDOW OPEN while using the app. Close it to quit.
echo.
start "" powershell -NoProfile -WindowStyle Hidden -Command "$u='http://localhost:8501'; for ($i=0; $i -lt 90; $i++) { try { Invoke-WebRequest ($u + '/_stcore/health') -UseBasicParsing -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep -Seconds 1 } }; Start-Process $u"
"%PY%" -m streamlit run "%APP_DIR%app.py" --server.headless true --server.port 8501 --browser.gatherUsageStats false
echo.
echo The app has stopped.
pause
exit /b 0

:check_done
"%PY%" -c "import sys, app; print('Self-test passed: Python', sys.version.split()[0])"
if errorlevel 1 goto :fail_setup
exit /b 0

:fail_zip
echo [ERROR] The program is running from inside the ZIP file.
echo Right-click the ZIP file, choose "Extract All", and run START_PFA from the extracted folder.
goto :fail_end

:fail_files
echo [ERROR] Program files are missing (bin\uv.exe or app.py).
echo Extract the whole ZIP file again with "Extract All", and run START_PFA from the extracted folder.
goto :fail_end

:fail_setup
echo.
echo [ERROR] Setting up the program failed.
echo 1. Check the internet connection (the first run downloads files).
echo 2. Run START_PFA again.
echo 3. If it still fails, send a screenshot of this window to the person who maintains the program.
goto :fail_end

:fail_end
if not defined CHECK_ONLY pause
exit /b 1
