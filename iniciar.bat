@echo off
setlocal
cd /d "%~dp0"
title Monitor TI
color 0A

echo ======================================================
echo      MONITOR TI
echo ======================================================
echo.

call "%~dp0_python.cmd"
if errorlevel 1 (
    if /i not "%~1"=="/silent" pause
    exit /b 1
)

%PYCMD% -c "import flask, paramiko, cryptography, wmi" >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Dependencias do Python nao instaladas. Execute instalar_dependencias.bat primeiro.
    if /i not "%~1"=="/silent" pause
    exit /b 1
)

rem Porta: variavel MONITOR_PORT do ambiente ou do arquivo .env (padrao 5050)
if not defined MONITOR_PORT (
    if exist ".env" for /f "tokens=2 delims==" %%p in ('findstr /b /c:"MONITOR_PORT=" ".env"') do set "MONITOR_PORT=%%p"
)
if not defined MONITOR_PORT set "MONITOR_PORT=5050"

rem Encerra somente a instancia anterior DESTE monitor (o processo que escuta na porta),
rem sem derrubar outros programas Python da maquina.
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /R /C:":%MONITOR_PORT% .*LISTENING"') do taskkill /F /PID %%p >nul 2>&1

echo Iniciando o Monitor TI na porta %MONITOR_PORT%...
echo.

if /i "%~1"=="/silent" (
    if not exist "data" mkdir "data"
    %PYCMD% app.py >> "data\monitor.log" 2>&1
    exit /b %errorlevel%
)

%PYCMD% app.py

echo.
echo O monitor foi encerrado. Se houve erro, leia as mensagens acima.
pause
