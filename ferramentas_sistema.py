# -*- coding: utf-8 -*-
"""
Ferramentas de gestão remota importadas do painel `sistema` (appold (1).py).

Mecanismo de execução: PAExec (paexec.exe) — substituto redistribuível do PsExec.
As rotas usam o hostname capturado automaticamente do computador clicado no monitor.
Registradas como Blueprint no Monitor_Hibrido_Pro.py sob /api/sistema/*.
"""

import os
import re
import ipaddress
import subprocess
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from flask import Blueprint, request, jsonify, Response, stream_with_context, session

from config import BASE_DIR, oem_encoding
import credenciais
import db
import requisitos

tools_bp = Blueprint('ferramentas_sistema', __name__)

# PAExec e DelProf2 não acompanham o repositório (licenças próprias): o assistente de
# configuração instala o PAExec (com verificação de assinatura) e o DelProf2 é manual.
OEM_ENC = oem_encoding()


def _caminho(chave):
    return requisitos.caminho_ferramenta(chave) or os.path.join(BASE_DIR, requisitos.FERRAMENTAS[chave]['arquivo'])


@tools_bp.before_request
def _exigir_admin():
    """Todas as ferramentas executam comandos nas máquinas da rede: só administrador do sistema,
    e usando a conta de REDE da própria pessoa (sem conta fixa)."""
    if not session.get('usuario'):
        return jsonify({"sucesso": False, "erro": "Login necessário"}), 401
    if session.get('papel') != 'admin':
        return jsonify({"sucesso": False, "erro": "Apenas administradores podem usar as ferramentas de gestão."}), 403
    if not credenciais.atual():
        dados = request.get_json(silent=True)
        host = str((dados or {}).get('hostname', '')) if isinstance(dados, dict) else ''
        return jsonify({"sucesso": False, "ok": False, "precisa_rede": True, "host_sugerido": host[:255],
                        "erro": "Entre com a sua conta de rede para usar a gestão remota."}), 428


def _dados() -> dict:
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}

# ==============================================================================
# CREDENCIAIS PARA ACESSO REMOTO (paexec)
# Configuradas no assistente de primeiro acesso / tela de Configurações (ficam criptografadas no banco). Se o usuário ficar
# vazio, o PAExec usa a conta do usuário que está rodando o monitor.
# Obs.: o PAExec recebe a senha pela linha de comando; rode o monitor em um servidor restrito.
# ==============================================================================
def pae_auth():
    """Credenciais do PAExec = conta de rede da pessoa logada (só na memória da sessão)."""
    return credenciais.pae_args()

# ==============================================================================
# CONFIG DE PERFORMANCE
# ==============================================================================
MAX_WORKERS = 10
DEFAULT_TIMEOUT = 30
BATCH_TIMEOUT = 60
INSTALL_TIMEOUT = 300

# ==============================================================================
# CAMINHOS DE REDE CENTRALIZADOS
# ==============================================================================
class _Caminhos(dict):
    """Caminhos dos instaladores no compartilhamento de rede (configurável em Configurações > file_server)."""
    _MAPA = {
        "chrome":         r"Navegador\Chrome.msi",
        "anydesk_novo":   r"Programas\AnyDesk.exe",
        "anydesk_antigo": r"Programas\AnyDeskAntigo.exe",
        "rustdesk":       r"Programas\rustdesk.msi",
        "glpi":           r"Programas\Programas apos formatar\GLPI-x64.msi",
        "driver_lexmark": r"Drivers e Impressoras\LEXMARK\GDI",
        "driver_epson":   r"Drivers e Impressoras\Driver Universal Epson",
        "atalho_default": r"Atalhos\atalho.lnk",
    }

    def __getitem__(self, chave):
        base = db.get_config('file_server').rstrip('\\')
        if not base:
            raise KeyError('Compartilhamento de instaladores (file_server) não configurado')
        return base + '\\' + self._MAPA[chave]


PATHS = _Caminhos()

# ==============================================================================
# HELPERS
# ==============================================================================
_HOSTNAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_\-\.]{0,253}$')

def is_ipv4(s: str) -> bool:
    try:
        return ipaddress.ip_address(s).version == 4
    except ValueError:
        return False

def validate_hostname(h: str) -> bool:
    if not h or len(h) > 255:
        return False
    return bool(_HOSTNAME_RE.match(h))

