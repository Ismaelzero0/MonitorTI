import sys
import contextvars
import random
import subprocess
import re
import csv
import io
import webbrowser
import threading
import time
import os
import socket
import shutil
import ipaddress
import shlex
import requests
import base64
import pythoncom
import urllib3
from urllib.parse import urlsplit
import paramiko
from datetime import datetime, timedelta
from functools import wraps
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from flask import (Flask, jsonify, render_template, request, session, redirect, url_for,
                   send_from_directory, send_file, Response)

from config import BASE_DIR, DATA_DIR, env, env_bool, env_int, oem_encoding
import credenciais
import db
import requisitos

# Evita UnicodeEncodeError nos print() quando a saída é redirecionada para arquivo (log) no Windows
for _fluxo in (sys.stdout, sys.stderr):
    try: _fluxo.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
    except (AttributeError, ValueError): pass

# SILENCIA AVISOS DE HTTPS (impressoras de rede usam certificado autoassinado)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

db.init()

app = Flask(__name__)
app.secret_key = db.get_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=env_bool('SESSION_COOKIE_SECURE', env_bool('MONITOR_HTTPS', False)),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    MAX_CONTENT_LENGTH=4 * 1024 * 1024,
)

# ---- Ferramentas de gestão remota (paexec) ----
from ferramentas_sistema import tools_bp, validate_hostname
app.register_blueprint(tools_bp, url_prefix='/api/sistema')

# ==============================================================================
# CONFIGURAÇÕES GLOBAIS (guardadas no banco; veja a tela Configurações do admin)
# ==============================================================================
OEM_ENC = oem_encoding()


def cfg(chave, padrao=''):
    return db.get_config(chave, padrao)


STATUS_REALTIME = {}
KNOWN_HOSTS_FILE = os.path.join(DATA_DIR, 'known_hosts')
LOCK_KNOWN_HOSTS = threading.Lock()

# -- COMANDOS SSH PRÉ-DEFINIDOS PARA PAINÉIS --
COMANDOS_PAINEL = {
    "status": {
        "nome": "Status Geral",
        "icone": "fa-solid fa-heart-pulse",
        "cor": "#10b981",
        "cmd": "echo '=== STATUS ==='; uptime; echo '--- FIREFOX ---'; pgrep -c firefox-esr 2>/dev/null && echo 'Firefox: RODANDO' || echo 'Firefox: PARADO'; echo '--- CHROMIUM ---'; pgrep -c chromium 2>/dev/null && echo 'Chromium: RODANDO' || echo 'Chromium: PARADO'; echo '--- AUDIO ---'; wpctl status 2>/dev/null | grep -E 'HDMI|default' | head -3; echo '--- DISCO ---'; df -h / | tail -1; echo '--- MEMORIA ---'; free -h | grep Mem; echo '--- REDE ---'; ip -4 addr show | grep inet | head -3; echo '--- TEMP ---'; vcgencmd measure_temp 2>/dev/null"
    },
    "reiniciar_painel": {
        "nome": "Reiniciar Painel (Completo)",
        "icone": "fa-solid fa-rotate-right",
        "cor": "#f59e0b",
        "cmd": "sudo reboot"
    },
    "reiniciar_navegador": {
        "nome": "Reiniciar Navegador",
        "icone": "fa-solid fa-globe",
        "cor": "#3b82f6",
        "cmd": "kill -9 $(pgrep -x firefox-esr) 2>/dev/null; kill -9 $(pgrep -x chromium) 2>/dev/null; kill -9 $(pgrep -f 'painel.sh') 2>/dev/null; sleep 2; nohup setsid bash ~/painel.sh > /dev/null 2>&1 & echo 'Navegador reiniciado'"
    },
    "ativar_kiosk": {
        "nome": "Ativar Kiosk (Tela Cheia)",
        "icone": "fa-solid fa-tv",
        "cor": "#10b981",
        "cmd": "kill -9 $(pgrep -x chromium) 2>/dev/null; kill -9 $(pgrep -x firefox-esr) 2>/dev/null; sleep 1; nohup setsid bash ~/painel.sh > /dev/null 2>&1 & echo 'Kiosk ativado'"
    },
    "desativar_kiosk": {
        "nome": "Desativar Kiosk (Liberar Teclado)",
        "icone": "fa-solid fa-keyboard",
        "cor": "#ef4444",
        "cmd": "kill -9 $(pgrep -x chromium) 2>/dev/null; kill -9 $(pgrep -x firefox-esr) 2>/dev/null; kill -9 $(pgrep -f 'painel.sh') 2>/dev/null; echo 'Kiosk desativado. Navegador fechado. Rasp liberado para uso normal.'"
    },
    "testar_audio": {
        "nome": "Testar Audio HDMI",
        "icone": "fa-solid fa-volume-high",
        "cor": "#8b5cf6",
        "cmd": "wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.8; wpctl set-mute @DEFAULT_AUDIO_SINK@ 0; echo '=== Sink atual ==='; wpctl status | grep -E '\\*|HDMI'; echo '=== Testando som... ==='; pw-play /usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga 2>/dev/null && echo 'AUDIO OK - som reproduzido com sucesso' || echo 'AUDIO FALHOU - tentando alternativa...'; speaker-test -t sine -f 440 -l 1 -p 1 2>/dev/null && echo 'AUDIO OK (speaker-test)' || echo 'speaker-test indisponivel. Verificar se o sink HDMI esta correto.'"
    },
    "status_audio": {
        "nome": "Status Audio",
        "icone": "fa-solid fa-wave-square",
        "cor": "#06b6d4",
        "cmd": "echo '=== SINKS DISPONIVEIS ==='; wpctl status | sed -n '/Sinks/,/Sources/p'; echo ''; echo '=== VOLUME ATUAL ==='; wpctl get-volume @DEFAULT_AUDIO_SINK@; echo ''; echo '=== STREAMS ATIVOS ==='; pw-dump 2>/dev/null | python3 -c \"import sys,json; d=json.load(sys.stdin); streams=[n for n in d if 'Stream' in n.get('info',{}).get('media.class','')]; [print(f'  {n[\\\"info\\\"][\\\"node.name\\\"]} | estado={n[\\\"info\\\"].get(\\\"state\\\",\\\"?\\\")}') for n in streams] if streams else print('  Nenhum stream ativo')\" 2>/dev/null || echo '  (pw-dump indisponivel)'"
    },
    "mudar_url": {
        "nome": "Mudar URL do Painel",
        "icone": "fa-solid fa-link",
        "cor": "#ec4899",
        "cmd": None,
        "eh_custom": True,
        "prompt": "Digite a nova URL do painel:"
        # O comando é montado (e a URL validada/escapada) em montar_cmd_mudar_url()
    },
    "limpar_cache": {
        "nome": "Limpar Cache do Navegador",
        "icone": "fa-solid fa-broom",
        "cor": "#64748b",
        "cmd": "echo 'Limpando cache...'; rm -rf ~/.cache/mozilla/firefox/*/cache2 ~/.cache/mozilla/firefox/*/startupCache 2>/dev/null; rm -rf ~/.cache/chromium/Default/Cache ~/.cache/chromium/Default/Code\\ Cache 2>/dev/null; echo 'Cache limpo. Reiniciando...'; kill -9 $(pgrep -x chromium) 2>/dev/null; kill -9 $(pgrep -x firefox-esr) 2>/dev/null; sleep 1; nohup setsid bash ~/painel.sh > /dev/null 2>&1 & echo 'Reiniciado'"
    },
    "ver_logs": {
        "nome": "Ultimos Logs do Painel",
        "icone": "fa-solid fa-file-lines",
        "cor": "#64748b",
        "cmd": "echo '=== PAINEL.SH (ultimas 20) ==='; tail -20 ~/painel.log 2>/dev/null || echo '(sem logs)'; echo ''; echo '=== AUDIO (ultimas 10) ==='; tail -10 ~/audio.log 2>/dev/null || echo '(sem logs)'; echo ''; echo '=== WAYVNC (ultimas 5) ==='; tail -5 ~/wayvnc.log 2>/dev/null || echo '(sem logs)'"
    },
    "ver_erro": {
        "nome": "Diagnostico de Erros",
        "icone": "fa-solid fa-triangle-exclamation",
        "cor": "#ef4444",
        "cmd": "echo '=== PROCESSOS MORTOS/ZUMBIS ==='; ps -eo stat,pid,comm | awk '$1 ~ /^Z/' | grep . || echo 'Nenhum'; echo ''; echo '=== ERROS RECENTES DMESG ==='; dmesg | tail -10; echo ''; echo '=== DISK FULL? ==='; df -h / | tail -1; echo ''; echo '=== ERROS FIREFOX ==='; journalctl --no-pager -n 5 2>/dev/null | grep -i error || echo 'Nenhum'; echo ''; echo '=== WAYVNC STATUS ==='; systemctl is-active wayvnc 2>/dev/null || pgrep -a wayvnc || echo 'wayvnc NAO RODANDO'"
    },
    "atualizar_sistema": {
        "nome": "Atualizar Sistema (apt update/upgrade)",
        "icone": "fa-solid fa-download",
        "cor": "#10b981",
        "cmd": "echo 'Atualizando sistema...'; sudo apt update -qq && sudo apt upgrade -y -qq 2>&1 | tail -5; echo 'Sistema atualizado.'"
    },
    "forcar_audio_hdmi": {
        "nome": "Forcar Audio HDMI (Reconfigurar)",
        "icone": "fa-solid fa-plug",
        "cor": "#f97316",
        "cmd": "echo 'Reconfigurando audio HDMI...'; rm -f ~/audio.ok; bash ~/set-hdmi-audio.sh; echo '=== Resultado ==='; cat ~/audio.log | tail -3; wpctl status | grep -E '\\*|HDMI'"
    },
    "ativar_painel_sh": {
        "nome": "Ativar painel.sh (Watchdog)",
        "icone": "fa-solid fa-shield",
        "cor": "#10b981",
        "cmd": "pgrep -f 'bash.*painel.sh' > /dev/null && echo 'painel.sh JA RODANDO (PID: $(pgrep -f bash.*painel.sh))' || { echo 'Iniciando painel.sh...'; nohup setsid bash ~/painel.sh > /dev/null 2>&1 & echo 'painel.sh iniciado (PID: '$!')'; }"
    },
    "desativar_painel_sh": {
        "nome": "Desativar painel.sh (Parar Watchdog)",
        "icone": "fa-solid fa-stop",
        "cor": "#ef4444",
        "cmd": "PIDS=$(pgrep -f 'bash.*painel.sh'); if [ -n \"$PIDS\" ]; then echo \"$PIDS\" | xargs kill -9; echo 'painel.sh encerrado (PIDs: '$PIDS')'; else echo 'painel.sh nao estava rodando.'; fi"
    },
    "info_sistema": {
        "nome": "Info do Sistema",
        "icone": "fa-solid fa-circle-info",
        "cor": "#3b82f6",
        "cmd": "echo '=== HOSTNAME ==='; hostname; echo '=== OS ==='; cat /etc/os-release | head -3; echo '=== KERNEL ==='; uname -r; echo '=== UPTIME ==='; uptime -p; echo '=== IP ==='; ip -4 addr show | grep inet; echo '=== BROWSER ==='; (firefox-esr --version 2>/dev/null || echo 'Firefox: N/A'); (chromium --version 2>/dev/null || echo 'Chromium: N/A'); echo '=== WAYVNC ==='; systemctl is-active wayvnc 2>/dev/null || echo 'N/A'; echo '=== SENSORS ==='; vcgencmd measure_temp 2>/dev/null || echo 'N/A'"
    }
}

