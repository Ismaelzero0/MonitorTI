# -*- coding: utf-8 -*-
"""
Camada de dados (SQLite): inventário, usuários, notas, histórico de status,
configurações (com segredos criptografados) e auditoria.

Cada função abre sua própria conexão (seguro com várias threads). O banco usa
modo WAL, então leituras não bloqueiam a gravação.
"""

import os
import secrets
import sqlite3
import subprocess
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta

from cryptography.fernet import Fernet, InvalidToken
from werkzeug.security import check_password_hash, generate_password_hash

from config import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, 'monitor.db')
KEY_PATH = os.path.join(DATA_DIR, 'chave.key')
PAPEIS = ('admin', 'tecnico', 'leitor')
TIPOS = ('PC', 'IMP', 'TV', 'Outro')

_lock_init = threading.Lock()
_fernet = None


def agora():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


@contextmanager
def conexao():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys = ON')
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS unidades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL UNIQUE COLLATE NOCASE,
    ordem INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS andares (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unidade_id INTEGER NOT NULL REFERENCES unidades(id) ON DELETE CASCADE,
    nome TEXT NOT NULL,
    ordem INTEGER NOT NULL DEFAULT 0,
    UNIQUE (unidade_id, nome)
);
CREATE TABLE IF NOT EXISTS equipamentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    andar_id INTEGER NOT NULL REFERENCES andares(id) ON DELETE CASCADE,
    setor TEXT NOT NULL DEFAULT '',
    host TEXT NOT NULL,
    tipo TEXT NOT NULL DEFAULT 'PC',
    criado_em TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_equip_andar ON equipamentos(andar_id);
CREATE TABLE IF NOT EXISTS paineis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unidade TEXT NOT NULL DEFAULT '',
    setor TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario TEXT NOT NULL UNIQUE COLLATE NOCASE,
    senha_hash TEXT NOT NULL,
    papel TEXT NOT NULL DEFAULT 'leitor',
    ativo INTEGER NOT NULL DEFAULT 1,
    criado_em TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    equipamento_id INTEGER NOT NULL REFERENCES equipamentos(id) ON DELETE CASCADE,
    usuario TEXT NOT NULL,
    texto TEXT NOT NULL,
    criado_em TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_notas_equip ON notas(equipamento_id);
