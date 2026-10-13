@echo off
setlocal
set "PYTHONUTF8=1"
cd /d "%~dp0.."
set "PYTHON_EXE="
if defined DEEPSTOCK_PYTHON set "PYTHON_EXE=%DEEPSTOCK_PYTHON%"
if not defined PYTHON_EXE if exist D:\workspace\conda-envs\deepstock\python.exe set "PYTHON_EXE=D:\workspace\conda-envs\deepstock\python.exe"
if not defined PYTHON_EXE if exist C:\ProgramData\miniconda3\envs\deepstock\python.exe set "PYTHON_EXE=C:\ProgramData\miniconda3\envs\deepstock\python.exe"
if not exist "%PYTHON_EXE%" exit /b 1
"%PYTHON_EXE%" -m scripts.run_scheduled_task defensive-observation
exit /b %errorlevel%
