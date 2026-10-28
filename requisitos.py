# -*- coding: utf-8 -*-
"""
Verificação dos requisitos do sistema e instalação (automática ou manual) das ferramentas opcionais.

Regras de segurança do download automático:
  - só URLs https de fonte oficial listadas aqui;
  - o arquivo só é instalado se a assinatura digital do Windows for válida e o assinante for o esperado;
  - nada é baixado sem o usuário pedir (botão no assistente / tela de Configurações).
"""

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

from config import BASE_DIR, DATA_DIR, TOOLS_DIR

FERRAMENTAS = {
    'paexec': {
        'nome': 'PAExec (execução remota)',
        'arquivo': 'paexec.exe',
        'url': 'https://www.poweradmin.com/paexec/paexec.exe',
        'assinante': 'Power Admin',
        'pagina': 'https://www.poweradmin.com/paexec/',
        'uso': 'Gestão de programas, impressoras e limpeza de perfis nas máquinas da rede.',
    },
    'delprof2': {
        'nome': 'DelProf2 (limpeza de perfis)',
        'arquivo': 'DelProf2.exe',
        'url': None,   # o site oficial exige formulário: instalação só manual
        'assinante': 'Helge Klein',
        'pagina': 'https://helgeklein.com/free-tools/delprof2-user-profile-deletion-tool/',
        'uso': 'Apagar perfis de usuário antigos nas máquinas (opcional).',
    },
}

LIMITE_DOWNLOAD = 30 * 1024 * 1024


def caminho_ferramenta(chave):
    arquivo = FERRAMENTAS[chave]['arquivo']
    for pasta in (TOOLS_DIR, BASE_DIR):
        caminho = os.path.join(pasta, arquivo)
        if os.path.isfile(caminho):
            return caminho
    return None


def verificar_assinatura(caminho):
    """Retorna (status, assinante) usando Get-AuthenticodeSignature (Windows 10/11)."""
    env = dict(os.environ, MONITOR_SIGPATH=caminho)
    cmd = ['powershell', '-NoProfile', '-NonInteractive', '-Command',
           '$s = Get-AuthenticodeSignature -LiteralPath $env:MONITOR_SIGPATH; '
           '[Console]::OutputEncoding = [Text.Encoding]::UTF8; '
           'Write-Output $s.Status.ToString(); Write-Output $s.SignerCertificate.Subject']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                           timeout=60, env=env)
        linhas = (r.stdout or '').strip().splitlines()
        return (linhas[0].strip() if linhas else 'Desconhecido', linhas[1].strip() if len(linhas) > 1 else '')
    except (OSError, subprocess.SubprocessError):
        return ('Erro', '')


def baixar_ferramenta(chave):
    info = FERRAMENTAS.get(chave)
    if not info or not info['url']:
        return {'ok': False, 'erro': 'Esta ferramenta só pode ser instalada manualmente.', 'pagina': (info or {}).get('pagina')}
    if not info['url'].startswith('https://'):
        return {'ok': False, 'erro': 'URL insegura recusada.'}
    os.makedirs(TOOLS_DIR, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.exe', prefix='monitor_dl_')
    tmp.close()
    try:
        req = urllib.request.Request(info['url'], headers={'User-Agent': 'MonitorTI-Setup'})
        total = 0
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp.name, 'wb') as out:
            while True:
                bloco = resp.read(65536)
                if not bloco:
                    break
                total += len(bloco)
                if total > LIMITE_DOWNLOAD:
                    return {'ok': False, 'erro': 'Arquivo maior que o esperado. Download cancelado.'}
                out.write(bloco)
        status, assinante = verificar_assinatura(tmp.name)
        if status != 'Valid' or info['assinante'].lower() not in assinante.lower():
            return {'ok': False, 'erro': f'Assinatura digital não confere (status: {status}). O arquivo NÃO foi instalado.'}
        destino = os.path.join(TOOLS_DIR, info['arquivo'])
        shutil.move(tmp.name, destino)
        return {'ok': True, 'detalhe': f'{info["arquivo"]} instalado. Assinatura válida: {assinante.split(",")[0]}', 'tamanho': total}
    except Exception as e:
        return {'ok': False, 'erro': f'Falha no download: {e}'}
    finally:
        try:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)
        except OSError:
            pass


def _lib_ok(modulo):
    try:
        importlib.import_module(modulo)
        return True
    except Exception:
        return False


def _versao_windows():
    try:
        v = sys.getwindowsversion()
        nome = 'Windows 11' if v.build >= 22000 else 'Windows 10'
        return (v.build >= 17763, f'{nome} (build {v.build})')
    except AttributeError:
        return (False, 'Não é Windows')


def _pasta_gravavel(pasta):
    try:
        teste = os.path.join(pasta, '.teste_escrita')
        with open(teste, 'w') as f:
            f.write('ok')
        os.unlink(teste)
        return True
    except OSError:
        return False


def verificar():
    """Lista de requisitos com o estado de cada um."""
    itens = []
    ok_win, texto_win = _versao_windows()
    itens.append({'id': 'windows', 'nome': 'Windows 10 ou 11', 'ok': ok_win, 'obrigatorio': True, 'detalhe': texto_win})
    itens.append({'id': 'python', 'nome': 'Python 3.10 ou superior', 'ok': sys.version_info >= (3, 10), 'obrigatorio': True,
                  'detalhe': sys.version.split()[0]})
    faltando = [m for m in ('flask', 'requests', 'paramiko', 'cryptography', 'wmi', 'pythoncom') if not _lib_ok(m)]
    itens.append({'id': 'bibliotecas', 'nome': 'Bibliotecas do Python', 'ok': not faltando, 'obrigatorio': True,
                  'detalhe': 'Todas instaladas' if not faltando else 'Faltando: ' + ', '.join(faltando) + ' (execute instalar_dependencias.bat)'})
    itens.append({'id': 'dados', 'nome': 'Pasta de dados com permissão de escrita', 'ok': _pasta_gravavel(DATA_DIR),
                  'obrigatorio': True, 'detalhe': DATA_DIR})
    for chave, info in FERRAMENTAS.items():
        caminho = caminho_ferramenta(chave)
        itens.append({'id': chave, 'nome': info['nome'], 'ok': bool(caminho), 'obrigatorio': False,
                      'detalhe': caminho or ('Não instalado. ' + info['uso']),
                      'instalar_auto': bool(info['url']), 'pagina': info['pagina']})
    return itens