def parse_hosts(text: str) -> list:
    if isinstance(text, list):
        out = []
        for t in text:
            out.extend(x for x in str(t).splitlines() if x.strip())
        text = "\n".join(out)
    return [h.strip() for h in text.splitlines() if h.strip() and validate_hostname(h.strip())]

def run_batch(hosts: list, fn, max_workers: int = MAX_WORKERS) -> list:
    logs = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fn, h): h for h in hosts}
        for future in as_completed(futures):
            try:
                logs.append(future.result())
            except Exception as e:
                logs.append(f"❌ {futures[future]}: Erro inesperado — {e}")
    return logs

def err(msg: str, code: int = 400):
    return jsonify({"sucesso": False, "erro": msg}), code

def ok(output: str):
    return jsonify({"sucesso": True, "output": output})


def psexec(hostname: str, cmd_args: list, timeout: int = DEFAULT_TIMEOUT, system: bool = True, auth=None):
    """
    Executa remoto via PAExec (mesmo canal SCM/ADMIN$ / porta 135 do PsExec).
    system=True por padrão: o modo SYSTEM (-s) é necessário para comandos que
    exigem privilégios elevados (fsutil/disco, impressoras, gestão remota etc.).
    Confirmado experimentalmente que -s funciona neste ambiente (sem RC=-9).
    """
    if not os.path.isfile(_caminho('paexec')):
        return subprocess.CompletedProcess([], -1, "", "paexec.exe não instalado (use Configurações > Requisitos).")
    # Ordem OBRIGATÓRIA do PAExec: \\host deve vir ANTES de -s/-u/-p
    # (senão o PAExec falha com RC=-9). -s e credenciais de domínio atrás do host.
    cmd = [_caminho('paexec'), '-accepteula', f"\\\\{hostname}"]
    if system:
        cmd.append('-s')
    cmd.extend(pae_auth() if auth is None else auth)
    cmd.extend(cmd_args)
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              encoding=OEM_ENC, errors='ignore', timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, -1, "", f"Timeout ({timeout}s) — {hostname} offline?")
    except Exception as e:
        return subprocess.CompletedProcess(cmd, -1, "", str(e))


def _copy_to_remote(host: str, local_src: str, remote_dst: str) -> tuple:
    unc = remote_dst.replace('C:\\', f"\\\\{host}\\C$\\")
    try:
        with credenciais.como_usuario():       # usa a conta de rede da pessoa logada
            shutil.copyfile(local_src, unc)
        return (0, "")
    except Exception as e:
        return (-1, str(e))


def validate_queue_name(n: str) -> bool:
    return bool(re.match(r'^[A-Za-z\u00C0-\u00FF\u00AA\u00BA\d\-_\. ]{1,100}$', n))


# ==============================================================================
# ROTAS — cada uma recebe o host do computador automaticamente
# ==============================================================================

@tools_bp.route('/listar_programas', methods=['POST'])
def listar_programas():
    h = _dados().get('hostname', '').strip()
    if not validate_hostname(h):
        return err("Hostname inválido.")
    ps = (
        "$p=@('HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
        "'HKLM:\\Software\\Wow6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',"
        "'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*');"
        "Get-ItemProperty $p -EA SilentlyContinue|Where{$_.DisplayName}|"
        "Select -Expand DisplayName|Sort -Unique"
    )
    try:
        r = psexec(h, ['powershell', '-command', ps], timeout=BATCH_TIMEOUT)
        return ok(r.stdout or "(sem resposta)")
    except Exception as e:
        return err(str(e), 500)


@tools_bp.route('/desinstalar_programa', methods=['POST'])
def desinstalar_programa():
    h    = _dados().get('hostname', '').strip()
    prog = _dados().get('programa', '').strip()
    if not validate_hostname(h):
        return err("Hostname inválido.")
    if not prog or len(prog) > 200:
        return err("Nome do programa inválido.")
    prog_safe = prog.replace("'", "''")
    script = f"""
    $app = Get-ItemProperty @(
        'HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',
        'HKLM:\\Software\\Wow6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*',
        'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*'
    ) -EA SilentlyContinue | Where {{$_.DisplayName -eq '{prog_safe}'}} | Select -First 1
    if (-not $app) {{ Write-Output 'Programa nao encontrado.'; exit 1 }}
    if ($app.UninstallString -match 'MsiExec.exe') {{
        Start-Process 'MsiExec.exe' -ArgumentList "/x $($app.PSChildName) /qn" -Wait
        Write-Output 'Removido via MSI.'
    }} elseif ($app.DisplayName -match 'AnyDesk') {{
        $p = 'C:\\Program Files (x86)\\AnyDesk\\AnyDesk.exe'
        if (Test-Path $p) {{ Start-Process $p -ArgumentList '--remove --silent' -Wait; Write-Output 'AnyDesk removido.' }}
    }} else {{ Write-Output 'Desinstalador nao suportado automaticamente.' }}
    """
    try:
        r = psexec(h, ['powershell', '-command', script], timeout=120)
        return ok(r.stdout or "(sem resposta)")
    except Exception as e:
        return err(str(e), 500)


