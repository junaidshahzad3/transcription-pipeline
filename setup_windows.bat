@echo off
REM One-time setup on Windows: checks Python, installs FFmpeg, creates a virtual env,
REM installs the requirements and runs the tests.
setlocal
cd /d "%~dp0"
echo.
echo === Transcription pipeline setup ===
echo.

REM ---- Python ----
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY ( where python >nul 2>nul && set PY=python )
if not defined PY (
  echo Python was not found. Install Python 3.10 or newer from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during install, then run this again.
  pause & exit /b 1
)
%PY% -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)"
if errorlevel 1 (
  echo Your Python is older than 3.10. Please install Python 3.10 or newer, then run this again.
  pause & exit /b 1
)
%PY% --version

REM ---- FFmpeg ----
set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Links"
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo FFmpeg not found. Installing it with winget...
  winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
  set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Links"
)
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo FFmpeg is still not on PATH. Close this window, open a new one and run setup_windows.bat again.
  pause & exit /b 1
)
ffmpeg -version | findstr /b "ffmpeg version"

REM ---- Virtual env and packages ----
if not exist .venv (
  echo Creating virtual environment...
  %PY% -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip -q
echo Installing packages (this can take a few minutes)...
pip install -r requirements.txt
if errorlevel 1 ( echo Package install failed. Copy the error above and send it to Claude. & pause & exit /b 1 )

REM ---- Tests ----
echo.
echo === Running tests ===
python -m pytest -q
echo.
echo Setup finished. To transcribe a file, drag an audio file onto transcribe.bat
echo or run:  transcribe.bat "C:\path\to\recording.mp3"
echo.
pause
