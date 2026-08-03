@echo off
REM Double-click this file to launch Brewlist and open it in your browser.
REM app.py itself finds a free port and opens the browser -- this is just a
REM convenient entry point. Closing this window (or the "Shut Down" button
REM in the page itself) stops the server.
cd /d "%~dp0"

where py >nul 2>nul
if %ERRORLEVEL%==0 (
    py app.py
    goto :end
)

where python >nul 2>nul
if %ERRORLEVEL%==0 (
    python app.py
    goto :end
)

where python3 >nul 2>nul
if %ERRORLEVEL%==0 (
    python3 app.py
    goto :end
)

echo Python was not found on your PATH. Install it from https://python.org/downloads/ and try again.

:end
pause