# Tabelas de Referência
WINDOWS_VERSIONS = {
    '26200': '11 25H2', '26100': '11 24H2', '22631': '11 23H2', '22621': '11 22H2', '22000': '11 21H2',
    '19045': '10 22H2', '19044': '10 21H2', '19043': '10 21H1', '19042': '10 20H2',
    '19041': '10 2004', '18363': '10 1909', '18362': '10 1903', '17763': '10 1809'
}

MONITOR_BRANDS = {
    'ACR': 'Acer', 'AOC': 'AOC', 'DEL': 'Dell', 'HP': 'HP', 'IBM': 'IBM', 
    'LEN': 'Lenovo', 'LGD': 'LG', 'SAM': 'Samsung', 'LG': 'LG', 'UNK': 'Genérico',
    'VSC': 'ViewSonic', 'BNQ': 'BenQ'
}

PRINTER_BRANDS = ['LEXMARK', 'BROTHER', 'GAINCHA', 'TALLY', 'EPSON', 'XEROX', 'HP', 'SAMSUNG', 'ZEBRA', 'KYOCERA', 'CANON', 'RICOH', 'KONICA', 'OKI']

# ==============================================================================
# 1. UTILITÁRIOS
# ==============================================================================
def body():
    """Corpo JSON da requisição (só aceita Content-Type application/json — protege contra CSRF por formulário)."""
    d = request.get_json(silent=True)
    return d if isinstance(d, dict) else {}

def limpa(valor, limite=200):
    """Texto vindo do cliente: string, sem espaços nas pontas e com tamanho limitado."""
    return str(valor if valor is not None else '').strip()[:limite]

def startupinfo_oculto():
    s = subprocess.STARTUPINFO()
    s.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    s.wShowWindow = subprocess.SW_HIDE
    return s

# ==============================================================================
# 2. REDE BÁSICA E SCANNER BACKGROUND
# ==============================================================================
_IPV4_RE = re.compile(r'^\d{1,3}(\.\d{1,3}){3}$')

_DNS_CACHE = {}          # nome -> (ip ou None, validade)
_DNS_TTL_OK, _DNS_TTL_FALHA = 300, 60

def resolve_ip(host):
    host = str(host).strip()
    if host.upper().startswith("PC-"): host = host[3:]
    if not host: return None
    if _IPV4_RE.match(host): return host
    if host.lower() == 'localhost': return '127.0.0.1'
    agora = time.time()
    hit = _DNS_CACHE.get(host)
    if hit and hit[1] > agora: return hit[0]
    ip = None
    try: ip = socket.gethostbyname(host)
    except OSError:
        sufixo = cfg('dns_suffix').strip().strip('.')
        if sufixo and sufixo.lower() not in host.lower():
            try: ip = socket.gethostbyname(f"{host}.{sufixo}")
            except OSError: pass
    _DNS_CACHE[host] = (ip, agora + (_DNS_TTL_OK if ip else _DNS_TTL_FALHA))
    return ip

def ping_individual(host):
    ip = resolve_ip(host)
    if not ip: return (host, False)
    cmd = ['ping', '-n', '1', '-w', '800', ip]
    try:
        res = subprocess.run(cmd, capture_output=True, startupinfo=startupinfo_oculto(), timeout=5)
        # No Windows o ping devolve código 0 mesmo com "Host de destino inacessível";
        # só há resposta real quando aparece "TTL=" na saída.
        saida = res.stdout.decode(OEM_ENC, errors='ignore').upper()
        return (host, res.returncode == 0 and 'TTL=' in saida)
    except (OSError, subprocess.SubprocessError):
        return (host, False)

def background_scanner_loop():
    print(">>> [BACKGROUND] Iniciando Monitoramento Contínuo (50 Threads)...")
    pool = ThreadPoolExecutor(max_workers=100)
    db.carregar_ultimo_estado()
    ultimo_expurgo = 0.0

    while True:
        try:
            todos_hosts = db.todos_hosts()
            if not todos_hosts:
                time.sleep(5)
                continue

            resultados = list(pool.map(ping_individual, todos_hosts))
            estados = {}
            for host, online in resultados:
                STATUS_REALTIME[host] = online
                estados[host] = online
            db.registrar_estados(estados)   # grava só as mudanças (histórico de disponibilidade)

            if time.time() - ultimo_expurgo > 86400:
                db.limpar_historico_antigo()
                ultimo_expurgo = time.time()
        except Exception as e:
            print(f">>> [BACKGROUND ERRO] {e}")
        time.sleep(5)

# ==============================================================================
# 4. USUÁRIOS, PAPÉIS E LOGIN
#    admin   = tudo (inventário, ações remotas, usuários, configurações)
#    tecnico = vê tudo e escreve anotações
#    leitor  = só vê
# ==============================================================================
def _nao_autorizado(codigo):
    msg = 'Login necessário' if codigo == 401 else 'Sem permissão para esta ação'
    if request.path.startswith('/api/'):
        return jsonify({"ok": False, "sucesso": False, "erro": msg, "msg": msg}), codigo
    if codigo == 401:
        return redirect(url_for('login_page', next=request.path))
    return render_template('login.html', erro=msg), 403