@tools_bp.route('/reiniciar', methods=['POST'])
def reiniciar():
    h = _dados().get('hostname', '').strip()
    if not validate_hostname(h):
        return err("Hostname inválido.")
    try:
        codigo = credenciais.desligar_ou_reiniciar(h, reiniciar=True)   # WMI com a conta da pessoa
        if codigo != 0:
            return err(f"O Windows recusou o comando (código {codigo}).", 500)
        return ok(f"Comando de reinicialização enviado para {h}.")
    except Exception as e:
        return err(str(e), 500)


@tools_bp.route('/consultar_disco', methods=['POST'])
def consultar_disco():
    h = _dados().get('hostname', '').strip()
    if not validate_hostname(h):
        return err("Hostname inválido.")
    try:
        r = psexec(h, ['cmd', '/c', 'fsutil', 'volume', 'diskfree', 'C:'], timeout=15)
        return ok(r.stdout or "(sem resposta)")
    except Exception as e:
        return err(str(e), 500)


@tools_bp.route('/listar_impressoras', methods=['POST'])
def listar_impressoras():
    h = _dados().get('hostname', '').strip()
    if not validate_hostname(h):
        return err("Hostname inválido.")
    ps = "Get-Printer|Select Name,PortName,DriverName|Sort Name|Format-Table -AutoSize|Out-String -Width 4096"
    try:
        r = psexec(h, ['powershell', '-command', ps], timeout=30)
        return ok(r.stdout or "(sem resposta)")
    except Exception as e:
        return err(str(e), 500)


@tools_bp.route('/remover_impressora', methods=['POST'])
def remover_impressora():
    h   = _dados().get('hostname', '').strip()
    imp = _dados().get('impressora', '').strip()
    if not validate_hostname(h):
        return err("Hostname inválido.")
    if not imp:
        return err("Nome da impressora obrigatório.")
    imp_safe = imp.replace("'", "''")
    ps = f"Remove-Printer -Name '{imp_safe}' -EA Stop; Write-Output 'Impressora removida.'"
    try:
        r = psexec(h, ['powershell', '-command', ps], timeout=30)
        return ok(r.stdout or "(sem resposta)")
    except Exception as e:
        return err(str(e), 500)


