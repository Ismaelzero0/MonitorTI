# -*- coding: utf-8 -*-
"""
Configuração básica do Monitor TI.

Quase tudo é configurado pelo próprio sistema (assistente de primeiro acesso e tela de
Configurações) e fica no banco SQLite. Variáveis de ambiente / `.env` são opcionais e
servem só como valor padrão ou para sobrescrever (ex.: MONITOR_HOST, MONITOR_PORT).
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get('MONITOR_DATA_DIR') or os.path.join(BASE_DIR, 'data')
TOOLS_DIR = os.path.join(BASE_DIR, 'tools')
os.makedirs(DATA_DIR, exist_ok=True)


def _carregar_dotenv(caminho):
    try:
        with open(caminho, 'r', encoding='utf-8-sig') as f:
            for linha in f:
                linha = linha.strip()
                if not linha or linha.startswith('#') or '=' not in linha:
                    continue
                chave, valor = linha.split('=', 1)
                chave, valor = chave.strip(), valor.strip()
                if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in ('"', "'"):
                    valor = valor[1:-1]
                if chave:
                    os.environ.setdefault(chave, valor)
    except OSError:
        pass


_carregar_dotenv(os.path.join(BASE_DIR, '.env'))


def env(nome, padrao=''):
    return os.environ.get(nome, padrao)


def env_bool(nome, padrao=False):
    valor = os.environ.get(nome)
    if valor is None or valor == '':
        return padrao
    return valor.strip().lower() in ('1', 'true', 'yes', 'sim', 'on')


def env_int(nome, padrao):
    try:
        return int(os.environ.get(nome, padrao))
    except (TypeError, ValueError):
        return padrao


def oem_encoding():
    """Codepage do console do Windows (varia por idioma) — usada ao ler saída de ping/shutdown/paexec."""
    try:
        import ctypes
        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    except Exception:
        return 'cp850'
