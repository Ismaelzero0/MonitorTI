@echo off
rem Localiza o Python 3 (Windows 10/11) e define a variavel PYCMD. Usado pelos outros .bat.
rem Nao usa setlocal de proposito: a variavel precisa voltar para quem chamou.
set "PYCMD="
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD (
    python --version >nul 2>&1 && set "PYCMD=python"
)
if not defined PYCMD if exist "%ProgramFiles%\Python312\python.exe" set PYCMD="%ProgramFiles%\Python312\python.exe"
if not defined PYCMD if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set PYCMD="%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PYCMD (
    echo [ERRO] Python 3 nao encontrado. Instale em https://www.python.org/downloads/ ^(marque "Add python.exe to PATH"^)
    echo        ou execute instalartudo.ps1 como administrador.
    exit /b 1
)
exit /b 0