@tools_bp.route('/instalar_impressora_ip', methods=['POST'])
def instalar_impressora_ip():
    import tempfile
    data = _dados()
    h     = data.get('hostname', '').strip()
    pip   = data.get('printer_ip', '').strip()
    nome  = data.get('nome_fila', '').strip()
    fab   = data.get('fabricante', 'LEXMARK').upper()

    if not validate_hostname(h):
        return err("Hostname inválido.")
    if not is_ipv4(pip):
        return err("IP da impressora inválido.")
    if not validate_queue_name(nome):
        return err("Nome da fila inválido.")

    if fab == 'EPSON':
        driver = "EPSON Universal Print Driver"
        inf    = os.path.join(PATHS["driver_epson"], "E_JFB0DE.INF")
    else:
        driver = "Lexmark Universal v2 XL"
        inf    = os.path.join(PATHS["driver_lexmark"], "LMUD1p40.inf")

    def ps_esc(s):
        return s.replace('"', '`"')

    nome_esc   = ps_esc(nome)
    driver_esc = ps_esc(driver)
    inf_esc    = ps_esc(inf)

    script = f"""$port = "IP_{pip}"
if (-not (Get-PrinterPort -Name $port -EA SilentlyContinue)) {{
    Add-PrinterPort -Name $port -PrinterHostAddress "{pip}"
    Write-Output "Porta criada: $port"
}} else {{
    Write-Output "Porta ja existe: $port"
}}
pnputil.exe /add-driver "{inf_esc}" /install | Out-Null
try {{
    Add-PrinterDriver -Name "{driver_esc}" -EA Stop
    Write-Output "Driver carregado."
}} catch {{
    Add-PrinterDriver -Name "{driver_esc}" -InfPath "{inf_esc}"
    Write-Output "Driver carregado via InfPath."
}}
if (Get-Printer -Name "{nome_esc}" -EA SilentlyContinue) {{
    Remove-Printer -Name "{nome_esc}" -EA SilentlyContinue
    Write-Output "Impressora anterior removida."
}}
Add-Printer -Name "{nome_esc}" -DriverName "{driver_esc}" -PortName $port
Write-Output "Impressora adicionada."
Restart-Service Spooler -Force
Start-Sleep -Seconds 3
if (Get-Printer -Name "{nome_esc}" -EA SilentlyContinue) {{
    Write-Output "SUCESSO: Impressora instalada e confirmada."
}} else {{
    Write-Output "ALERTA: Nao detectada apos reinicio do spooler."
}}
"""
    tmp = tempfile.NamedTemporaryFile(mode='wb', suffix='.ps1', delete=False, prefix='ps_imp_')
    try:
        tmp.write(script.encode('utf-16'))
        tmp.close()
        dest_ps1 = r"C:\Windows\Temp\ps_imp.ps1"
        code, cperr = _copy_to_remote(h, tmp.name, dest_ps1)
        if code != 0:
            return err(f"Falha ao copiar script para {h}: {cperr}")
        r = psexec(h, [
            'powershell', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
            '-File', r'C:\Windows\Temp\ps_imp.ps1',
        ], timeout=INSTALL_TIMEOUT)
        try:
            psexec(h, ['cmd', '/c', 'del', r'C:\Windows\Temp\ps_imp.ps1'], timeout=10)
        except Exception:
            pass
        output   = (r.stdout or '').strip()
        errout   = (r.stderr or '').strip()
        combined = output if output else errout if errout else "(sem output)"
        return ok(combined)
    except Exception as e:
        return err(str(e), 500)
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


@tools_bp.route('/atualizar_chrome', methods=['POST'])
def atualizar_chrome():
    h = _dados().get('hostname', '').strip()
    if not validate_hostname(h):
        return err("Hostname inválido.")
    try:
        r = psexec(h, ['msiexec', '/i', PATHS["chrome"], '/qn', '/norestart'], timeout=180)
        # msiexec: 0 = ok, 3010 = ok (reinício pendente)
        if r.returncode in (0, 1641, 3010):
            return ok(f"Chrome atualizado em {h}.")
        return err(f"Falha ao atualizar o Chrome em {h} (código {r.returncode}). {(r.stderr or r.stdout or '').strip()}", 500)
    except Exception as e:
        return err(str(e), 500)


