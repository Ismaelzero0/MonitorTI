# ==============================================================================
# INSTALADOR DO MONITOR TI  (Windows 10 / 11)
#
#   [1] Automático: instala o Python (verificando a assinatura digital), as bibliotecas e,
#                   se você quiser, configura o início automático com o Windows.
#   [2] Manual:     mostra o que falta e onde baixar; depois confere tudo para você.
#
# Execute com o botão direito > "Executar com o PowerShell". O modo automático pede administrador.
# ==============================================================================
#Requires -Version 5.1
param([ValidateSet('Automatico', 'Manual')][string]$Modo)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
$projectDir = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($projectDir)) { $projectDir = (Get-Location).Path }

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Find-Python {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        & $py.Source -3 --version *> $null
        if ($LASTEXITCODE -eq 0) { return @($py.Source, '-3') }
    }
    $cmd = Get-Command python.exe -ErrorAction SilentlyContinue
    # ignora o "atalho" da Microsoft Store (WindowsApps), que não é um Python de verdade
    if ($cmd -and $cmd.Source -notlike '*\WindowsApps\*') { return @($cmd.Source) }
    foreach ($p in @("$env:ProgramFiles\Python312\python.exe", "$env:LocalAppData\Programs\Python\Python312\python.exe")) {
        if (Test-Path -LiteralPath $p) { return @($p) }
    }
    return $null
}

function Invoke-Python([string[]]$python, [string[]]$argumentos) {
    $extra = @(); if ($python.Count -gt 1) { $extra = $python[1..($python.Count - 1)] }
    & $python[0] @extra @argumentos
}

function Test-Bibliotecas($python) {
    Invoke-Python $python @('-c', 'import flask, requests, paramiko, cryptography, wmi, pythoncom') *> $null
    return ($LASTEXITCODE -eq 0)
}

Write-Host '=== Instalador do Monitor TI ===' -ForegroundColor Cyan
Write-Host "Pasta: $projectDir`n"

if (-not $Modo) {
    Write-Host '  [1] Automático - instala tudo sozinho (recomendado)'
    Write-Host '  [2] Manual     - mostra o que falta e onde baixar'
    $r = Read-Host 'Escolha 1 ou 2'
    $Modo = if ($r -eq '2') { 'Manual' } else { 'Automatico' }
}

# ------------------------------------------------------------------ MANUAL
if ($Modo -eq 'Manual') {
    Write-Host "`nO que o Monitor TI precisa:" -ForegroundColor Yellow
    Write-Host '  1. Python 3.10 ou superior  -> https://www.python.org/downloads/  (marque "Add python.exe to PATH")'
    Write-Host '  2. Bibliotecas              -> depois do Python, rode: instalar_dependencias.bat'
    Write-Host '  3. (Opcional) PAExec        -> o assistente de configuração do sistema instala com verificação de assinatura,'
    Write-Host '                                 ou baixe em https://www.poweradmin.com/paexec/ e coloque em tools\paexec.exe'
    Write-Host '  4. (Opcional) DelProf2      -> https://helgeklein.com/free-tools/delprof2-user-profile-deletion-tool/ (tools\DelProf2.exe)'
    Read-Host "`nQuando terminar, pressione Enter para conferir"
    $python = Find-Python
    if (-not $python) { Write-Host 'Python ainda não encontrado.' -ForegroundColor Red; exit 1 }
    Write-Host "Python encontrado: $($python -join ' ')" -ForegroundColor Green
    if (Test-Bibliotecas $python) { Write-Host 'Bibliotecas OK.' -ForegroundColor Green }
    else { Write-Host 'Faltam bibliotecas: rode instalar_dependencias.bat' -ForegroundColor Yellow; exit 1 }
    Write-Host "`nTudo certo. Inicie com iniciar.bat e abra http://127.0.0.1:5050" -ForegroundColor Cyan
    exit 0
}

# ------------------------------------------------------------------ AUTOMÁTICO
if (-not (Test-Admin)) {
    Write-Host 'O modo automático precisa de administrador. Clique com o botão direito > Executar como administrador.' -ForegroundColor Red
    Read-Host 'Pressione Enter para sair'
    exit 1
}

Write-Host "[1/3] Python..." -ForegroundColor Yellow
$python = Find-Python
if (-not $python) {
    $url = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe'
    $installer = Join-Path $env:TEMP 'python-3.12.10-amd64.exe'
    Write-Host "  Baixando $url"
    Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing
    $sig = Get-AuthenticodeSignature -FilePath $installer
    if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notlike '*Python Software Foundation*') {
        Remove-Item $installer -Force -ErrorAction SilentlyContinue
        Write-Host '  ERRO: assinatura digital do instalador do Python inválida. Instalação cancelada.' -ForegroundColor Red
        exit 1
    }
    Write-Host '  Assinatura verificada (Python Software Foundation). Instalando...' -ForegroundColor Green
    $proc = Start-Process -FilePath $installer -ArgumentList '/quiet', 'InstallAllUsers=1', 'PrependPath=1', 'Include_test=0' -Wait -PassThru
    Remove-Item $installer -Force -ErrorAction SilentlyContinue
    if ($proc.ExitCode -ne 0) { Write-Host "  ERRO: instalador terminou com código $($proc.ExitCode)." -ForegroundColor Red; exit 1 }
    $python = Find-Python
    if (-not $python) { $python = @("$env:ProgramFiles\Python312\python.exe") }
}
Write-Host "  Python: $($python -join ' ')" -ForegroundColor Green

Write-Host "`n[2/3] Bibliotecas..." -ForegroundColor Yellow
Invoke-Python $python @('-m', 'ensurepip', '--default-pip') *> $null
Invoke-Python $python @('-m', 'pip', 'install', '--upgrade', 'pip')
Invoke-Python $python @('-m', 'pip', 'install', '-r', (Join-Path $projectDir 'requirements.txt'))
if ($LASTEXITCODE -ne 0) { Write-Host '  ERRO ao instalar as bibliotecas.' -ForegroundColor Red; exit 1 }
if (-not (Test-Bibliotecas $python)) { Write-Host '  ERRO: as bibliotecas não carregam após a instalação.' -ForegroundColor Red; exit 1 }
Write-Host '  Bibliotecas OK.' -ForegroundColor Green

Write-Host "`n[3/3] Início automático com o Windows" -ForegroundColor Yellow
Write-Host '  [1] Como tarefa agendada ao ligar o PC (recomendado para servidor)'
Write-Host '  [2] Programa de bandeja ao entrar no Windows'
Write-Host '  [3] Não, eu inicio manualmente'
$a = Read-Host 'Escolha 1, 2 ou 3'
if ($a -eq '1' -or $a -eq '2') {
    & (Join-Path $projectDir 'autostart.ps1') -Acao Instalar -Tipo $(if ($a -eq '1') { 'Tarefa' } else { 'Bandeja' })
}

Write-Host "`nPronto! Para abrir: iniciar.bat (ou bandeja.bat). Primeiro acesso: http://127.0.0.1:5050/setup" -ForegroundColor Cyan
Read-Host 'Pressione Enter para fechar'
