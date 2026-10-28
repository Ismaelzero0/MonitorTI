@echo off
rem Reinicia o monitor (o iniciar.bat ja encerra a instancia anterior pela porta).
cd /d "%~dp0"
call "%~dp0iniciar.bat" %*
