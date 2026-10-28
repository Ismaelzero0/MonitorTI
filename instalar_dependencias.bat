@echo off
setlocal
cd /d "%~dp0"
title Instalando dependencias do Monitor TI
color 0E

call "%~dp0_python.cmd"
if errorlevel 1 (
    pause
    exit /b 1
)

echo ======================================================
echo   Instalando bibliotecas (requirements.txt)
echo ======================================================
echo.
%PYCMD% -m ensurepip --default-pip >nul 2>&1
%PYCMD% -m pip install --upgrade pip
%PYCMD% -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERRO] Falha na instalacao. Veja as mensagens acima.
    pause
    exit /b 1
)

echo.
echo Pronto! Agora execute iniciar.bat e abra http://127.0.0.1:5050
pause
