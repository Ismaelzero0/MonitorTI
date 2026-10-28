# ==============================================================================
# Início automático do Monitor TI.
#   .\autostart.ps1 -Acao Instalar -Tipo Tarefa    -> tarefa agendada ao ligar o PC (sem precisar entrar no Windows)
#   .\autostart.ps1 -Acao Instalar -Tipo Bandeja   -> programa de bandeja ao entrar no Windows
#   .\autostart.ps1 -Acao Remover                  -> remove tarefa, atalho e regra de firewall
# Execute como administrador (a tarefa agendada e o firewall exigem).
# ==============================================================================
#Requires -Version 5.1
param(
    [ValidateSet('Instalar', 'Remover')][string]$Acao = 'Instalar',
    [ValidateSet('Tarefa', 'Bandeja')][string]$Tipo = 'Tarefa'
)
$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$nomeTarefa = 'MonitorTI'
$nomeRegra = 'Monitor TI (rede local)'
$atalho = Join-Path ([Environment]::GetFolderPath('Startup')) 'Monitor TI.lnk'

function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}
if (-not (Test-Admin)) { Write-Host 'Execute como administrador.' -ForegroundColor Red; exit 1 }

if ($Acao -eq 'Remover') {
    Unregister-ScheduledTask -TaskName $nomeTarefa -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item $atalho -Force -ErrorAction SilentlyContinue
    Remove-NetFirewallRule -DisplayName $nomeRegra -ErrorAction SilentlyContinue
    Write-Host 'Início automático e regra de firewall removidos.' -ForegroundColor Green
    exit 0
}

if (-not (Test-Path (Join-Path $projectDir 'app.py'))) { Write-Host 'app.py não encontrado nesta pasta.' -ForegroundColor Red; exit 1 }

if ($Tipo -eq 'Tarefa') {
    Write-Host 'A tarefa roda com uma conta do Windows. Para as ações remotas (WMI, mensagens, PAExec) use uma conta com permissão de administrador nas máquinas.'
    Write-Host '  [1] Minha conta atual (pede a senha do Windows)'
    Write-Host '  [2] SYSTEM (sem senha; só serve se as máquinas não exigirem a sua conta)'
    $c = Read-Host 'Escolha 1 ou 2'
    $cmdLinha = '/c "' + (Join-Path $projectDir 'iniciar.bat') + '" /silent'
    $acao = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument $cmdLinha -WorkingDirectory $projectDir
    $gatilho = New-ScheduledTaskTrigger -AtStartup
    $config = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
    Unregister-ScheduledTask -TaskName $nomeTarefa -Confirm:$false -ErrorAction SilentlyContinue
    if ($c -eq '2') {
        $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
        Register-ScheduledTask -TaskName $nomeTarefa -Action $acao -Trigger $gatilho -Settings $config -Principal $principal | Out-Null
    } else {
        $cred = Get-Credential -UserName "$env:USERDOMAIN\$env:USERNAME" -Message 'Senha da conta que vai executar o Monitor TI'
        Register-ScheduledTask -TaskName $nomeTarefa -Action $acao -Trigger $gatilho -Settings $config -RunLevel Highest `
            -User $cred.UserName -Password $cred.GetNetworkCredential().Password | Out-Null
    }
    Write-Host "Tarefa '$nomeTarefa' criada: o monitor inicia sozinho quando o PC ligar (log em data\monitor.log)." -ForegroundColor Green
} else {
    $py = (Get-Command pythonw.exe -ErrorAction SilentlyContinue)
    if (-not $py) {
        $exe = & py.exe -3 -c "import sys; print(sys.executable)" 2>$null
        if ($exe) { $pw = Join-Path (Split-Path $exe) 'pythonw.exe' } else { Write-Host 'pythonw.exe não encontrado.' -ForegroundColor Red; exit 1 }
    } else { $pw = $py.Source }
    $ws = New-Object -ComObject WScript.Shell
    $lnk = $ws.CreateShortcut($atalho)
    $lnk.TargetPath = $pw
    $lnk.Arguments = '"' + (Join-Path $projectDir 'bandeja.py') + '"'
    $lnk.WorkingDirectory = $projectDir
    $lnk.Save()
    Write-Host "Atalho criado em: $atalho (a bandeja inicia quando você entrar no Windows)." -ForegroundColor Green
}

$fw = Read-Host 'Liberar o acesso ao painel para a rede local (outros PCs e celulares na mesma rede)? [s/N]'
if ($fw -match '^[sSyY]') {
    $porta = 5050
    $envFile = Join-Path $projectDir '.env'
    if (Test-Path $envFile) {
        $linha = Select-String -Path $envFile -Pattern '^MONITOR_PORT=(\d+)' | Select-Object -First 1
        if ($linha) { $porta = [int]$linha.Matches[0].Groups[1].Value }
    }
    Remove-NetFirewallRule -DisplayName $nomeRegra -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName $nomeRegra -Direction Inbound -Protocol TCP -LocalPort $porta -Action Allow `
        -Profile Private, Domain -RemoteAddress LocalSubnet | Out-Null
    $linhas = @(); if (Test-Path $envFile) { $linhas = @(Get-Content $envFile | Where-Object { $_ -notmatch '^MONITOR_HOST=' }) }
    $linhas += 'MONITOR_HOST=0.0.0.0'
    Set-Content -Path $envFile -Value $linhas -Encoding UTF8
    Write-Host "Porta $porta liberada só para a sub-rede local (perfis Privado/Domínio) e MONITOR_HOST=0.0.0.0 gravado no .env." -ForegroundColor Green
    Write-Host 'Atenção: o servidor usa HTTP simples. Para acessar de fora da empresa use VPN.' -ForegroundColor Yellow
}