CREATE TABLE IF NOT EXISTS historico (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    host TEXT NOT NULL,
    online INTEGER NOT NULL,
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_hist_host ON historico(host, ts);
CREATE TABLE IF NOT EXISTS config (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL DEFAULT '',
    segredo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS auditoria (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    usuario TEXT NOT NULL,
    acao TEXT NOT NULL,
    detalhe TEXT NOT NULL DEFAULT ''
);
"""


def init():
    with _lock_init:
        with conexao() as con:
            con.execute('PRAGMA journal_mode = WAL')
            con.executescript(SCHEMA)


# ------------------------------------------------------------------ criptografia
def _get_fernet():
    global _fernet
    if _fernet is None:
        if not os.path.exists(KEY_PATH):
            with open(KEY_PATH, 'wb') as f:
                f.write(Fernet.generate_key())
            try:  # restringe o arquivo ao usuário atual (melhor esforço)
                usuario = os.environ.get('USERNAME', '')
                if usuario:
                    subprocess.run(['icacls', KEY_PATH, '/inheritance:r', '/grant:r', f'{usuario}:F'],
                                   capture_output=True, timeout=10)
            except Exception:
                pass
        with open(KEY_PATH, 'rb') as f:
            _fernet = Fernet(f.read().strip())
    return _fernet


def criptografar(texto):
    return _get_fernet().encrypt(texto.encode('utf-8')).decode('ascii')


def descriptografar(token):
    try:
        return _get_fernet().decrypt(token.encode('ascii')).decode('utf-8')
    except (InvalidToken, ValueError):
        return ''


def get_secret_key():
    """Chave de sessão do Flask: guardada no banco (criptografada), gerada na 1ª vez."""
    atual = get_config('flask_secret')
    if atual:
        return atual
    nova = secrets.token_hex(32)
    set_config('flask_secret', nova, segredo=True)
    return nova


# ------------------------------------------------------------------ configurações
def get_config(chave, padrao=''):
    with conexao() as con:
        row = con.execute('SELECT valor, segredo FROM config WHERE chave = ?', (chave,)).fetchone()
    if not row:
        return padrao
    return descriptografar(row['valor']) if row['segredo'] else row['valor']


def set_config(chave, valor, segredo=False):
    valor = '' if valor is None else str(valor)
    guardado = criptografar(valor) if (segredo and valor) else valor
    with conexao() as con:
        con.execute('INSERT INTO config(chave, valor, segredo) VALUES (?,?,?) '
                    'ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor, segredo = excluded.segredo',
                    (chave, guardado, 1 if segredo else 0))


def apagar_config(chave):
    with conexao() as con:
        con.execute('DELETE FROM config WHERE chave = ?', (chave,))


def get_config_publica():
    """Todas as configurações; segredos aparecem só como 'definido' (nunca o valor)."""
    saida = {}
    with conexao() as con:
        for row in con.execute('SELECT chave, valor, segredo FROM config'):
            if row['chave'] == 'flask_secret':
                continue
            saida[row['chave']] = {'definido': bool(row['valor']), 'segredo': True} if row['segredo'] \
                else {'valor': row['valor'], 'segredo': False}
    return saida


# ------------------------------------------------------------------ usuários
def validar_senha(senha):
    if not senha:
        return 'Informe uma senha.'
    return ''


def tem_usuario():
    with conexao() as con:
        return con.execute('SELECT 1 FROM usuarios WHERE ativo = 1 LIMIT 1').fetchone() is not None


def criar_usuario(usuario, senha, papel):
    usuario = (usuario or '').strip()
    if not usuario or len(usuario) > 40 or not usuario.replace('_', '').replace('.', '').replace('-', '').isalnum():
        raise ValueError('Usuário inválido (use letras, números, ponto, hífen ou sublinhado).')
    if papel not in PAPEIS:
        raise ValueError('Papel inválido.')
    erro = validar_senha(senha)
    if erro:
        raise ValueError(erro)
    try:
        with conexao() as con:
            con.execute('INSERT INTO usuarios(usuario, senha_hash, papel, criado_em) VALUES (?,?,?,?)',
                        (usuario, generate_password_hash(senha), papel, agora()))
    except sqlite3.IntegrityError:
        raise ValueError('Já existe um usuário com esse nome.')


def autenticar(usuario, senha):
    """Retorna {'usuario','papel'} ou None."""
    with conexao() as con:
        row = con.execute('SELECT usuario, senha_hash, papel FROM usuarios WHERE usuario = ? AND ativo = 1',
                          ((usuario or '').strip(),)).fetchone()
    if not row:
        # gasta o mesmo tempo para não revelar se o usuário existe
        check_password_hash(generate_password_hash('x'), senha or '')
        return None
    if check_password_hash(row['senha_hash'], senha or ''):
        return {'usuario': row['usuario'], 'papel': row['papel']}
    return None


def listar_usuarios():
    with conexao() as con:
        return [dict(r) for r in con.execute('SELECT id, usuario, papel, ativo, criado_em FROM usuarios ORDER BY usuario')]


def _contar_admins(con, excluindo_id=None):
    sql = "SELECT COUNT(*) FROM usuarios WHERE papel = 'admin' AND ativo = 1"
    args = ()
    if excluindo_id is not None:
        sql += ' AND id != ?'
        args = (excluindo_id,)
    return con.execute(sql, args).fetchone()[0]


def excluir_usuario(uid):
    with conexao() as con:
        row = con.execute('SELECT papel FROM usuarios WHERE id = ?', (uid,)).fetchone()
        if not row:
            raise ValueError('Usuário não encontrado.')
        if row['papel'] == 'admin' and _contar_admins(con, uid) == 0:
            raise ValueError('Não é possível excluir o último administrador.')
        con.execute('DELETE FROM usuarios WHERE id = ?', (uid,))


def alterar_usuario(uid, papel=None, senha=None, ativo=None):
    with conexao() as con:
        row = con.execute('SELECT papel, ativo FROM usuarios WHERE id = ?', (uid,)).fetchone()
        if not row:
            raise ValueError('Usuário não encontrado.')
        novo_papel = papel if papel is not None else row['papel']
        novo_ativo = (1 if ativo else 0) if ativo is not None else row['ativo']
        if novo_papel not in PAPEIS:
            raise ValueError('Papel inválido.')
        if (novo_papel != 'admin' or not novo_ativo) and row['papel'] == 'admin' and _contar_admins(con, uid) == 0:
            raise ValueError('Deve existir pelo menos um administrador ativo.')
        if senha:
            erro = validar_senha(senha)
            if erro:
                raise ValueError(erro)
            con.execute('UPDATE usuarios SET senha_hash = ? WHERE id = ?', (generate_password_hash(senha), uid))
        con.execute('UPDATE usuarios SET papel = ?, ativo = ? WHERE id = ?', (novo_papel, novo_ativo, uid))


# ------------------------------------------------------------------ inventário
def _unidade_id(con, nome, criar=False):
    row = con.execute('SELECT id FROM unidades WHERE nome = ?', (nome,)).fetchone()
    if row:
        return row['id']
    if not criar:
        return None
    ordem = con.execute('SELECT COALESCE(MAX(ordem), 0) + 1 FROM unidades').fetchone()[0]
    return con.execute('INSERT INTO unidades(nome, ordem) VALUES (?,?)', (nome, ordem)).lastrowid


def _andar_id(con, unidade_id, nome, criar=False):
    row = con.execute('SELECT id FROM andares WHERE unidade_id = ? AND nome = ?', (unidade_id, nome)).fetchone()
    if row:
        return row['id']
    if not criar:
        return None
    ordem = con.execute('SELECT COALESCE(MAX(ordem), 0) + 1 FROM andares WHERE unidade_id = ?', (unidade_id,)).fetchone()[0]
    return con.execute('INSERT INTO andares(unidade_id, nome, ordem) VALUES (?,?,?)', (unidade_id, nome, ordem)).lastrowid


def get_all_data():
    """{unidade: {andar: [ {local, setor, host, tipo, id_uniq, notas} ]}} na ordem cadastrada."""
    dados = {}
    with conexao() as con:
        for u in con.execute('SELECT id, nome FROM unidades ORDER BY ordem, nome'):
            dados[u['nome']] = {}
            for a in con.execute('SELECT id, nome FROM andares WHERE unidade_id = ? ORDER BY ordem, id', (u['id'],)):
                itens = []
                for e in con.execute(
                        'SELECT e.id, e.setor, e.host, e.tipo, '
                        '(SELECT COUNT(*) FROM notas n WHERE n.equipamento_id = e.id) AS qnotas '
                        'FROM equipamentos e WHERE e.andar_id = ? ORDER BY e.id', (a['id'],)):
                    itens.append({'local': a['nome'], 'setor': e['setor'], 'host': e['host'],
                                  'tipo': e['tipo'], 'id_uniq': e['id'], 'notas': e['qnotas']})
                dados[u['nome']][a['nome']] = itens
    return dados


def todos_hosts():
    with conexao() as con:
        return [r['host'] for r in con.execute('SELECT DISTINCT host FROM equipamentos WHERE host != ""')]


def total_equipamentos():
    with conexao() as con:
        return con.execute('SELECT COUNT(*) FROM equipamentos').fetchone()[0]


def criar_unidade(nome):
    nome = (nome or '').strip()[:100]
    if not nome:
        raise ValueError('Informe o nome da unidade.')
    with conexao() as con:
        if _unidade_id(con, nome):
            raise ValueError('Já existe uma unidade com esse nome.')
        return _unidade_id(con, nome, criar=True)


def renomear_unidade(antigo, novo):
    novo = (novo or '').strip()[:100]
    if not novo:
        raise ValueError('Informe o novo nome.')
    with conexao() as con:
        uid = _unidade_id(con, antigo)
        if uid is None:
            raise ValueError('Unidade não encontrada.')
        outro = _unidade_id(con, novo)
        if outro is not None and outro != uid:
            raise ValueError('Já existe uma unidade com esse nome.')
        con.execute('UPDATE unidades SET nome = ? WHERE id = ?', (novo, uid))


def excluir_unidade(nome):
    with conexao() as con:
        uid = _unidade_id(con, nome)
        if uid is None:
            raise ValueError('Unidade não encontrada.')
        con.execute('DELETE FROM unidades WHERE id = ?', (uid,))


def adicionar_andar(unidade, nome, posicao='END'):
    nome = (nome or '').strip()[:100]
    if not nome:
        raise ValueError('Informe o nome do andar.')
    with conexao() as con:
        uid = _unidade_id(con, unidade)
        if uid is None:
            raise ValueError('Unidade não encontrada.')
        if _andar_id(con, uid, nome):
            raise ValueError('Já existe um andar com esse nome nesta unidade.')
        andares = [r['id'] for r in con.execute('SELECT id FROM andares WHERE unidade_id = ? ORDER BY ordem, id', (uid,))]
        novo = con.execute('INSERT INTO andares(unidade_id, nome, ordem) VALUES (?,?,0)', (uid, nome)).lastrowid
        if posicao == 'START':
            ordem_ids = [novo] + andares
        elif posicao in ('END', '', None):
            ordem_ids = andares + [novo]
        else:
            ref = _andar_id(con, uid, posicao)
            ordem_ids = list(andares)
            ordem_ids.insert(ordem_ids.index(ref) + 1 if ref in ordem_ids else len(ordem_ids), novo)
        for i, aid in enumerate(ordem_ids):
            con.execute('UPDATE andares SET ordem = ? WHERE id = ?', (i, aid))


def _tipo_normalizado(tipo):
    t = (tipo or '').upper()
    if t in ('PC', 'IMP', 'TV'):
        return t
    if any(x in t for x in ('COMPUTADOR', 'PC', 'DESK', 'NOTE', 'NB', 'LAPTOP', 'SERVER')):
        return 'PC'
    if any(x in t for x in ('IMPRESSORA', 'PRINT', 'IMP', 'SCANNER')):
        return 'IMP'
    if 'TV' in t or 'PAINEL' in t:
        return 'TV'
    return 'Outro'


def adicionar_equipamento(unidade, andar, setor, host, tipo):
    host = (host or '').strip()[:200]
    if not host:
        raise ValueError('Informe o host ou IP.')
    andar = (andar or '').strip()[:100] or 'Geral'
    with conexao() as con:
        uid = _unidade_id(con, unidade)
        if uid is None:
            raise ValueError('Unidade não encontrada.')
        dup = con.execute('SELECT 1 FROM equipamentos e JOIN andares a ON a.id = e.andar_id '
                          'WHERE a.unidade_id = ? AND LOWER(e.host) = LOWER(?)', (uid, host)).fetchone()
        if dup:
            raise ValueError('Este host já está cadastrado nesta unidade.')
        aid = _andar_id(con, uid, andar, criar=True)
        return con.execute('INSERT INTO equipamentos(andar_id, setor, host, tipo, criado_em) VALUES (?,?,?,?,?)',
                           (aid, (setor or '').strip()[:200], host, _tipo_normalizado(tipo), agora())).lastrowid


def _achar_equipamento(con, unidade, host, setor):
    return con.execute(
        'SELECT e.id FROM equipamentos e JOIN andares a ON a.id = e.andar_id JOIN unidades u ON u.id = a.unidade_id '
        'WHERE u.nome = ? AND e.host = ? AND e.setor = ? LIMIT 1', (unidade, host, setor)).fetchone()


def editar_equipamento(unidade, old_host, old_setor, new_andar, new_setor, new_host):
    new_host = (new_host or '').strip()[:200]
    if not new_host:
        raise ValueError('Informe o host ou IP.')
    with conexao() as con:
        row = _achar_equipamento(con, unidade, old_host, old_setor)
        if not row:
            raise ValueError('Item não encontrado.')
        uid = _unidade_id(con, unidade)
        if new_host.lower() != old_host.lower():
            dup = con.execute('SELECT 1 FROM equipamentos e JOIN andares a ON a.id = e.andar_id '
                              'WHERE a.unidade_id = ? AND LOWER(e.host) = LOWER(?) AND e.id != ?',
                              (uid, new_host, row['id'])).fetchone()
            if dup:
                raise ValueError('Este host já está cadastrado nesta unidade.')
        aid = _andar_id(con, uid, (new_andar or '').strip()[:100] or 'Geral', criar=True)
        con.execute('UPDATE equipamentos SET andar_id = ?, setor = ?, host = ? WHERE id = ?',
                    (aid, (new_setor or '').strip()[:200], new_host, row['id']))


def excluir_equipamento(unidade, host, setor):
    with conexao() as con:
        row = _achar_equipamento(con, unidade, host, setor)
        if not row:
            raise ValueError('Item não encontrado.')
        con.execute('DELETE FROM equipamentos WHERE id = ?', (row['id'],))


def detalhe_unidade(unidade):
    """Andares e equipamentos de uma unidade, para a tela de edição do admin."""
    with conexao() as con:
        uid = _unidade_id(con, unidade)
        if uid is None:
            raise ValueError('Unidade não encontrada.')
        andares = []
        for a in con.execute('SELECT id, nome FROM andares WHERE unidade_id = ? ORDER BY ordem, id', (uid,)):
            itens = [dict(r) for r in con.execute(
                'SELECT id, setor, host, tipo FROM equipamentos WHERE andar_id = ? ORDER BY id', (a['id'],))]
            andares.append({'nome': a['nome'], 'itens': itens})
        return andares


def renomear_andar(unidade, antigo, novo):
    novo = (novo or '').strip()[:100]
    if not novo:
        raise ValueError('Informe o novo nome do andar.')
    with conexao() as con:
        uid = _unidade_id(con, unidade)
        aid = _andar_id(con, uid, antigo) if uid is not None else None
        if aid is None:
            raise ValueError('Andar não encontrado.')
        outro = _andar_id(con, uid, novo)
        if outro is not None and outro != aid:
            raise ValueError('Já existe um andar com esse nome nesta unidade.')
        con.execute('UPDATE andares SET nome = ? WHERE id = ?', (novo, aid))


def excluir_andar(unidade, nome):
    """Exclui o andar e seus equipamentos (e anotações). Retorna quantos equipamentos foram removidos."""
    with conexao() as con:
        uid = _unidade_id(con, unidade)
        aid = _andar_id(con, uid, nome) if uid is not None else None
        if aid is None:
            raise ValueError('Andar não encontrado.')
        qtd = con.execute('SELECT COUNT(*) FROM equipamentos WHERE andar_id = ?', (aid,)).fetchone()[0]
        con.execute('DELETE FROM andares WHERE id = ?', (aid,))
        return qtd


def salvar_equipamento(equip_id, andar, setor, host, tipo):
    """Edita todos os campos de um equipamento pelo id (o andar pode ser trocado para outro da mesma unidade)."""
    host = (host or '').strip()[:200]
    if not host:
        raise ValueError('Informe o host ou IP.')
    if tipo not in TIPOS:
        raise ValueError('Tipo inválido.')
    with conexao() as con:
        row = con.execute('SELECT e.id, a.unidade_id FROM equipamentos e JOIN andares a ON a.id = e.andar_id '
                          'WHERE e.id = ?', (equip_id,)).fetchone()
        if not row:
            raise ValueError('Equipamento não encontrado.')
        dup = con.execute('SELECT 1 FROM equipamentos e JOIN andares a ON a.id = e.andar_id '
                          'WHERE a.unidade_id = ? AND LOWER(e.host) = LOWER(?) AND e.id != ?',
                          (row['unidade_id'], host, equip_id)).fetchone()
        if dup:
            raise ValueError('Este host já está cadastrado nesta unidade.')
        aid = _andar_id(con, row['unidade_id'], (andar or '').strip()[:100] or 'Geral', criar=True)
        con.execute('UPDATE equipamentos SET andar_id = ?, setor = ?, host = ?, tipo = ? WHERE id = ?',
                    (aid, (setor or '').strip()[:200], host, tipo, equip_id))


def excluir_equipamento_id(equip_id):
    with conexao() as con:
        if not con.execute('SELECT 1 FROM equipamentos WHERE id = ?', (equip_id,)).fetchone():
            raise ValueError('Equipamento não encontrado.')
        con.execute('DELETE FROM equipamentos WHERE id = ?', (equip_id,))


def importar_linhas(linhas, ignorar_duplicados=True):
    """linhas: iterável de (unidade, andar, setor, host, tipo). Retorna (inseridos, ignorados)."""
    inseridos = ignorados = 0
    with conexao() as con:
        for unidade, andar, setor, host, tipo in linhas:
            unidade, host = (unidade or '').strip()[:100], (host or '').strip()[:200]
            if not unidade or not host:
                ignorados += 1
                continue
            uid = _unidade_id(con, unidade, criar=True)
            aid = _andar_id(con, uid, (andar or '').strip()[:100] or 'Geral', criar=True)
            if ignorar_duplicados and con.execute(
                    'SELECT 1 FROM equipamentos e JOIN andares a ON a.id = e.andar_id '
                    'WHERE a.unidade_id = ? AND LOWER(e.host) = LOWER(?) AND e.setor = ?',
                    (uid, host, (setor or '').strip())).fetchone():
                ignorados += 1
                continue
            con.execute('INSERT INTO equipamentos(andar_id, setor, host, tipo, criado_em) VALUES (?,?,?,?,?)',
                        (aid, (setor or '').strip()[:200], host, _tipo_normalizado(tipo), agora()))
            inseridos += 1
    return inseridos, ignorados


def exportar_linhas():
    with conexao() as con:
        return [(r['unidade'], r['andar'], r['setor'], r['host'], r['tipo']) for r in con.execute(
            'SELECT u.nome AS unidade, a.nome AS andar, e.setor, e.host, e.tipo FROM equipamentos e '
            'JOIN andares a ON a.id = e.andar_id JOIN unidades u ON u.id = a.unidade_id '
            'ORDER BY u.ordem, u.nome, a.ordem, e.id')]


# ------------------------------------------------------------------ painéis de TV
def listar_paineis():
    with conexao() as con:
        return [{'unidade': r['unidade'], 'setor': r['setor'], 'ip': r['ip'], 'id_painel': r['id']}
                for r in con.execute('SELECT id, unidade, setor, ip FROM paineis ORDER BY unidade, setor, ip')]


def adicionar_painel(unidade, setor, ip):
    try:
        with conexao() as con:
            con.execute('INSERT INTO paineis(unidade, setor, ip) VALUES (?,?,?)',
                        ((unidade or '').strip()[:100], (setor or '').strip()[:200], ip))
    except sqlite3.IntegrityError:
        raise ValueError('Este IP já está cadastrado.')


def editar_painel(old_ip, unidade, setor, ip):
    try:
        with conexao() as con:
            n = con.execute('UPDATE paineis SET unidade = ?, setor = ?, ip = ? WHERE ip = ?',
                            ((unidade or '').strip()[:100], (setor or '').strip()[:200], ip, old_ip)).rowcount
    except sqlite3.IntegrityError:
        raise ValueError('Já existe um painel com esse IP.')
    if not n:
        raise ValueError('Painel não encontrado.')


def excluir_painel(ip):
    with conexao() as con:
        con.execute('DELETE FROM paineis WHERE ip = ?', (ip,))


# ------------------------------------------------------------------ notas
def listar_notas(equipamento_id):
    with conexao() as con:
        return [dict(r) for r in con.execute(
            'SELECT id, usuario, texto, criado_em FROM notas WHERE equipamento_id = ? ORDER BY id DESC', (equipamento_id,))]


def adicionar_nota(equipamento_id, usuario, texto):
    texto = (texto or '').strip()[:2000]
    if not texto:
        raise ValueError('A anotação está vazia.')
    with conexao() as con:
        if not con.execute('SELECT 1 FROM equipamentos WHERE id = ?', (equipamento_id,)).fetchone():
            raise ValueError('Equipamento não encontrado.')
        con.execute('INSERT INTO notas(equipamento_id, usuario, texto, criado_em) VALUES (?,?,?,?)',
                    (equipamento_id, usuario, texto, agora()))


def excluir_nota(nota_id, usuario, eh_admin):
    with conexao() as con:
        row = con.execute('SELECT usuario FROM notas WHERE id = ?', (nota_id,)).fetchone()
        if not row:
            raise ValueError('Anotação não encontrada.')
        if not eh_admin and row['usuario'] != usuario:
            raise ValueError('Você só pode excluir as suas próprias anotações.')
        con.execute('DELETE FROM notas WHERE id = ?', (nota_id,))


def host_do_equipamento(equipamento_id):
    with conexao() as con:
        row = con.execute('SELECT host FROM equipamentos WHERE id = ?', (equipamento_id,)).fetchone()
    return row['host'] if row else None


# ------------------------------------------------------------------ histórico de status
_ultimo_estado = {}
_lock_hist = threading.Lock()


def carregar_ultimo_estado():
    with conexao() as con:
        for r in con.execute('SELECT h.host, h.online FROM historico h '
                             'JOIN (SELECT host, MAX(id) AS mid FROM historico GROUP BY host) m ON m.mid = h.id'):
            _ultimo_estado[r['host']] = bool(r['online'])


def registrar_estados(estados):
    """estados: {host: online}. Grava só as mudanças (transições)."""
    mudancas = []
    with _lock_hist:
        for host, online in estados.items():
            online = bool(online)
            if _ultimo_estado.get(host) != online:
                _ultimo_estado[host] = online
                mudancas.append((host, 1 if online else 0, agora()))
    if mudancas:
        with conexao() as con:
            con.executemany('INSERT INTO historico(host, online, ts) VALUES (?,?,?)', mudancas)


def historico_host(host, limite=40):
    with conexao() as con:
        eventos = [dict(r) for r in con.execute(
            'SELECT online, ts FROM historico WHERE host = ? ORDER BY id DESC LIMIT ?', (host, limite))]
        desde = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
        rows = con.execute('SELECT online, ts FROM historico WHERE host = ? AND ts >= ? ORDER BY id', (host, desde)).fetchall()
        anterior = con.execute('SELECT online FROM historico WHERE host = ? AND ts < ? ORDER BY id DESC LIMIT 1',
                               (host, desde)).fetchone()
    disponibilidade = None
    if rows or anterior:
        fim = datetime.now()
        ini = datetime.strptime(desde, '%Y-%m-%d %H:%M:%S')
        estado = bool(anterior['online']) if anterior else bool(rows[0]['online'])
        cursor = ini if anterior else datetime.strptime(rows[0]['ts'], '%Y-%m-%d %H:%M:%S')
        total = (fim - cursor).total_seconds()
        on = 0.0
        for r in rows:
            t = datetime.strptime(r['ts'], '%Y-%m-%d %H:%M:%S')
            if estado:
                on += (t - cursor).total_seconds()
            cursor, estado = t, bool(r['online'])
        if estado:
            on += (fim - cursor).total_seconds()
        disponibilidade = round(100 * on / total, 1) if total > 0 else None
    return {'eventos': eventos, 'disponibilidade_7d': disponibilidade}


def limpar_historico_antigo(dias=180):
    corte = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d %H:%M:%S')
    with conexao() as con:
        con.execute('DELETE FROM historico WHERE ts < ?', (corte,))


# ------------------------------------------------------------------ auditoria / backup
def auditar(usuario, acao, detalhe=''):
    try:
        with conexao() as con:
            con.execute('INSERT INTO auditoria(ts, usuario, acao, detalhe) VALUES (?,?,?,?)',
                        (agora(), usuario or '-', acao, (detalhe or '')[:500]))
    except sqlite3.Error:
        pass


def listar_auditoria(limite=200):
    with conexao() as con:
        return [dict(r) for r in con.execute('SELECT ts, usuario, acao, detalhe FROM auditoria ORDER BY id DESC LIMIT ?', (limite,))]


def backup(destino):
    """Cópia consistente do banco (usa a API de backup do SQLite, segura com o sistema rodando)."""
    origem = sqlite3.connect(DB_PATH)
    try:
        dest = sqlite3.connect(destino)
        try:
            origem.backup(dest)
        finally:
            dest.close()
    finally:
        origem.close()