@tools_bp.route('/corrigir_idioma_chrome', methods=['POST'])
def corrigir_idioma_chrome():
    h = _dados().get('hostname', '').strip()
    if not validate_hostname(h):
        return err("Hostname inválido.")
    ps = r"""
$locale = 'pt-BR'
$alterados = @()
$erros = @()
$chromeAberto = $false
if (Get-Process -Name 'chrome' -EA SilentlyContinue) {
    $chromeAberto = $true
    Stop-Process -Name 'chrome' -Force -EA SilentlyContinue
    $sw = [Diagnostics.Stopwatch]::StartNew()
    while ((Get-Process -Name 'chrome' -EA SilentlyContinue) -and $sw.Elapsed.TotalSeconds -lt 10) {
        Start-Sleep -Milliseconds 300
    }
}
$userProfiles = @()
try {
    $loggedUser = (query user 2>$null | Select-String '>' |
                   ForEach-Object { ($_ -split '\s+')[1] } | Select-Object -First 1)
    if ($loggedUser) {
        $p = "C:\Users\$loggedUser\AppData\Local\Google\Chrome\User Data"
        if (Test-Path $p) { $userProfiles += $p }
    }
} catch {}
$fallback = "$env:LOCALAPPDATA\Google\Chrome\User Data"
if ((Test-Path $fallback) -and ($userProfiles -notcontains $fallback)) { $userProfiles += $fallback }
if (-not $userProfiles) { Write-Output "AVISO: Nenhum perfil do Chrome encontrado." }
foreach ($profileBase in $userProfiles) {
    $localState = Join-Path $profileBase 'Local State'
    if (Test-Path $localState) {
        try {
            $ls = Get-Content $localState -Raw -Encoding UTF8 | ConvertFrom-Json
            if (-not $ls.PSObject.Properties['browser']) {
                $ls | Add-Member -MemberType NoteProperty -Name 'browser' -Value ([PSCustomObject]@{}) -Force
            }
            $ls.browser | Add-Member -MemberType NoteProperty -Name 'app_locale' -Value $locale -Force
            if (-not $ls.PSObject.Properties['intl']) {
                $ls | Add-Member -MemberType NoteProperty -Name 'intl' -Value ([PSCustomObject]@{}) -Force
            }
            $ls.intl | Add-Member -MemberType NoteProperty -Name 'accept_languages'  -Value $locale -Force
            $ls.intl | Add-Member -MemberType NoteProperty -Name 'selected_languages' -Value $locale -Force
            $ls | ConvertTo-Json -Depth 20 -Compress | Set-Content $localState -Encoding UTF8 -Force
            $alterados += $localState
        } catch { $erros += "Local State: $_" }
    }
    $perfis = Get-ChildItem $profileBase -Directory -EA SilentlyContinue |
              Where-Object { $_.Name -eq 'Default' -or $_.Name -match '^Profile \d+$' }
    foreach ($perfil in $perfis) {
        foreach ($fileName in @('Preferences', 'Secure Preferences')) {
            $prefFile = Join-Path $perfil.FullName $fileName
            if (-not (Test-Path $prefFile)) { continue }
            try {
                $pref = Get-Content $prefFile -Raw -Encoding UTF8 | ConvertFrom-Json
                if (-not $pref.PSObject.Properties['intl']) {
                    $pref | Add-Member -MemberType NoteProperty -Name 'intl' -Value ([PSCustomObject]@{}) -Force
                }
                $pref.intl | Add-Member -MemberType NoteProperty -Name 'accept_languages'  -Value $locale -Force
                $pref.intl | Add-Member -MemberType NoteProperty -Name 'selected_languages' -Value $locale -Force
                $pref | Add-Member -MemberType NoteProperty -Name 'app_locale' -Value $locale -Force
                $pref | ConvertTo-Json -Depth 20 -Compress | Set-Content $prefFile -Encoding UTF8 -Force
                $alterados += $prefFile
            } catch { $erros += "${prefFile}: $_" }
        }
    }
}
foreach ($key in @('HKCU:\Software\Policies\Google\Chrome','HKLM:\Software\Policies\Google\Chrome')) {
    try {
        if (-not (Test-Path $key)) { New-Item -Path $key -Force | Out-Null }
        Set-ItemProperty -Path $key -Name 'ApplicationLocale' -Value $locale -Type String -Force
        $alterados += $key
    } catch { $erros += "${key}: $_" }
}
if ($chromeAberto) { Write-Output "AVISO: Chrome encerrado para aplicar alteracoes." }
if ($alterados) {
    Write-Output "OK: Idioma definido para pt-BR em $($alterados.Count) local(is)."
    $alterados | ForEach-Object { Write-Output "  -> $_" }
} else { Write-Output "FALHA: Nenhum arquivo foi alterado." }
if ($erros) { Write-Output "ERROS:"; $erros | ForEach-Object { Write-Output "  !! $_" } }
"""
    try:
        r = psexec(h, ['powershell', '-command', ps], timeout=45)
        return ok((r.stdout or '') or (r.stderr or ''))
    except Exception as e:
        return err(str(e), 500)


@tools_bp.route('/instalar_anydesk', methods=['POST'])
def instalar_anydesk():
    h      = _dados().get('hostname', '').strip()
    versao = _dados().get('versao', 'NOVO').upper()
    if not validate_hostname(h):
        return err("Hostname inválido.")
    src = PATHS["anydesk_novo"] if versao == 'NOVO' else PATHS["anydesk_antigo"]
    logs = [f"=== AnyDesk ({versao}) → {h} ==="]
    try:
        logs.append("1. Parando processos existentes...")
        psexec(h, ['taskkill', '/f', '/im', 'anydesk.exe'], timeout=20)
        logs.append("2. Copiando instalador...")
        code, _ = _copy_to_remote(h, src, r"C:\Windows\Temp\anydesk_installer.exe")
        if code != 0:
            return jsonify({"sucesso": False, "erro": "Falha ao copiar instalador.", "output": "\n".join(logs)}), 500
        logs.append("3. Instalando silenciosamente...")
        psexec(h, [
            r'C:\Windows\Temp\anydesk_installer.exe',
            '--install', r'C:\Program Files (x86)\AnyDesk',
            '--start-with-win', '--silent',
            '--create-shortcuts', '--create-desktop-icon'
        ], timeout=120)
        logs.append("4. Iniciando serviço...")
        psexec(h, ['net', 'start', 'AnyDesk'], timeout=20)
        chk = psexec(h, ['sc', 'query', 'AnyDesk'], timeout=10)
        if "SERVICE_NAME" in chk.stdout:
            logs.append("✅ SUCESSO: Serviço AnyDesk ativo.")
            return ok("\n".join(logs))
        return jsonify({"sucesso": False, "erro": "Instalação incerta.", "output": "\n".join(logs)}), 200
    except Exception as e:
        return jsonify({"sucesso": False, "erro": str(e), "output": "\n".join(logs)}), 500


