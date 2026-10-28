# -*- coding: utf-8 -*-
"""
Credenciais de REDE por sessão (sem conta fixa).

Cada pessoa informa a própria conta de rede (ex.: DOMINIO\\usuario) ao usar a gestão remota.
  - A senha fica SÓ na memória do servidor, ligada à sessão da pessoa (o cookie guarda apenas um
    identificador aleatório). Nunca vai para o banco, para o log ou para a tela.
  - Expira após um período sem uso (TTL), ao sair ou quando o servidor reinicia.
  - As ações remotas usam a conta da própria pessoa; o Windows das máquinas registra quem fez.

Isolamento entre usuários: o acesso a arquivos/registro remotos usa LogonUser com
LOGON32_LOGON_NEW_CREDENTIALS (equivalente ao `runas /netonly`), que cria uma sessão de logon
separada por pessoa — assim duas pessoas usando o monitor ao mesmo tempo não compartilham a
conexão SMB nem as credenciais. O WMI recebe usuário/senha explicitamente a cada conexão e o
PAExec recebe -u/-p.
"""

import contextvars
import os
import secrets
import threading
import time
from contextlib import contextmanager

import pythoncom
import wmi

TTL_SEGUNDOS = 8 * 3600   # igual à sessão de login: valida uma vez e vale até a sessão cair

_lock = threading.Lock()
_store = {}     # token -> {'usuario', 'senha', 'host', 'dono', 'ts'}
_atual = contextvars.ContextVar('credencial_rede', default=None)


def _expirar(agora):
    for token in [t for t, c in _store.items() if agora - c['ts'] > TTL_SEGUNDOS]:
        _store.pop(token, None)


def guardar(dono, usuario, senha, host):
    token = secrets.token_urlsafe(32)
    agora = time.time()
    with _lock:
        _expirar(agora)
        _store[token] = {'usuario': usuario, 'senha': senha, 'host': host, 'dono': dono, 'ts': agora}
    return token


def buscar(token):
    """Retorna a credencial (e renova a validade) ou None se não existe/expirou."""
    if not token:
        return None
    agora = time.time()
    with _lock:
        _expirar(agora)
        cred = _store.get(token)
        if cred:
            cred['ts'] = agora
        return dict(cred) if cred else None


def descartar(token):
    with _lock:
        _store.pop(token, None)


def segundos_restantes(token):
    with _lock:
        cred = _store.get(token)
        return max(0, int(TTL_SEGUNDOS - (time.time() - cred['ts']))) if cred else 0


# ---- credencial da requisição em andamento (definida no início de cada requisição) ----
def definir_contexto(cred):
    _atual.set(cred)


def atual():
    return _atual.get()


def normalizar_usuario(usuario):
    """Sem domínio informado, usa o domínio deste servidor (se ele estiver em um); contas locais ficam como estão."""
    usuario = (usuario or '').strip()
    if not usuario or '\\' in usuario or '@' in usuario:
        return usuario
    dominio = os.environ.get('USERDOMAIN', '')
    if dominio and dominio.lower() != os.environ.get('COMPUTERNAME', '').lower():
        return f"{dominio}\\{usuario}"
    return usuario


def _dividir(usuario):
    """'DOMINIO\\user' -> ('DOMINIO', 'user'); 'user@dominio' -> ('', 'user@dominio'); 'user' -> ('', 'user')."""
    if '\\' in usuario:
        dominio, nome = usuario.split('\\', 1)
        return dominio, nome
    return '', usuario


@contextmanager
def como_usuario(cred=None):
    """Executa o bloco com a identidade DE REDE da pessoa (arquivos/registro remotos). Sem credencial, não muda nada."""
    cred = cred or atual()
    if not cred:
        yield
        return
    import win32con
    import win32security
    dominio, nome = _dividir(cred['usuario'])
    token = win32security.LogonUser(nome, dominio, cred['senha'], win32con.LOGON32_LOGON_NEW_CREDENTIALS,
                                    win32con.LOGON32_PROVIDER_WINNT50)
    try:
        win32security.ImpersonateLoggedOnUser(token)
        try:
            yield
        finally:
            win32security.RevertToSelf()
    finally:
        token.Close()


def pae_args(cred=None):
    cred = cred or atual()
    return ['-u', cred['usuario'], '-p', cred['senha']] if cred else []


def wmi_conectar(computador, namespace=None, privilegios=None, cred=None):
    """Conexão WMI. Local: identidade do servidor. Remoto: usa a conta da pessoa, se houver; senão a do servidor."""
    cred = cred or atual()
    args = {}
    if namespace:
        args['namespace'] = namespace
    if privilegios:
        args['privileges'] = privilegios
    if computador and computador not in ('127.0.0.1', 'localhost'):
        args['computer'] = computador
        if cred:
            args['user'] = cred['usuario']
            args['password'] = cred['senha']
    return wmi.WMI(**args)


def desligar_ou_reiniciar(computador, reiniciar, cred=None):
    """Reinicia/desliga (forçado) via WMI com a conta da pessoa. Retorna o código de retorno (0 = ok)."""
    pythoncom.CoInitialize()
    c = sistema = None
    try:
        c = wmi_conectar(computador, privilegios=['Shutdown'], cred=cred)
        sistema = c.Win32_OperatingSystem()[0]
        # Win32Shutdown: 2 = reiniciar, 1 = desligar, 4 = forçar, 8 = desligar a energia
        resultado = sistema.Win32Shutdown(Flags=(2 + 4) if reiniciar else (1 + 4 + 8))
        return resultado[0] if isinstance(resultado, (tuple, list)) else resultado
    finally:
        c = sistema = None     # libera os objetos COM antes de encerrar o COM
        pythoncom.CoUninitialize()
