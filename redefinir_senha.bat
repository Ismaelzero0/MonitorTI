@echo off
cd /d "%~dp0"
call "%~dp0_python.cmd"
if errorlevel 1 (pause & exit /b 1)
%PYCMD% redefinir_senha.py
echo.
pause