def papel_requerido(*papeis):
    def deco(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not session.get('usuario'):
                return _nao_autorizado(401)
            if papeis and session.get('papel') not in papeis:
                return _nao_autorizado(403)
            return f(*args, **kwargs)
        return decorated_function
    return deco

login_required = papel_requerido()
editor_required = papel_requerido('admin', 'tecnico')
admin_required = papel_requerido('admin')

def _resposta_precisa_rede():
    d = request.get_json(silent=True)
    host = limpa((d or {}).get('host') or (d or {}).get('hostname') or '', 255) if isinstance(d, dict) else ''
    msg = 'Entre com a sua conta de rede para usar a gestão remota.'
    return jsonify({"ok": False, "sucesso": False, "precisa_rede": True, "host_sugerido": host, "erro": msg, "msg": msg}), 428

def rede_requerida(f):
    """A ação precisa da conta de REDE da própria pessoa (sem conta fixa): sem ela, responde 428 e a tela pede o login."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not credenciais.atual():
            return _resposta_precisa_rede()
        return f(*args, **kwargs)
    return decorated_function

def usuario_atual():
    return session.get('usuario') or '-'

def erro_json(e, chave='msg'):
    return jsonify({"ok": False, chave: str(e)})

_LOGIN_MAX_FALHAS = 5
_LOGIN_BLOQUEIO_SEG = 300
_login_falhas = {}   # ip -> (qtde_falhas, instante_da_ultima)
_lock_login = threading.Lock()

def _login_bloqueado(ip):
    with _lock_login:
        qtde, ts = _login_falhas.get(ip, (0, 0.0))
        if qtde >= _LOGIN_MAX_FALHAS and time.time() - ts < _LOGIN_BLOQUEIO_SEG:
            return True
        if qtde and time.time() - ts >= _LOGIN_BLOQUEIO_SEG:
            _login_falhas.pop(ip, None)
    return False

def _login_registrar(ip, sucesso):
    with _lock_login:
        if sucesso:
            _login_falhas.pop(ip, None)
        else:
            qtde, _ = _login_falhas.get(ip, (0, 0.0))
            _login_falhas[ip] = (qtde + 1, time.time())

def _destino_seguro(destino):
    """Só aceita caminhos internos (evita open redirect via ?next=)."""
    if destino and destino.startswith('/') and not destino.startswith('//') and '\\' not in destino:
        return destino
    return '/'

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        ip = request.remote_addr or '?'
        if _login_bloqueado(ip):
            return render_template('login.html', erro="Muitas tentativas. Aguarde alguns minutos."), 429
        usuario = db.autenticar(request.form.get('user', ''), request.form.get('pass', ''))
        if usuario:
            _login_registrar(ip, True)
            session.clear()
            session['usuario'] = usuario['usuario']
            session['papel'] = usuario['papel']
            session.permanent = True
            db.auditar(usuario['usuario'], 'login', ip)
            return redirect(_destino_seguro(request.args.get('next')))
        _login_registrar(ip, False)
        db.auditar(request.form.get('user', '')[:40], 'login_falhou', ip)
        time.sleep(0.5)
        return render_template('login.html', erro="Usuário ou senha incorretos"), 401
    return render_template('login.html')

@app.route('/logout')
def logout():
    credenciais.descartar(session.get('cred_token'))   # sair do sistema também encerra a conta de rede
    session.clear()
    return redirect('/')

@app.route('/admin')
@admin_required
def admin_panel():
    return render_template('admin.html', usuario=usuario_atual())

_setup_pendente = None

def setup_pendente():
    """True enquanto ainda não existe nenhum usuário (assistente de primeiro acesso)."""
    global _setup_pendente
    if _setup_pendente is None or _setup_pendente:
        _setup_pendente = not db.tem_usuario()
    return _setup_pendente

def _eh_local():
    return request.remote_addr in ('127.0.0.1', '::1')

@app.before_request
def _antes_de_cada_requisicao():
    if request.endpoint == 'static':
        return None
    credenciais.definir_contexto(credenciais.buscar(session.get('cred_token')))   # conta de rede desta sessão (ou None)
    if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
        # Defesa extra contra CSRF: o navegador envia Origin em pedidos de outro site; se não for o nosso, recusa.
        origem = request.headers.get('Origin')
        if origem and urlsplit(origem).netloc != request.host:
            return jsonify({"ok": False, "erro": "Origem da requisição não permitida."}), 403
    if setup_pendente():
        if request.path in ('/setup', '/api/setup/admin'):
            return None
        if request.path.startswith('/api/'):
            return jsonify({"ok": False, "erro": "Configure o sistema primeiro (/setup)."}), 503
        return redirect(url_for('setup_page'))
    # Opcional: exigir login também para visualizar (configurado no assistente)
    if cfg('exigir_login_visualizar') == '1' and not session.get('usuario'):
        if request.endpoint in ('login_page', 'logout'):
            return None
        return _nao_autorizado(401)
    return None

@app.after_request
def _cabecalhos_seguranca(resp):
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    resp.headers.setdefault('Referrer-Policy', 'same-origin')
    if request.path.startswith('/api/'):
        resp.headers.setdefault('Cache-Control', 'no-store')
    return resp

# ==============================================================================
# 4b. ASSISTENTE DE PRIMEIRO ACESSO (/setup) E CONFIGURAÇÕES
# ==============================================================================
@app.route('/setup')
def setup_page():
    if setup_pendente():
        if not _eh_local():
            return Response("Configuração inicial: abra o sistema no próprio computador onde ele foi instalado "
                            "(http://127.0.0.1:%d/setup). Por segurança ela não pode ser feita pela rede." % env_int('MONITOR_PORT', 5050),
                            status=403, mimetype='text/plain; charset=utf-8')
        return render_template('setup.html', modo='inicial', porta=env_int('MONITOR_PORT', 5050))
    if session.get('papel') == 'admin':
        return render_template('setup.html', modo='reconfigurar', porta=env_int('MONITOR_PORT', 5050))
    return redirect(url_for('login_page', next='/setup'))

@app.route('/api/setup/admin', methods=['POST'])
def api_setup_admin():
    global _setup_pendente
    if not setup_pendente() or not _eh_local():
        return jsonify({"ok": False, "erro": "A configuração inicial já foi feita (ou não é permitida a partir desta máquina)."}), 403
    d = body()
    try:
        db.criar_usuario(limpa(d.get('usuario'), 40), str(d.get('senha') or ''), 'admin')
    except ValueError as e:
        return erro_json(e, 'erro')
    db.set_config('org_name', limpa(d.get('org_name'), 100))
    db.set_config('exigir_login_visualizar', '1' if d.get('exigir_login_visualizar', True) else '0')
    _setup_pendente = False
    session.clear()
    session['usuario'] = limpa(d.get('usuario'), 40)
    session['papel'] = 'admin'
    session.permanent = True
    db.auditar(session['usuario'], 'setup_inicial', 'administrador criado')
    return jsonify({"ok": True})

@app.route('/api/setup/rede', methods=['POST'])
@admin_required
def api_setup_rede():
    d = body()
    sufixo = limpa(d.get('dns_suffix'), 100).strip('.')
    if sufixo and not re.match(r'^[A-Za-z0-9][A-Za-z0-9.\-]*$', sufixo):
        return jsonify({"ok": False, "erro": "Sufixo DNS inválido."})
    db.set_config('dns_suffix', sufixo)
    teste = limpa(d.get('host_teste'), 255)
    if not teste:
        return jsonify({"ok": True, "detalhe": "Salvo (nenhum host de teste informado)."})
    if not validate_hostname(teste):
        return jsonify({"ok": False, "erro": "Nome de host de teste inválido."})
    ip = resolve_ip(teste)
    if ip:
        return jsonify({"ok": True, "detalhe": f"{teste} resolveu para {ip}."})
    return jsonify({"ok": False, "erro": f"Não foi possível resolver '{teste}'. Confira o nome, o DNS e o sufixo."})

def _mensagem_erro_windows(e):
    codigo = getattr(e, 'winerror', None)
    mapa = {
        5: "Acesso negado: a conta é válida, mas NÃO é administradora nesta máquina.",
        1326: "Usuário ou senha incorretos.",
        1327: "A conta tem restrição de horário/estação de trabalho.",
        1331: "A conta está desativada.",
        1909: "A conta está bloqueada. Procure o administrador do domínio.",
        1385: "A conta não tem permissão de logon nesta máquina.",
        53: "Máquina não encontrada pela rede (nome/IP errado, desligada ou compartilhamento administrativo bloqueado).",
        67: "Compartilhamento administrativo (ADMIN$) indisponível nesta máquina.",
        1219: "Já existe uma conexão com outro usuário para esta máquina.",
        1311: "Nenhum servidor de domínio disponível para validar a conta.",
        1355: "Domínio não encontrado. Use o formato DOMINIO\\usuario ou usuario@dominio.",
    }
    return mapa.get(codigo) or str(e)[:160]

def _eh_esta_maquina(host, ip):
    """True se o alvo é o próprio servidor (loopback, nome ou qualquer IP desta máquina)."""
    if ip.startswith('127.') or host.strip().lower() in ('localhost', socket.gethostname().lower()):
        return True
    try:
        return ip in set(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        return False

def _checar_credencial(usuario, senha):
    """Etapa 1: usuário e senha são válidos na rede? (logon de rede, uma única tentativa). Retorna (ok, detalhe)."""
    import win32con
    import win32security
    dominio, nome = credenciais._dividir(usuario)
    if not dominio and '@' not in nome:
        dominio = '.'          # nome simples = conta local desta máquina
    try:
        token = win32security.LogonUser(nome, dominio, senha, win32con.LOGON32_LOGON_NETWORK, win32con.LOGON32_PROVIDER_DEFAULT)
        token.Close()
        return True, "Usuário e senha válidos na rede"
    except Exception as e:
        return False, _mensagem_erro_windows(e)

def _candidatos_validacao(preferido=''):
    """Máquinas usadas no teste interno de administrador: a da ação (se houver) e PCs do inventário que estejam ligados."""
    lista = []
    p = sem_prefixo_pc(preferido or '')
    if p and validate_hostname(p):
        ip = resolve_ip(p)
        if ip and not _eh_esta_maquina(p, ip):
            lista.append(p)
    ligados = [it['host'] for andares in db.get_all_data().values() for itens in andares.values() for it in itens
               if it['tipo'] == 'PC' and STATUS_REALTIME.get(it['host']) and validate_hostname(sem_prefixo_pc(it['host']))]
    random.shuffle(ligados)
    for h in ligados:
        if len(lista) >= 4:
            break
        h = sem_prefixo_pc(h)
        ip = resolve_ip(h)
        if ip and h not in lista and not _eh_esta_maquina(h, ip):
            lista.append(h)
    return lista

def _tentar_admin(host, cred):
    """Abre o compartilhamento administrativo (ADMIN$): só administradores conseguem. Somente leitura."""
    try:
        with credenciais.como_usuario(cred):
            os.stat(rf"\\{host}\ADMIN$\System32")
        return host, True, ''
    except Exception as e:
        return host, False, _mensagem_erro_windows(e)

def validar_conta_rede(usuario, senha, host_preferido=''):
    """Validação OBRIGATÓRIA, feita pelo próprio sistema (sem pedir máquina ao usuário).
    Retorna (ok, passos, tentou_senha_errada)."""
    cred = {"usuario": usuario, "senha": senha}
    passos = []

    ok_cred, detalhe = _checar_credencial(usuario, senha)
    passos.append({"nome": "Usuário e senha na rede", "ok": ok_cred, "detalhe": detalhe})
    if not ok_cred:
        return False, passos, ('incorretos' in detalhe or 'bloqueada' in detalhe)

    candidatos = _candidatos_validacao(host_preferido)
    if not candidatos:
        passos.append({"nome": "Permissão de administrador na rede", "ok": False,
                       "detalhe": "Não há nenhuma máquina disponível para provar a permissão (nenhum PC do inventário está ligado). "
                                  "Cadastre equipamentos ou tente quando houver um PC ligado."})
        return False, passos, False

    executor = ThreadPoolExecutor(max_workers=len(candidatos))
    pendentes = {executor.submit(_tentar_admin, h, cred) for h in candidatos}
    prazo = time.time() + 12
    resultados = []
    sucesso = None
    while pendentes and sucesso is None and time.time() < prazo:
        feitos, pendentes = wait(pendentes, timeout=max(0.1, prazo - time.time()), return_when=FIRST_COMPLETED)
        for f in feitos:
            r = f.result()
            resultados.append(r)
            if r[1] and sucesso is None:
                sucesso = r[0]
    executor.shutdown(wait=False)

    if sucesso:
        passos.append({"nome": "Permissão de administrador na rede", "ok": True,
                       "detalhe": f"Confirmada (acesso administrativo aberto em {sucesso}; teste somente leitura)"})
        return True, passos, False
    negadas = [h for h, ok, d in resultados if 'NÃO é administradora' in d]
    if negadas:
        detalhe = f"A conta é válida, mas NÃO é administradora nas máquinas testadas ({', '.join(negadas)})."
    else:
        detalhe = "Nenhuma das máquinas testadas respondeu ao acesso administrativo (desligadas ou SMB bloqueado no firewall)."
    passos.append({"nome": "Permissão de administrador na rede", "ok": False, "detalhe": detalhe})
    return False, passos, False

_rede_falhas = {}    # usuário do sistema -> (qtde, instante): protege a conta de domínio contra bloqueio por engano
_lock_rede = threading.Lock()

@app.route('/api/rede/entrar', methods=['POST'])
@login_required
def api_rede_entrar():
    """Valida a conta de REDE da pessoa (obrigatório) e a mantém só na memória desta sessão (nada é salvo em disco)."""
    dono = usuario_atual()
    with _lock_rede:
        qtde, ts = _rede_falhas.get(dono, (0, 0.0))
        if qtde >= 5 and time.time() - ts < 600:
            return jsonify({"ok": False, "erro": "Muitas tentativas com credencial errada. Aguarde 10 minutos para não bloquear a sua conta de rede."}), 429
        if qtde and time.time() - ts >= 600:
            _rede_falhas.pop(dono, None)
    d = body()
    usuario = credenciais.normalizar_usuario(limpa(d.get('usuario'), 100))
    senha = str(d.get('senha') or '')[:200]
    preferido = limpa(d.get('host'), 255)      # máquina da ação em andamento (opcional; não é perguntada ao usuário)
    if not usuario or not senha:
        return jsonify({"ok": False, "erro": "Informe o usuário (ex.: DOMINIO\\usuario) e a senha."})
    ok, passos, senha_errada = validar_conta_rede(usuario, senha, preferido)
    if not ok:
        if senha_errada:
            with _lock_rede:
                qtde, _ = _rede_falhas.get(dono, (0, 0.0))
                _rede_falhas[dono] = (qtde + 1, time.time())
        db.auditar(dono, 'rede_login_falhou', usuario)
        return jsonify({"ok": False, "passos": passos, "erro": "A conta não foi validada. Sem a validação a gestão remota fica bloqueada."})
    with _lock_rede:
        _rede_falhas.pop(dono, None)
    credenciais.descartar(session.get('cred_token'))
    session['cred_token'] = credenciais.guardar(dono, usuario, senha, preferido)
    db.auditar(dono, 'rede_login', f"{usuario} validada como administradora")
    return jsonify({"ok": True, "passos": passos, "usuario": usuario,
                    "expira_em_min": credenciais.TTL_SEGUNDOS // 60})

@app.route('/api/rede/estado')
def api_rede_estado():
    cred = credenciais.atual()
    return jsonify({"ativo": bool(cred), "usuario": cred['usuario'] if cred else None,
                    "segundos": credenciais.segundos_restantes(session.get('cred_token')) if cred else 0})

@app.route('/api/rede/sair', methods=['POST'])
def api_rede_sair():
    credenciais.descartar(session.pop('cred_token', None))
    return jsonify({"ok": True})

@app.route('/api/setup/testar_ssh', methods=['POST'])
@admin_required
def api_setup_testar_ssh():
    d = body()
    ip = limpa(d.get('ip'), 64)
    usuario = limpa(d.get('usuario'), 60)
    senha = str(d.get('senha') or '')[:200]
    if not _ip_valido(ip):
        return jsonify({"ok": False, "erro": "Informe o IP do painel (ex.: 10.0.0.50)."})
    if not usuario or not senha:
        return jsonify({"ok": False, "erro": "Informe o usuário e a senha SSH."})
    r = ssh_executar(ip, 'uptime', timeout=15, usuario=usuario, senha=senha)
    if not r.get('ok'):
        return jsonify({"ok": False, "erro": r.get('erro', 'Falha no SSH')})
    if d.get('salvar'):
        db.set_config('painel_ssh_user', usuario)
        db.set_config('painel_ssh_pass', senha, segredo=True)
        db.auditar(usuario_atual(), 'config', 'credenciais SSH dos painéis salvas')
    return jsonify({"ok": True, "detalhe": (r.get('output') or '').strip()[:200], "salvo": bool(d.get('salvar'))})

@app.route('/api/setup/requisitos')
@admin_required
def api_setup_requisitos():
    return jsonify({"ok": True, "itens": requisitos.verificar()})

@app.route('/api/setup/baixar', methods=['POST'])
@admin_required
def api_setup_baixar():
    chave = limpa(body().get('ferramenta'), 30)
    if chave not in requisitos.FERRAMENTAS:
        return jsonify({"ok": False, "erro": "Ferramenta desconhecida."})
    resultado = requisitos.baixar_ferramenta(chave)
    db.auditar(usuario_atual(), 'download_ferramenta', f"{chave}: {'ok' if resultado.get('ok') else 'falhou'}")
    return jsonify(resultado)

@app.route('/api/setup/concluir', methods=['POST'])
@admin_required
def api_setup_concluir():
    db.set_config('setup_concluido', '1')
    return jsonify({"ok": True})

_CONFIG_PERMITIDAS = {
    'org_name': False, 'dns_suffix': False, 'exigir_login_visualizar': False, 'popup_title': False,
    'file_server': False, 'painel_ssh_user': False, 'painel_ssh_pass': True,
    'equipe_sobre': False,
}

def equipe_sobre():
    """Usuários escolhidos para aparecer em 'Sobre → Equipe' (só contas que ainda existem e estão ativas)."""
    escolhidos = [n.strip() for n in cfg('equipe_sobre').split(',') if n.strip()]
    ativos = {u['usuario'] for u in db.listar_usuarios() if u['ativo']}
    return [n for n in escolhidos if n in ativos]

@app.route('/api/config', methods=['GET', 'POST'])
@admin_required
def api_config():
    if request.method == 'GET':
        return jsonify({"ok": True, "config": {k: v for k, v in db.get_config_publica().items() if k in _CONFIG_PERMITIDAS}})
    d = body()
    for chave, segredo in _CONFIG_PERMITIDAS.items():
        if chave not in d:
            continue
        valor = str(d[chave] if d[chave] is not None else '')[:300]
        if segredo and not valor:
            continue            # campo de senha em branco = manter a atual
        db.set_config(chave, valor, segredo=segredo)
    for chave in d.get('limpar', []) if isinstance(d.get('limpar'), list) else []:
        if chave in _CONFIG_PERMITIDAS:
            db.set_config(chave, '', segredo=_CONFIG_PERMITIDAS[chave])
    db.auditar(usuario_atual(), 'config', 'configurações alteradas')
    return jsonify({"ok": True})

# ---- usuários ----
@app.route('/api/usuarios', methods=['GET', 'POST'])
@admin_required
def api_usuarios():
    if request.method == 'GET':
        return jsonify({"ok": True, "usuarios": db.listar_usuarios(), "eu": usuario_atual()})
    d = body()
    try:
        db.criar_usuario(limpa(d.get('usuario'), 40), str(d.get('senha') or ''), limpa(d.get('papel'), 20))
    except ValueError as e:
        return erro_json(e)
    db.auditar(usuario_atual(), 'usuario_criado', f"{limpa(d.get('usuario'), 40)} ({limpa(d.get('papel'), 20)})")
    return jsonify({"ok": True})

@app.route('/api/usuarios/<int:uid>/alterar', methods=['POST'])
@admin_required
def api_usuario_alterar(uid):
    d = body()
    try:
        db.alterar_usuario(uid, papel=d.get('papel'), senha=d.get('senha') or None,
                           ativo=d.get('ativo') if 'ativo' in d else None)
    except ValueError as e:
        return erro_json(e)
    db.auditar(usuario_atual(), 'usuario_alterado', f"id {uid}")
    return jsonify({"ok": True})

@app.route('/api/usuarios/<int:uid>/excluir', methods=['POST'])
@admin_required
def api_usuario_excluir(uid):
    try:
        db.excluir_usuario(uid)
    except ValueError as e:
        return erro_json(e)
    db.auditar(usuario_atual(), 'usuario_excluido', f"id {uid}")
    return jsonify({"ok": True})

@app.route('/api/auditoria')
@admin_required
def api_auditoria():
    return jsonify({"ok": True, "itens": db.listar_auditoria(200)})

# ---- backup / CSV ----
@app.route('/admin/backup')
@admin_required
def admin_backup():
    import tempfile
    nome = f"monitor_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.db"
    tmp = os.path.join(tempfile.gettempdir(), nome)
    db.backup(tmp)
    db.auditar(usuario_atual(), 'backup', nome)
    resp = send_file(tmp, as_attachment=True, download_name=nome)
    resp.call_on_close(lambda: os.path.exists(tmp) and os.unlink(tmp))
    return resp

@app.route('/api/exportar_csv')
@admin_required
def api_exportar_csv():
    saida = io.StringIO()
    w = csv.writer(saida, delimiter=';')
    w.writerow(['unidade', 'andar', 'setor', 'host', 'tipo'])
    for linha in db.exportar_linhas():
        w.writerow(linha)
    return Response('﻿' + saida.getvalue(), mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="inventario.csv"'})

_ALIASES_CSV = {
    'unidade': ('unidade',), 'andar': ('andar', 'local', 'local/andar'), 'setor': ('setor',),
    'host': ('host', 'hostname', 'hostname/ip', 'ip', 'nome'), 'tipo': ('tipo',),
}

@app.route('/api/importar_csv', methods=['POST'])
@admin_required
def api_importar_csv():
    texto = str(body().get('csv') or '').lstrip('﻿')[:2_000_000]
    if not texto.strip():
        return jsonify({"ok": False, "msg": "Arquivo vazio."})
    try:
        dialeto = csv.Sniffer().sniff(texto[:4096], delimiters=',;\t')
    except csv.Error:
        dialeto = csv.excel
    linhas = list(csv.reader(io.StringIO(texto), dialeto))
    cab = [c.strip().lower() for c in linhas[0]]
    idx = {}
    for campo, nomes in _ALIASES_CSV.items():
        for n in nomes:
            if n in cab:
                idx[campo] = cab.index(n)
                break
    if 'unidade' not in idx or 'host' not in idx:
        return jsonify({"ok": False, "msg": "A 1ª linha deve ser o cabeçalho com as colunas: unidade;andar;setor;host;tipo"})

    def col(linha, campo):
        i = idx.get(campo)
        return linha[i].strip() if i is not None and i < len(linha) else ''

    dados = [(col(l, 'unidade'), col(l, 'andar'), col(l, 'setor'), col(l, 'host'), col(l, 'tipo')) for l in linhas[1:] if any(l)]
    if len(dados) > 20000:
        return jsonify({"ok": False, "msg": "Linhas demais (máx. 20.000)."})
    inseridos, ignorados = db.importar_linhas(dados)
    db.auditar(usuario_atual(), 'importar_csv', f"{inseridos} novos, {ignorados} ignorados")
    return jsonify({"ok": True, "inseridos": inseridos, "ignorados": ignorados})

# ==============================================================================
# 4c. INVENTÁRIO (unidades, andares, equipamentos), NOTAS E HISTÓRICO
# ==============================================================================
@app.route('/api/adicionar_item', methods=['POST'])
@admin_required
def api_adicionar():
    d = body()
    try:
        db.adicionar_equipamento(limpa(d.get('unit')), limpa(d.get('floor')), limpa(d.get('sector')),
                                 limpa(d.get('host')), limpa(d.get('type') or 'PC', 50))
    except ValueError as e:
        return erro_json(e)
    db.auditar(usuario_atual(), 'equipamento_adicionado', f"{limpa(d.get('unit'))} / {limpa(d.get('host'))}")
    return jsonify({"ok": True})

@app.route('/api/editar_item', methods=['POST'])
@admin_required
def api_edit():
    d = body()
    try:
        db.editar_equipamento(limpa(d.get('unit')), limpa(d.get('old_host')), limpa(d.get('old_sector')),
                              limpa(d.get('new_floor')), limpa(d.get('new_sector')), limpa(d.get('new_host')))
    except ValueError as e:
        return erro_json(e)
    db.auditar(usuario_atual(), 'equipamento_editado', f"{limpa(d.get('old_host'))} -> {limpa(d.get('new_host'))}")
    return jsonify({"ok": True})

@app.route('/api/delete_item', methods=['POST'])
@admin_required
def api_delete():
    d = body()
    try:
        db.excluir_equipamento(limpa(d.get('unit')), limpa(d.get('host')), limpa(d.get('sector')))
    except ValueError as e:
        return erro_json(e)
    db.auditar(usuario_atual(), 'equipamento_excluido', f"{limpa(d.get('unit'))} / {limpa(d.get('host'))}")
    return jsonify({"ok": True})

@app.route('/api/adicionar_andar_existente', methods=['POST'])
@admin_required
def api_adicionar_andar():
    d = body()
    try:
        db.adicionar_andar(limpa(d.get('unit')), limpa(d.get('floor_name')), limpa(d.get('position_ref')) or 'END')
    except ValueError as e:
        return erro_json(e)
    db.auditar(usuario_atual(), 'andar_criado', f"{limpa(d.get('unit'))} / {limpa(d.get('floor_name'))}")
    return jsonify({"ok": True})

def _linhas_para_itens(texto, andar, tipo):
    """Converte linhas 'Setor HOSTNAME' (ou só 'HOSTNAME') do assistente de Nova Unidade."""
    itens = []
    for linha in str(texto or '').splitlines():
        partes = linha.strip().split()
        if not partes:
            continue
        itens.append((andar, limpa(' '.join(partes[:-1])) or 'Indefinido', limpa(partes[-1]), tipo))
    return itens

@app.route('/api/criar_solicitacao', methods=['POST'])
@admin_required
def api_criar_unidade():
    """Cria uma nova unidade (com andares e equipamentos) a partir do assistente 'Nova Unidade'."""
    d = body()
    nome = limpa(d.get('nome'), 100)
    floors = d.get('floors')
    if not nome:
        return jsonify({"ok": False, "msg": "Informe o nome da unidade."})
    if not isinstance(floors, list) or not floors:
        return jsonify({"ok": False, "msg": "Informe ao menos um andar."})
    itens = []
    for f in floors[:50]:
        if not isinstance(f, dict):
            continue
        andar = limpa(f.get('name')) or 'Geral'
        itens += _linhas_para_itens(f.get('pcs'), andar, 'PC')
        itens += _linhas_para_itens(f.get('imps'), andar, 'IMP')
    if len(itens) > 5000:
        return jsonify({"ok": False, "msg": "Equipamentos demais (máx. 5000)."})
    try:
        db.criar_unidade(nome)
        for f in floors[:50]:
            if isinstance(f, dict):
                try: db.adicionar_andar(nome, limpa(f.get('name')) or 'Geral')
                except ValueError: pass
        db.importar_linhas([(nome, a, s, h, t) for a, s, h, t in itens], ignorar_duplicados=False)
    except ValueError as e:
        return erro_json(e)
    db.auditar(usuario_atual(), 'unidade_criada', f"{nome} ({len(itens)} equipamentos)")
    return jsonify({"ok": True})

@app.route('/api/unidade/renomear', methods=['POST'])
@admin_required
def api_unidade_renomear():
    d = body()
    try:
        db.renomear_unidade(limpa(d.get('antigo'), 100), limpa(d.get('novo'), 100))
    except ValueError as e:
        return erro_json(e)
    db.auditar(usuario_atual(), 'unidade_renomeada', f"{limpa(d.get('antigo'))} -> {limpa(d.get('novo'))}")
    return jsonify({"ok": True})

@app.route('/api/unidade/excluir', methods=['POST'])
@admin_required
def api_unidade_excluir():
    nome = limpa(body().get('nome'), 100)
    try:
        db.excluir_unidade(nome)
    except ValueError as e:
        return erro_json(e)
    db.auditar(usuario_atual(), 'unidade_excluida', nome)
    return jsonify({"ok": True})

@app.route('/api/unidade/detalhe')
@admin_required
def api_unidade_detalhe():
    try:
        return jsonify({"ok": True, "andares": db.detalhe_unidade(limpa(request.args.get('nome'), 100))})
    except ValueError as e:
        return erro_json(e)

@app.route('/api/andar/renomear', methods=['POST'])
@admin_required
def api_andar_renomear():
    d = body()
    try:
        db.renomear_andar(limpa(d.get('unit'), 100), limpa(d.get('antigo'), 100), limpa(d.get('novo'), 100))
    except ValueError as e:
        return erro_json(e)
    db.auditar(usuario_atual(), 'andar_renomeado', f"{limpa(d.get('unit'))}: {limpa(d.get('antigo'))} -> {limpa(d.get('novo'))}")
    return jsonify({"ok": True})

@app.route('/api/andar/excluir', methods=['POST'])
@admin_required
def api_andar_excluir():
    d = body()
    try:
        qtd = db.excluir_andar(limpa(d.get('unit'), 100), limpa(d.get('nome'), 100))
    except ValueError as e:
        return erro_json(e)
    db.auditar(usuario_atual(), 'andar_excluido', f"{limpa(d.get('unit'))} / {limpa(d.get('nome'))} ({qtd} equipamentos)")
    return jsonify({"ok": True, "removidos": qtd})

@app.route('/api/equipamento/salvar', methods=['POST'])
@admin_required
def api_equipamento_salvar():
    d = body()
    try:
        db.salvar_equipamento(int(d.get('id') or 0), limpa(d.get('andar'), 100), limpa(d.get('setor')),
                              limpa(d.get('host')), limpa(d.get('tipo'), 10))
    except (ValueError, TypeError) as e:
        return erro_json(e)
    db.auditar(usuario_atual(), 'equipamento_editado', f"id {d.get('id')}: {limpa(d.get('host'))}")
    return jsonify({"ok": True})

@app.route('/api/equipamento/excluir', methods=['POST'])
@admin_required
def api_equipamento_excluir():
    d = body()
    try:
        db.excluir_equipamento_id(int(d.get('id') or 0))
    except (ValueError, TypeError) as e:
        return erro_json(e)
    db.auditar(usuario_atual(), 'equipamento_excluido', f"id {d.get('id')}")
    return jsonify({"ok": True})

@app.route('/api/unidades')
@admin_required
def api_unidades():
    dados = db.get_all_data()
    return jsonify({"ok": True, "unidades": [
        {"nome": u, "andares": len(a), "equipamentos": sum(len(i) for i in a.values())} for u, a in dados.items()]})

@app.route('/api/scanner/importar', methods=['POST'])
@admin_required
def api_scanner_importar():
    d = body()
    unit = limpa(d.get('unit'))
    items = d.get('items', [])
    if not unit or not isinstance(items, list) or not items:
        return jsonify({"ok": False, "msg": "Unidade ou itens inválidos"})
    if len(items) > 500:
        return jsonify({"ok": False, "msg": "Itens demais (máx. 500)"})
    try:
        try:
            db.criar_unidade(unit)   # a unidade pode ser nova (assistente de primeiro acesso)
        except ValueError:
            pass
        linhas = []
        for item in items:
            if not isinstance(item, dict): continue
            ident = limpa(item.get('hostname'))
            if limpa(item.get('type'), 30) == 'Impressora': ident = limpa(item.get('ip'))
            if ident and '.' in ident and not ident.replace('.', '').isdigit(): ident = ident.split('.')[0]
            if not ident: continue
            linhas.append((unit, 'Geral', limpa(item.get('sector', 'Geral')) or 'Geral', ident, limpa(item.get('type'), 30)))
        if not linhas:
            return jsonify({"ok": False, "msg": "Nenhum item válido para importar"})
        inseridos, ignorados = db.importar_linhas(linhas)
    except ValueError as e:
        return erro_json(e)
    db.auditar(usuario_atual(), 'scanner_importar', f"{unit}: {inseridos} novos")
    return jsonify({"ok": True, "inseridos": inseridos, "ignorados": ignorados})

# ---- notas por equipamento ----
@app.route('/api/notas/<int:eid>')
def api_notas(eid):
    host = db.host_do_equipamento(eid)
    return jsonify({"ok": True, "host": host, "notas": db.listar_notas(eid),
                    "pode_escrever": session.get('papel') in ('admin', 'tecnico'),
                    "eu": session.get('usuario'), "admin": session.get('papel') == 'admin'})

@app.route('/api/notas/<int:eid>/adicionar', methods=['POST'])
@editor_required
def api_nota_adicionar(eid):
    try:
        db.adicionar_nota(eid, usuario_atual(), body().get('texto'))
    except ValueError as e:
        return erro_json(e)
    return jsonify({"ok": True})

@app.route('/api/notas/excluir/<int:nid>', methods=['POST'])
@editor_required
def api_nota_excluir(nid):
    try:
        db.excluir_nota(nid, usuario_atual(), session.get('papel') == 'admin')
    except ValueError as e:
        return erro_json(e)
    return jsonify({"ok": True})

# ---- histórico de disponibilidade ----
@app.route('/api/historico/<path:host>')
def api_historico(host):
    if not validate_hostname(sem_prefixo_pc(host)):
        return jsonify({"ok": False, "eventos": [], "disponibilidade_7d": None})
    r = db.historico_host(host)
    r['ok'] = True
    return jsonify(r)


# ==============================================================================
# 5. FERRAMENTA: SCANNER DE REDE (CORRIGIDO)
# ==============================================================================
def check_port_open(ip, port, timeout=0.3):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((ip, port)) == 0
    except OSError:
        return False

def get_printer_details_scanner(ip):
    model = "Impressora de Rede"
    brand = "Desc."
    urls = [f"http://{ip}", f"https://{ip}"]
    for url in urls:
        try:
            r = requests.get(url, timeout=1.0, verify=False)
            if r.status_code == 200:
                content = r.text.upper()
                for b in PRINTER_BRANDS:
                    if b in content:
                        brand = b
                        model = f"Impressora {b}"
                        break
                if brand != "Desc.": break
        except: continue
    return {"modelo": model, "brand": brand}

def scan_target_logic(target_ip):
    # 1. Ping
    if not ping_individual(target_ip)[1]:
        if not (check_port_open(target_ip, 135) or check_port_open(target_ip, 9100)):
            return None

    # 2. Resolve Nome
    try: hostname = socket.gethostbyaddr(target_ip)[0]
    except: hostname = target_ip

    device_type = "Outro"
    details = {}
    
    # 3. Identificação
    if check_port_open(target_ip, 9100) or check_port_open(target_ip, 515):
        device_type = "Impressora"
        details = get_printer_details_scanner(target_ip)
    elif check_port_open(target_ip, 445) or check_port_open(target_ip, 135):
        device_type = "Computador"
        try:
            w = get_wmi_info(target_ip)
            if w['modelo'] and w['modelo'] != 'N/D': details['modelo'] = w['modelo']
        except: pass
    else:
        if "PC" in hostname.upper() or "DESK" in hostname.upper(): device_type = "Computador"
        else: details = {"modelo": "Dispositivo IP"}

    return {
        "ip": target_ip, 
        "hostname": hostname, 
        "type": device_type, 
        "details": details,
        "registered": False, 
        "location": None
    }

@app.route('/api/scanner/scan', methods=['POST'])
@admin_required
def api_scanner_scan():
    try:
        d = body()
        try:
            network = ipaddress.ip_network(limpa(d.get('ip_range', ''), 50), strict=False)
        except ValueError:
            return jsonify({'error': 'Range inválido. Exemplo: 10.0.1.0/24'})
        if network.version != 4 or not network.is_private:
            return jsonify({'error': 'Só é permitido escanear redes privadas IPv4 (10.x, 172.16-31.x, 192.168.x)'})
        if network.num_addresses > 512: return jsonify({'error': 'Range muito grande (Max /23)'})
        hosts = list(network.hosts())

        # Mapa de Registrados
        registered_map = {}
        for unidade, andares in db.get_all_data().items():
            for andar, items in andares.items():
                for item in items:
                    h = item.get('host', '').upper().strip()
                    if h: registered_map[h] = f"{unidade} - {andar}"
        
        results = []
        with ThreadPoolExecutor(max_workers=60) as executor:
            # cada tarefa leva uma cópia do contexto (para o WMI do scanner usar a conta de rede da pessoa, se houver)
            futures = [executor.submit(contextvars.copy_context().run, scan_target_logic, str(ip)) for ip in hosts]
            for f in futures:
                try:
                    res = f.result()
                except Exception as e_scan:
                    print(f">>> [SCANNER] Falha em um host: {e_scan}")
                    continue
                if res:
                    # Lógica de Prioridade IP para Identificação
                    key_ip = res['ip'].strip().upper()
                    key_host = res['hostname'].upper().strip()
                    
                    if '.' in key_host and not key_host.replace('.','').isdigit():
                        key_short = key_host.split('.')[0]
                    else:
                        key_short = key_host

                    if key_ip in registered_map:
                        res['registered'] = True
                        res['location'] = registered_map[key_ip]
                    elif key_host in registered_map:
                        res['registered'] = True
                        res['location'] = registered_map[key_host]
                    elif key_short in registered_map:
                        res['registered'] = True
                        res['location'] = registered_map[key_short]
                        
                    results.append(res)
                
        return jsonify(results)
    except Exception as e:
        return jsonify({'error': str(e)})

# ==============================================================================
# 6. FUNÇÕES WMI E PSEXEC
# ==============================================================================
def sem_prefixo_pc(host):
    host = str(host).strip()
    return host[3:] if host.upper().startswith("PC-") else host

def get_wmi_info(host):
    host = sem_prefixo_pc(host)
    info = {'modelo': 'N/D', 'cpu': 'N/D', 'ram_total': 'N/D', 'uptime': 'N/D', 'serial': 'N/D', 'user': 'N/D', 'os': 'N/D', 'total_gb': 0, 'free_gb': 0, 'percent': 0, 'error': False}
    
    online = STATUS_REALTIME.get(host)
    if online is None: _, online = ping_individual(host)
    if not online: info['error'] = True; return info

    pythoncom.CoInitialize()
    try:
        target = None if host in ['127.0.0.1', 'localhost'] else host
        c = credenciais.wmi_conectar(target)

        cs = c.Win32_ComputerSystem()[0]
        info['modelo'] = (cs.Model or 'N/D').strip()
        info['user'] = (cs.UserName or 'N/D').strip()[:30]
        try: info['ram_total'] = f"{float(cs.TotalPhysicalMemory)/(1024**3):.1f} GB"
        except: pass

        try:
            cpu = c.Win32_Processor()[0].Name
            info['cpu'] = (cpu or 'N/D').replace("Intel(R) Core(TM) ", "").replace("CPU ", "").strip()
        except: pass

        try:
            serial = c.Win32_BIOS()[0].SerialNumber
            info['serial'] = (serial or 'N/D').strip()
        except: pass

        try:
            for d in c.Win32_LogicalDisk(DeviceID="C:"):
                size = float(d.Size or 0)
                free = float(d.FreeSpace or 0)
                info['total_gb'] = f"{size/(1024**3):.1f}"
                info['free_gb'] = f"{free/(1024**3):.1f}"
                info['percent'] = ((size-free)/size)*100 if size else 0
                break
        except: pass

        try:
            ositem = c.Win32_OperatingSystem()[0]
            base = (ositem.Caption or 'N/D').replace("Microsoft ", "").strip()
            build = ositem.BuildNumber or ""
            ver = WINDOWS_VERSIONS.get(build, "")
            info['os'] = f"Win {ver} {base}" if ver else base
            try:
                dt = datetime.strptime(str(ositem.LastBootUpTime).split('.')[0], "%Y%m%d%H%M%S")
                info['uptime'] = f"{(datetime.now() - dt).days} dias"
            except: pass
        except: pass
    except: info['error'] = True
    finally: pythoncom.CoUninitialize()
    return info

def _decode_wmi_chars(arr):
    return "".join(chr(i) for i in arr if i != 0).strip() if arr else ""

def listar_monitores_wmi(target):
    """Lê os monitores via WMI (root\\wmi). `target` None = máquina local. Levanta exceção se o WMI falhar."""
    monitors = []
    pythoncom.CoInitialize()
    try:
        c = credenciais.wmi_conectar(target, namespace='root\\wmi')
        for m in c.WmiMonitorID():
            modelo = _decode_wmi_chars(m.UserFriendlyName)
            serial = _decode_wmi_chars(m.SerialNumberID)
            fab = _decode_wmi_chars(m.ManufacturerName)
            marca = MONITOR_BRANDS.get(fab, fab) if fab else "Desconhecido"
            if not modelo: modelo = fab or "Genérico"
            if not serial: serial = "N/D"
            monitors.append({"marca": marca, "modelo": modelo, "serial_monitor": serial})
    finally:
        pythoncom.CoUninitialize()
    return monitors

def get_monitors_wmi(host):
    host = sem_prefixo_pc(host)
    target = None if host in ['127.0.0.1', 'localhost'] else host
    try:
        return listar_monitores_wmi(target)
    except Exception:
        return []

# ==============================================================================
# 7. ROTAS FLASK
# ==============================================================================
@app.route('/')
def index():
    view_data = db.get_all_data()

    tc, tc_on = 0, 0
    ti, ti_on = 0, 0
    
    if view_data:
        for unidade, andares in view_data.items():
            for lista in andares.values():
                for item in lista:
                    host = item.get('host')
                    tipo = item.get('tipo')
                    is_online = STATUS_REALTIME.get(host, False)
                    if tipo == 'PC':
                        tc += 1
                        if is_online: tc_on += 1
                    elif tipo == 'IMP': 
                        ti += 1
                        if is_online: ti_on += 1
                    item['status_class'] = 'online' if is_online else 'offline' 

    return render_template('index.html', all_data=view_data, 
                           t_pc=tc, t_pc_on=tc_on, t_pc_off=tc-tc_on,
                           t_imp=ti, t_imp_on=ti_on, t_imp_off=ti-ti_on,
                           comandos_painel=COMANDOS_PAINEL,
                           org_name=cfg('org_name'), equipe=equipe_sobre(),
                           usuario=session.get('usuario'), papel=session.get('papel') or '')

@app.route('/api/check/<path:host>')
def api_check(host):
    if not validate_hostname(sem_prefixo_pc(host)):
        return jsonify({"online": False, "ip": None})
    is_online = STATUS_REALTIME.get(host)
    if is_online is None: _, is_online = ping_individual(host)
    return jsonify({"online": is_online, "ip": resolve_ip(host)})

@app.route('/api/disk/<path:host>')
def api_disk(host):
    if not validate_hostname(sem_prefixo_pc(host)):
        return jsonify({"error": True}), 400
    return jsonify(get_wmi_info(host))

@app.route('/api/details/<path:host>')
def api_details(host):
    if not validate_hostname(sem_prefixo_pc(host)):
        return jsonify({"error": True, "monitors": []}), 400
    info = get_wmi_info(host)
    if not info['error']: info['monitors'] = get_monitors_wmi(host)
    else: info['monitors'] = []
    return jsonify(info)

def _ler_clientid_teamviewer(target):
    """ClientID do TeamViewer no registro (local ou remoto, via Remote Registry)."""
    import winreg
    try:
        with credenciais.como_usuario():       # registro remoto com a conta de rede da pessoa
            hk = winreg.ConnectRegistry(rf"\\{target}" if target else None, winreg.HKEY_LOCAL_MACHINE)
            try:
                for subkey in (r"SOFTWARE\WOW6432Node\TeamViewer", r"SOFTWARE\TeamViewer"):
                    try:
                        with winreg.OpenKey(hk, subkey) as k:
                            valor, _ = winreg.QueryValueEx(k, "ClientID")
                            return str(valor)
                    except OSError:
                        continue
            finally:
                winreg.CloseKey(hk)
    except OSError:
        return None
    return None

def _ler_texto_arquivo(caminho, limite=262144):
    try:
        with credenciais.como_usuario(), open(caminho, 'rb') as f:
            return f.read(limite).decode('utf-8', errors='replace')
    except OSError:
        return None

@app.route('/api/mapear_conexoes', methods=['POST'])
@editor_required
@rede_requerida
def api_mapear_conexoes():
    try:
        hostname = sem_prefixo_pc(limpa(body().get('hostname'), 255))
        if not hostname: return jsonify({"sucesso": False, "erro": "Sem Hostname"})
        if not validate_hostname(hostname): return jsonify({"sucesso": False, "erro": "Hostname inválido"})

        target = None if hostname in ['127.0.0.1', 'localhost'] else hostname
        output_lines = []
        remote_ids = {"anydesk": "N/D", "teamviewer": "N/D", "rustdesk": "N/D"}

        # ---------- MONITORES VIA WMI ----------
        try:
            monitores = listar_monitores_wmi(target)
            for m in monitores:
                output_lines.append(f"{'MONITOR':<35} {m['marca']} {m['modelo']}  |  {m['serial_monitor']}")
            if not monitores:
                output_lines.append(f"{'MONITOR':<35} Nenhum monitor detectado")
        except Exception as e:
            output_lines.append(f"{'MONITOR':<35} Erro WMI: {str(e)}")

        # ---------- TEAMVIEWER ID VIA REGISTRO ----------
        tv = _ler_clientid_teamviewer(target)
        if tv: remote_ids["teamviewer"] = tv
        output_lines.append(f"{'REMOTE_INFO':<35} TeamViewer | {remote_ids['teamviewer']}")

        # ---------- ANYDESK / RUSTDESK ID VIA COMPARTILHAMENTO C$ ----------
        raiz = rf"\\{target}\C$" if target else "C:"
        candidatos = {
            "anydesk": (r"ProgramData\AnyDesk\system.conf", r"ProgramData\AnyDesk\ad_svc.conf"),
            "rustdesk": (r"Windows\ServiceProfiles\LocalService\AppData\Roaming\RustDesk\config\RustDesk.toml",
                         r"ProgramData\RustDesk\config\RustDesk.toml"),
        }
        padroes = {
            "anydesk": re.compile(r'ad\.anynet\.id=(\d+)'),
            "rustdesk": re.compile(r'id\s*=\s*[\'"]?(\d{9,})[\'"]?'),
        }
        for app_remoto, arquivos in candidatos.items():
            for rel in arquivos:
                conteudo = _ler_texto_arquivo(raiz + "\\" + rel)
                if not conteudo: continue
                m = padroes[app_remoto].search(conteudo)
                if m:
                    remote_ids[app_remoto] = m.group(1)
                    break
        output_lines.append(f"{'REMOTE_INFO':<35} AnyDesk | {remote_ids['anydesk']}")
        output_lines.append(f"{'REMOTE_INFO':<35} RustDesk | {remote_ids['rustdesk']}")

        output = "\n".join(output_lines) + "\n"
        return jsonify({"sucesso": True, "output": output, "remote_ids": remote_ids})
    except Exception as e:
        return jsonify({"sucesso": False, "erro": str(e)})

@app.route('/api/power', methods=['POST'])
@admin_required
@rede_requerida
def api_power():
    try:
        d = body()
        host = sem_prefixo_pc(limpa(d.get('host'), 255))
        action = limpa(d.get('action', 'restart'), 20)
        if not host: return jsonify({"ok": False, "msg": "Sem host"})
        if not validate_hostname(host): return jsonify({"ok": False, "msg": "Hostname inválido"})
        if host.lower() in ('localhost', '127.0.0.1'):
            return jsonify({"ok": False, "msg": "Não é permitido reiniciar/desligar o próprio servidor do monitor por aqui"})
        if action not in ('restart', 'shutdown'): return jsonify({"ok": False, "msg": "Ação inválida"})
        # WMI (Win32Shutdown) com a conta de rede da própria pessoa; o hostname já foi validado.
        codigo = credenciais.desligar_ou_reiniciar(host, reiniciar=(action == 'restart'))
        db.auditar(usuario_atual(), 'energia', f"{action} em {host} (conta {credenciais.atual()['usuario']}): código {codigo}")
        return jsonify({"ok": codigo == 0, "msg": "Comando enviado" if codigo == 0 else f"O Windows recusou o comando (código {codigo})"})
    except Exception as e:
        return jsonify({"ok": False, "msg": _mensagem_erro_windows(e)})

@app.route('/api/message', methods=['POST'])
@admin_required
def api_message():
    try:
        d = body()
        host = sem_prefixo_pc(limpa(d.get('host'), 255))
        mensagem = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', limpa(d.get('message'), 500))
        if not host: return jsonify({"ok": False, "msg": "Sem host"})
        if not validate_hostname(host): return jsonify({"ok": False, "msg": "Hostname inválido"})
        if not mensagem: return jsonify({"ok": False, "msg": "Mensagem vazia"})

        TITULO = cfg('popup_title') or 'Suporte TI'

        # --- 1. Local ---
        if host in ['127.0.0.1', 'localhost']:
            session_id = _get_active_session_id()
            if session_id is None:
                return jsonify({"ok": False, "msg": "Nenhuma sessao de usuario ativa localmente"})
            ok = _wts_send_local(session_id, TITULO, mensagem)
            return jsonify({"ok": ok, "msg": "Mensagem enviada na tela" if ok else "Falha ao exibir mensagem"})

        # --- 2. Remoto (usa a conta de rede da própria pessoa) ---
        if not credenciais.atual():
            return _resposta_precisa_rede()
        # 2a. Detecta a sessao ativa (usuario logado) via WMI
        session_id = None
        alvo = resolve_ip(host) or host
        pythoncom.CoInitialize()
        try:
            c = credenciais.wmi_conectar(alvo)
            for p in c.Win32_Process(Name="explorer.exe"):
                if p.SessionId not in (0, None):
                    session_id = int(p.SessionId)
                    break
        except Exception as e:
            return jsonify({"ok": False, "msg": f"Sem acesso WMI ao {host}: {str(e)}"})
        finally:
            pythoncom.CoUninitialize()

        if session_id is None:
            return jsonify({"ok": False, "msg": f"{host} sem usuario ativo (ninguem logado na tela)."})

        # 2b. Copia o script do popup via C$ e executa localmente na maquina alvo (WMI)
        script_local = os.path.join(BASE_DIR, 'wts_send.ps1')
        if not os.path.exists(script_local):
            return jsonify({"ok": False, "msg": "Arquivo wts_send.ps1 nao encontrado junto ao monitor"})

        destino = f"\\\\{alvo}\\C$\\Windows\\Temp\\wts_send.ps1"
        try:
            # reescreve sempre o script no destino para garantir versao sem BOM
            with credenciais.como_usuario(), open(script_local, 'rb') as fsrc, open(destino, 'wb') as fdst:
                content = fsrc.read().lstrip(b'\xef\xbb\xbf')
                fdst.write(content)
        except Exception as e:
            return jsonify({"ok": False, "msg": f"Falha ao copiar popup: {str(e)}"})

        # Copia a logo junto (o WPF carrega de C:\Windows\Temp)
        logo_local = os.path.join(BASE_DIR, 'popup-logo.png')   # opcional: coloque seu logo aqui
        if os.path.exists(logo_local):
            try:
                with credenciais.como_usuario():
                    shutil.copyfile(logo_local, f"\\\\{alvo}\\C$\\Windows\\Temp\\popup-logo.png")
            except Exception:
                pass  # sem logo o popup usa o fallback

        # Codifica em Base64 para evitar problemas com aspas/encoding no CommandLine do WMI
        t_b64 = base64.b64encode(TITULO.encode('utf-16-le')).decode('ascii')
        m_b64 = base64.b64encode(mensagem.encode('utf-16-le')).decode('ascii')
        comando = (f'powershell -NoProfile -ExecutionPolicy Bypass '
                   f'-File C:\\Windows\\Temp\\wts_send.ps1 -SessionId {session_id} '
                   f'-TitleB64 {t_b64} -TextB64 {m_b64}')
        pythoncom.CoInitialize()
        try:
            c = credenciais.wmi_conectar(alvo)
            proc = c.Win32_Process.Create(CommandLine=comando)
            # Win32_Process.Create devolve (ProcessId, ReturnValue); ReturnValue 0 => sucesso
            cod = proc[1] if isinstance(proc, (tuple, list)) and len(proc) > 1 else proc
            if cod == 0:
                try:
                    # remove o script só depois que o popup já foi carregado (apagar na hora quebraria a execução)
                    c.Win32_Process.Create(CommandLine='cmd /c ping -n 150 127.0.0.1 >nul & del /q C:\\Windows\\Temp\\wts_send.ps1')
                except Exception:
                    pass
                return jsonify({"ok": True, "msg": f"Mensagem exibida para o usuario ativo de {host} (sessao {session_id})"})
            return jsonify({"ok": False, "msg": f"Falha ao abrir popup (codigo {cod})"})
        except Exception as e:
            return jsonify({"ok": False, "msg": f"Falha WMI: {str(e)}"})
        finally:
            pythoncom.CoUninitialize()
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})

def _get_active_session_id():
    """Retorna o SessionId de um processo de usuario ativo (explorer.exe) local ou None."""
    try:
        import wmi as _w
        pythoncom.CoInitialize()
        try:
            c = _w.WMI()
            for p in c.Win32_Process(Name="explorer.exe"):
                if p.SessionId not in (0, None):
                    return int(p.SessionId)
        finally:
            pythoncom.CoUninitialize()
    except Exception:
        pass
    return None

def _wts_send_local(session_id, titulo, texto):
    """Exibe popup nativo (WTSSendMessage) na sessao do usuario local."""
    scrip = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wts_send.ps1')
    if not os.path.exists(scrip):
        return False
    s = subprocess.STARTUPINFO(); s.dwFlags |= subprocess.STARTF_USESHOWWINDOW; s.wShowWindow = subprocess.SW_HIDE
    t_b64 = base64.b64encode(titulo.encode('utf-16-le')).decode('ascii')
    m_b64 = base64.b64encode(texto.encode('utf-16-le')).decode('ascii')
    res = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', scrip,
                          '-SessionId', str(session_id), '-TitleB64', t_b64, '-TextB64', m_b64],
                         capture_output=True, text=True, timeout=15, startupinfo=s)
    return 'ENVIADO' in res.stdout

@app.route('/bg_image')
def serve_bg_image():
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'imagens')
        for f in os.listdir(path):
            if f.lower().endswith(('.jpg','.jpeg','.png')): return send_from_directory(path, f)
    except: pass
    return "", 404

# ==============================================================================
# 8. VNC MONITOR
# ==============================================================================
def check_vnc_host(ip, port=5900, timeout=3.0):
    ip = ip.strip()
    agora = time.strftime('%H:%M:%S')
    try:
        addr = ipaddress.ip_address(ip)
        valido = addr.version == 4 and addr.is_private
    except ValueError:
        valido = False
    if not valido:
        return {'ip': ip, 'status': 'invalid', 'message': 'IP invalido (use um IPv4 da rede interna)', 'latency_ms': None, 'checked_at': agora}
    start = time.time()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            latency = round((time.time() - start) * 1000, 1)
            return {'ip': ip, 'status': 'online', 'message': f'Porta VNC ({port}) acessivel', 'latency_ms': latency, 'checked_at': time.strftime('%H:%M:%S')}
    except (socket.timeout, TimeoutError):
        return {'ip': ip, 'status': 'offline', 'message': 'Timeout — sem resposta', 'latency_ms': None, 'checked_at': time.strftime('%H:%M:%S')}
    except ConnectionRefusedError:
        return {'ip': ip, 'status': 'offline', 'message': 'Conexao recusada', 'latency_ms': None, 'checked_at': time.strftime('%H:%M:%S')}
    except OSError as e:
        return {'ip': ip, 'status': 'offline', 'message': f'Erro de rede: {e}', 'latency_ms': None, 'checked_at': time.strftime('%H:%M:%S')}

@app.route('/api/vnc/check', methods=['POST'])
@admin_required
def api_vnc_check():
    data = body()
    ips_raw = data.get('ips', '')
    try:
        port = int(data.get('port', 5900))
    except (TypeError, ValueError):
        return jsonify({'error': 'Porta inválida'}), 400
    if not 1 <= port <= 65535:
        return jsonify({'error': 'Porta inválida'}), 400
    if isinstance(ips_raw, list):
        ip_list = [str(ip).strip() for ip in ips_raw if str(ip).strip()]
    else:
        ip_list = [ip.strip() for ip in re.split(r'[,\n\r;]+', str(ips_raw)) if ip.strip()]
    if not ip_list:
        return jsonify({'error': 'Nenhum IP fornecido'}), 400
    if len(ip_list) > 50:
        return jsonify({'error': 'Maximo de 50 IPs por verificacao'}), 400
    results = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(check_vnc_host, ip, port): ip for ip in ip_list}
        for future in futures:
            results.append(future.result())
    results.sort(key=lambda x: [int(p) if p.isdigit() else 0 for p in x['ip'].split('.')])
    summary = {
        'total': len(results),
        'online': sum(1 for r in results if r['status'] == 'online'),
        'offline': sum(1 for r in results if r['status'] == 'offline'),
        'invalid': sum(1 for r in results if r['status'] == 'invalid'),
    }
    return jsonify({'results': results, 'summary': summary})

# ==============================================================================
# 9. PAINÉIS TV - SSH + COMANDOS REMOTOS
# ==============================================================================
def _ip_valido(ip):
    try:
        return ipaddress.ip_address(ip).version == 4
    except ValueError:
        return False

def painel_conhecido(ip):
    """Só IPs cadastrados em Painéis TV podem receber SSH (impede usar o monitor para enviar a senha a outro host)."""
    return any(p['ip'] == ip for p in db.listar_paineis())

def ssh_executar(ip, comando, timeout=30, usuario=None, senha=None):
    """Executa comando SSH no Raspberry Pi e retorna output."""
    usuario = usuario or cfg('painel_ssh_user', 'pi')
    senha = senha if senha is not None else cfg('painel_ssh_pass')
    if not senha:
        return {"ok": False, "erro": "Senha SSH dos painéis não configurada (Configurações > Painéis TV)."}
    client = paramiko.SSHClient()
    with LOCK_KNOWN_HOSTS:
        if os.path.exists(KNOWN_HOSTS_FILE):
            try: client.load_host_keys(KNOWN_HOSTS_FILE)
            except (OSError, paramiko.SSHException): pass
    # Confia na chave na 1ª conexão e passa a exigir a mesma nas próximas (detecta troca de host / MITM).
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(ip, username=usuario, password=senha,
                       timeout=10, allow_agent=False, look_for_keys=False)
        with LOCK_KNOWN_HOSTS:
            try: client.save_host_keys(KNOWN_HOSTS_FILE)
            except OSError: pass
        stdin, stdout, stderr = client.exec_command(comando, timeout=timeout)
        saida = stdout.read().decode('utf-8', errors='replace')
        erro = stderr.read().decode('utf-8', errors='replace')
        exit_code = stdout.channel.recv_exit_status()
        return {"ok": True, "output": saida, "erro": erro, "exit_code": exit_code}
    except paramiko.BadHostKeyException:
        return {"ok": False, "erro": "A chave SSH deste painel mudou (painel reinstalado ou possível interceptação). Se foi reinstalado, remova a linha dele do arquivo data/known_hosts."}
    except paramiko.AuthenticationException:
        return {"ok": False, "erro": "Falha na autenticação SSH (usuario/senha incorretos)"}
    except paramiko.SSHException as e:
        return {"ok": False, "erro": f"Erro SSH: {str(e)}"}
    except Exception as e:
        return {"ok": False, "erro": f"Não foi possível conectar: {str(e)}"}
    finally:
        client.close()

def check_painel_online(ip):
    """Verifica se o painel está online via ping."""
    return ping_individual(ip)[1]

_URL_PAINEL_RE = re.compile(r"^https?://[A-Za-z0-9._~:/?#\[\]@!$&()*+,;=%\-]{1,500}$")

def montar_cmd_mudar_url(url):
    """Monta o comando que troca a URL no painel.sh. Retorna None se a URL for inválida."""
    if not _URL_PAINEL_RE.match(url):
        return None
    url_sed = re.sub(r'([\\&])', r'\\\1', url)   # escapa os caracteres especiais do sed (o delimitador | já é barrado pela regex)
    expr = f"s|^URL=.*|URL='{url_sed}'|"
    return ("sed -i " + shlex.quote(expr) + " ~/painel.sh; "
            "echo " + shlex.quote("URL alterada para: " + url) + "; "
            "echo 'Reiniciando navegador...'; kill -9 $(pgrep -x chromium) 2>/dev/null; "
            "kill -9 $(pgrep -x firefox-esr) 2>/dev/null; sleep 1; "
            "nohup setsid bash ~/painel.sh > /dev/null 2>&1 & echo 'Navegador reiniciado com nova URL'")

def get_painel_details(ip):
    """Busca detalhes do painel via SSH."""
    result = ssh_executar(ip, COMANDOS_PAINEL['status']['cmd'], timeout=15)
    if not result['ok']:
        return None

    info = {
        "os": "N/D", "uptime": "N/D", "navegador": "N/D", "audio_sink": "N/D",
        "disco": "N/D", "memoria": "N/D", "ip_local": "N/D", "temp": "N/D"
    }

    output = result['output']

    m = re.search(r'up\s+(.+?),\s+\d+\s+user', output)
    if m: info['uptime'] = m.group(1).strip()

    if 'Firefox: RODANDO' in output: info['navegador'] = 'Firefox ESR'
    elif 'Chromium: RODANDO' in output: info['navegador'] = 'Chromium'
    elif 'Firefox: PARADO' in output or 'Chromium: PARADO' in output: info['navegador'] = 'PARADO'

    m = re.search(r'(\*?\s*\d+\.\s*.*HDMI.*)', output)
    if m: info['audio_sink'] = m.group(1).strip()

    m = re.search(r'^\S+\s+\S+\s+\S+\s+\S+\s+(\d+%)\s+/\s*$', output, re.M)
    if m: info['disco'] = m.group(1)

    m = re.search(r'Mem:\s+(\S+)\s+(\S+)', output)
    if m: info['memoria'] = f"{m.group(2)}/{m.group(1)}"

    m = re.search(r'inet\s+([\d.]+)', output)
    if m: info['ip_local'] = m.group(1)

    m = re.search(r'temp=(\d+\.\d+)', output)
    if m: info['temp'] = f"{m.group(1)}°C"

    return info

@app.route('/api/paineis')
def api_paineis():
    return jsonify(db.listar_paineis())

@app.route('/api/paineis/status', methods=['POST'])
def api_paineis_status():
    pedidos = body().get('ips')
    conhecidos = [p['ip'] for p in db.listar_paineis()]
    if isinstance(pedidos, list) and pedidos:
        ips = [ip for ip in pedidos if isinstance(ip, str) and ip in conhecidos]
    else:
        ips = conhecidos
    results = {}
    def check_one(ip):
        try:
            online = check_painel_online(ip)
            details = get_painel_details(ip) if online else None
        except Exception:
            online, details = False, None
        results[ip] = {"online": online, "details": details}
    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(check_one, ips))
    return jsonify(results)

@app.route('/api/paineis/executar', methods=['POST'])
@admin_required
def api_paineis_executar():
    data = body()
    ip = limpa(data.get('ip'), 64)
    cmd_id = limpa(data.get('cmd_id'), 50)

    if not ip:
        return jsonify({"ok": False, "erro": "IP obrigatório"})
    if not painel_conhecido(ip):
        return jsonify({"ok": False, "erro": "IP não pertence a um painel cadastrado"})

    if cmd_id == 'custom':
        custom_cmd = str(data.get('custom_cmd') or '').strip()[:2000]
        if not custom_cmd:
            return jsonify({"ok": False, "erro": "Comando vazio"})
        result = ssh_executar(ip, custom_cmd, timeout=60)
        db.auditar(usuario_atual(), 'painel_comando_livre', f"{ip}: {custom_cmd[:200]}")
    elif cmd_id == 'mudar_url':
        cmd = montar_cmd_mudar_url(limpa(data.get('param'), 500))
        if not cmd:
            return jsonify({"ok": False, "erro": "URL inválida (use http:// ou https:// e sem espaços/aspas)"})
        result = ssh_executar(ip, cmd, timeout=30)
        db.auditar(usuario_atual(), 'painel_mudar_url', f"{ip}: {limpa(data.get('param'), 200)}")
    elif cmd_id in COMANDOS_PAINEL:
        cmd_def = COMANDOS_PAINEL[cmd_id]
        if cmd_def.get('cmd'):
            result = ssh_executar(ip, cmd_def['cmd'], timeout=30)
            if cmd_id != 'status':
                db.auditar(usuario_atual(), 'painel_comando', f"{ip}: {cmd_id}")
        else:
            return jsonify({"ok": False, "erro": "Comando não executável"})
    else:
        return jsonify({"ok": False, "erro": "Comando desconhecido"})

    return jsonify(result)

@app.route('/api/paineis/adicionar', methods=['POST'])
@admin_required
def api_paineis_adicionar():
    d = body()
    ip = limpa(d.get('ip'), 45)
    if not _ip_valido(ip):
        return jsonify({"ok": False, "erro": "IP inválido"})
    try:
        db.adicionar_painel(limpa(d.get('unidade')), limpa(d.get('setor')), ip)
    except ValueError as e:
        return erro_json(e, 'erro')
    db.auditar(usuario_atual(), 'painel_adicionado', ip)
    return jsonify({"ok": True})

@app.route('/api/paineis/editar', methods=['POST'])
@admin_required
def api_paineis_editar():
    d = body()
    new_ip = limpa(d.get('ip'), 45)
    if not _ip_valido(new_ip):
        return jsonify({"ok": False, "erro": "IP inválido"})
    try:
        db.editar_painel(limpa(d.get('old_ip'), 45), limpa(d.get('unidade')), limpa(d.get('setor')), new_ip)
    except ValueError as e:
        return erro_json(e, 'erro')
    db.auditar(usuario_atual(), 'painel_editado', new_ip)
    return jsonify({"ok": True})

@app.route('/api/paineis/excluir', methods=['POST'])
@admin_required
def api_paineis_excluir():
    ip = limpa(body().get('ip'), 45)
    if not ip:
        return jsonify({"ok": False, "erro": "IP obrigatório"})
    db.excluir_painel(ip)
    db.auditar(usuario_atual(), 'painel_excluido', ip)
    return jsonify({"ok": True})

# ==============================================================================
# 10. INICIALIZAÇÃO
# ==============================================================================
HOST = env('MONITOR_HOST', '127.0.0.1')
PORT = env_int('MONITOR_PORT', 5050)
HTTPS = env_bool('MONITOR_HTTPS', False)
ESQUEMA = 'https' if HTTPS else 'http'

def open_browser():
    time.sleep(2)
    caminho = '/setup' if setup_pendente() else '/'
    print(f">>> [BROWSER] Abrindo {ESQUEMA}://127.0.0.1:{PORT}{caminho}")
    webbrowser.open(f"{ESQUEMA}://127.0.0.1:{PORT}{caminho}")

def iniciar_monitoramento():
    threading.Thread(target=background_scanner_loop, daemon=True).start()

if __name__ == '__main__':
    print("--- MONITOR TI ---")
    if setup_pendente():
        print(f">>> Primeiro acesso: abra {ESQUEMA}://127.0.0.1:{PORT}/setup neste computador para configurar o sistema.")
    contexto_ssl = None
    if HTTPS:
        import certificado
        contexto_ssl = certificado.garantir()
        print(">>> [HTTPS] Conexão criptografada ativa (certificado autoassinado em data/certificado.pem).")
    elif HOST not in ('127.0.0.1', 'localhost', '::1'):
        print(f">>> [AVISO] Servidor acessível pela rede em {HOST}:{PORT} usando HTTP simples. "
              "Defina MONITOR_HTTPS=1 no .env para criptografar, ou use VPN / proxy HTTPS.")
    iniciar_monitoramento()
    if env_bool('OPEN_BROWSER', True):
        threading.Thread(target=open_browser, daemon=True).start()
    print(f">>> Acesse: {ESQUEMA}://127.0.0.1:{PORT}")
    if not HTTPS:
        app.run(host=HOST, port=PORT, debug=False, threaded=True)
    else:
        import ssl
        from werkzeug.serving import ThreadedWSGIServer

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(*contexto_ssl)

        class ServidorHttps(ThreadedWSGIServer):
            """HTTPS cujo handshake roda na thread de cada conexão (uma conexão parada, como as
            "de reserva" do navegador, não trava as outras) e que redireciona http:// para https://."""
            def finish_request(self, request, client_address):
                try:
                    request.settimeout(15)
                    inicio = request.recv(1, socket.MSG_PEEK)
                    if not inicio:
                        return
                    if inicio[0] != 0x16:          # não é TLS: alguém abriu http:// nesta porta
                        self._redirecionar(request)
                        return
                    request = ctx.wrap_socket(request, server_side=True)
                    request.settimeout(None)
                except (OSError, ssl.SSLError):
                    return
                try:
                    super().finish_request(request, client_address)
                finally:
                    try: request.close()
                    except OSError: pass

            def _redirecionar(self, sock):
                dados = sock.recv(4096).decode('latin-1', 'ignore')
                alvo = re.search(r'^[A-Z]+ (/[^ \r\n]*)', dados)
                host = re.search(r'^host:\s*([A-Za-z0-9.\-]+)', dados, re.I | re.M)
                destino = f"https://{host.group(1) if host else '127.0.0.1'}:{PORT}{alvo.group(1) if alvo else '/'}"
                corpo = f'Use HTTPS: <a href="{destino}">{destino}</a>'.encode()
                sock.sendall(b"HTTP/1.1 301 Moved Permanently\r\nLocation: " + destino.encode() +
                             b"\r\nContent-Type: text/html\r\nConnection: close\r\nContent-Length: " +
                             str(len(corpo)).encode() + b"\r\n\r\n" + corpo)

        servidor = ServidorHttps(HOST, PORT, app)
        servidor.ssl_context = ctx    # só para o WSGI informar url_scheme=https
        servidor.serve_forever()
