@echo off
REM Transcribe one audio or video file. Drag a file onto this script, or run:
REM   transcribe.bat "C:\path\to\recording.mp3"
setlocal
cd /d "%~dp0"
set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Links"
if "%~1"=="" (
  echo Drag an audio file onto transcribe.bat, or run: transcribe.bat "C:\path\to\file.mp3"
  pause & exit /b 1
)
if not exist .venv ( echo Run setup_windows.bat first. & pause & exit /b 1 )
call .venv\Scripts\activate.bat
if not defined WHISPER_MODEL set WHISPER_MODEL=base
set PYTHONIOENCODING=utf-8
echo Transcribing "%~1" with Whisper model "%WHISPER_MODEL%" (first run downloads the model)...
python cli.py "%~1" > "%~dpn1.transcript.json"
if errorlevel 1 ( echo Transcription failed. Copy the error above and send it to Claude. & pause & exit /b 1 )
python -c "import json,sys; from app.formats import to_srt; d=json.load(open(sys.argv[1],encoding='utf-8')); open(sys.argv[2],'w',encoding='utf-8').write(to_srt(d['segments']))" "%~dpn1.transcript.json" "%~dpn1.srt"
echo.
echo Done. Saved:
echo   %~dpn1.transcript.json
echo   %~dpn1.srt
echo.
type "%~dpn1.srt"
echo.
pause
