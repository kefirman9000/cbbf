@echo off
cd /d "%~dp0"
title CBBF
where py >nul 2>nul
if %errorlevel%==0 (py -3 main.py) else (python main.py)
if errorlevel 1 (
  echo.
  echo CBBF closed with an error. If you see nothing above, Python may not be installed.
  echo Get it from https://www.python.org/downloads/ and tick "Add Python to PATH".
  echo.
  pause
)
exit
