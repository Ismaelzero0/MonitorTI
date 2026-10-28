"""Redefine a senha de um usuário (ou cria um administrador) direto no banco.

Precisa ser executado NO computador do servidor, por quem tem acesso à pasta do
sistema - é isso que prova que a pessoa pode recuperar o acesso.
Pode rodar com o monitor ligado.
"""
import getpass
import sys

import db

db.init()
usuarios = db.listar_usuarios()

print('=== Monitor TI - redefinir senha ===\n')
if usuarios:
    print('Usuários cadastrados:')
    for u in usuarios:
        print('  - %s (%s)%s' % (u['usuario'], u['papel'], '' if u['ativo'] else ' [desativado]'))
else:
    print('Nenhum usuário cadastrado ainda.')

padrao = next((u['usuario'] for u in usuarios if u['papel'] == 'admin'), 'admin')
nome = input('\nUsuário [%s]: ' % padrao).strip() or padrao
senha = getpass.getpass('Nova senha: ')
if senha != getpass.getpass('Repita a nova senha: '):
    sys.exit('As senhas não conferem. Nada foi alterado.')

existente = next((u for u in usuarios if u['usuario'] == nome), None)
try:
    if existente:
        db.alterar_usuario(existente['id'], senha=senha, ativo=True)
        print('\nSenha de "%s" redefinida (usuário reativado, se estava desativado).' % nome)
    else:
        if input('"%s" não existe. Criar como administrador? [s/N] ' % nome).strip().lower() != 's':
            sys.exit('Nada foi alterado.')
        db.criar_usuario(nome, senha, 'admin')
        print('\nAdministrador "%s" criado.' % nome)
    db.auditar('sistema', 'senha_redefinida_localmente', nome)
except ValueError as e:
    sys.exit('Erro: %s' % e)
