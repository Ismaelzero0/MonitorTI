@echo off
rem Inicia o monitor em segundo plano (janela minimizada), gravando a saida em data\monitor.log.
cd /d "%~dp0"
start "MonitorTI" /min cmd /c ""%~dp0iniciar.bat" /silent"
exit