@tools_bp.route('/instalar_rustdesk', methods=['POST'])
def instalar_rustdesk():
    h = _dados().get('hostname', '').strip()
    if not validate_hostname(h):
        return err("Hostname inválido.")
    try:
        psexec(h, ['taskkill', '/f', '/im', 'rustdesk.exe'], timeout=20)
        code, _ = _copy_to_remote(h, PATHS["rustdesk"], r"C:\Windows\Temp\rustdesk.msi")
        if code != 0:
            return err("Falha ao copiar instalador.", 500)
        psexec(h, ['msiexec', '/i', r'C:\Windows\Temp\rustdesk.msi', '/qn', '/norestart'], timeout=120)
        chk = psexec(h, ['sc', 'query', 'RustDesk'], timeout=10)
        if "SERVICE_NAME" in chk.stdout:
            return ok("✅ RustDesk instalado com sucesso.")
        return err("Falha na verificação do serviço.")
    except Exception as e:
        return err(str(e), 500)


@tools_bp.route('/limpar_perfis', methods=['POST'])
def limpar_perfis():
    if not os.path.exists(_caminho('delprof2')):
        return err("Delprof2.exe não encontrado no servidor.", 500)
    h = _dados().get('hostname', '').strip()
    if not validate_hostname(h):
        return err("Hostname inválido.")

    auth = pae_auth()              # conta de rede da pessoa, capturada antes do streaming
    cred = credenciais.atual()

    def gerar_log():
        yield "=== Limpeza de Perfis — Delprof2 ===\n"
        try:
            # Verifica se existe versão anterior do Delprof2 no host remoto
            yield f"[{h}] Verificando...\n"
            chk = subprocess.run(
                [_caminho('paexec'), '-accepteula', f"\\\\{h}"] + auth +
                ['cmd', '/c', 'if exist C:\\Windows\\Temp\\Delprof2.exe echo exists'],
                capture_output=True, text=True, encoding=OEM_ENC, errors='ignore', timeout=15
            )
            if "exists" in chk.stdout:
                yield f"[{h}] Removendo versão anterior...\n"
                subprocess.run(
                    [_caminho('paexec'), f"\\\\{h}"] + auth +
                    ['cmd', '/c', 'del C:\\Windows\\Temp\\Delprof2.exe'],
                    capture_output=True, timeout=10
                )
            yield f"[{h}] Copiando Delprof2...\n"
            with credenciais.como_usuario(cred):
                shutil.copyfile(_caminho('delprof2'), f"\\\\{h}\\ADMIN$\\Temp\\Delprof2.exe")
            yield f"[{h}] Executando limpeza (Delprof2 /u)...\n"
            proc = subprocess.Popen(
                [_caminho('paexec'), '-accepteula', f"\\\\{h}"] + auth +
                ['-s', 'cmd', '/c', r'C:\Windows\Temp\Delprof2.exe /u'],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding=OEM_ENC, bufsize=1, errors='ignore'
            )
            for line in iter(proc.stdout.readline, ''):
                yield line
            proc.stdout.close()
            proc.wait()
            yield f"[{h}] ✅ Concluído.\n"
        except subprocess.TimeoutExpired:
            yield f"[{h}] ⏱️ Timeout — offline ou sem resposta.\n"
        except Exception as e:
            yield f"[{h}] ❌ Erro: {e}\n"
        yield "\n=== Processo finalizado! ===\n"

    return Response(stream_with_context(gerar_log()), mimetype='text/plain')
