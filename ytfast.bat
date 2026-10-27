@echo off
if "%~1"=="" (
  start "" pythonw "%~dp0ytfast_gui.pyw"
  exit /b
)
python -X utf8 "%~dp0ytfast.py" %*
