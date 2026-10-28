@echo off
rem Abre o Monitor TI como programa de bandeja (icone perto do relogio): iniciar, reiniciar, abrir painel, sair.
cd /d "%~dp0"
call "%~dp0_python.cmd"
if errorlevel 1 ( pause & exit /b 1 )
%PYCMD% -c "import pystray, PIL" >nul 2>&1
if errorlevel 1 (
    echo Instalando o componente da bandeja ^(pystray, pillow^)...
    %PYCMD% -m pip install pystray pillow
)
start "" %PYCMD% bandeja.py
exit
