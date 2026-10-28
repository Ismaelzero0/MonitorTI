# Monitor TI

Painel web para acompanhar **computadores, impressoras e painéis de TV** de uma ou várias unidades, com **ferramentas de gestão remota** para o time de TI. Roda sozinho (Flask + SQLite): sem nuvem e sem serviços externos. Você instala, abre no navegador e vai preenchendo tudo pelo próprio sistema.

> **Windows 10 e 11 apenas** (usa WMI, PAExec e APIs do Windows). Python 3.10 ou superior (testado com 3.12).

---

## Sumário

1. [O que o sistema faz](#1-o-que-o-sistema-faz)
2. [Como funciona por dentro](#2-como-funciona-por-dentro)
3. [Instalação](#3-instalação)
4. [Primeiro acesso (assistente)](#4-primeiro-acesso-assistente)
5. [Usando o sistema](#5-usando-o-sistema)
6. [Usuários e permissões](#6-usuários-e-permissões)
7. [Gestão remota e a conta de rede de cada pessoa](#7-gestão-remota-e-a-conta-de-rede-de-cada-pessoa)
8. [Acesso pela rede, HTTPS e celular](#8-acesso-pela-rede-https-e-celular)
9. [Início automático com o Windows](#9-início-automático-com-o-windows)
10. [Backup e senha perdida](#10-backup-e-senha-perdida)
11. [Segurança](#11-segurança)
12. [Configurações (.env)](#12-configurações-env)
13. [Painéis de TV (Raspberry Pi)](#13-painéis-de-tv-raspberry-pi)
14. [Ferramentas de terceiros](#14-ferramentas-de-terceiros)
15. [Problemas comuns](#15-problemas-comuns)
16. [Estrutura dos arquivos](#16-estrutura-dos-arquivos)
17. [Licença e créditos](#17-licença-e-créditos)

---

## 1. O que o sistema faz

| Área | O que oferece |
|---|---|
| **Inventário** | Unidades → andares → equipamentos (PC, impressora, TV, outro). Cadastro e edição pela tela, importação/exportação em CSV e scanner de rede que encontra equipamentos e importa direto. |
| **Status em tempo real** | Ping contínuo de todos os equipamentos. Contadores de PCs e impressoras online/offline por unidade e na visão geral. |
| **Detalhes da máquina** | Via WMI: modelo, CPU, RAM, disco, monitores e usuário logado. |
| **Histórico** | Registra quando cada equipamento caiu ou voltou e calcula a disponibilidade dos últimos 7 dias. |
| **Anotações** | Notas por equipamento (“trocou a fonte”, “monitor com defeito”), com autor e data. |
| **Gestão remota** | Reiniciar/desligar, mensagem na tela do usuário, listar/instalar/remover programas e impressoras, limpar perfis, instalar acesso remoto e outros (veja a seção 7). |
| **Painéis de TV** | Controle de TVs com Raspberry Pi por SSH: reiniciar navegador, modo quiosque, limpar cache, áudio e outros. |
| **Usuários e papéis** | Administrador, técnico e leitor, com registro de ações (auditoria). |
| **Celular** | Layout adaptado: menu retrátil, cartões empilhados e tabelas rolando na horizontal. |
| **Sobre / Equipe** | Janela com os créditos dos criadores e a equipe escolhida pelo administrador entre as contas existentes. |

---

## 2. Como funciona por dentro

```
 Navegador ──HTTP(S)──►  Flask (app.py) ──►  SQLite (data/monitor.db)
                           │
                           ├─ Thread de monitoramento: ping em todos os IPs (100 em paralelo)
                           │     └► STATUS_REALTIME (memória) ─► histórico (só grava mudanças)
                           │
                           ├─ Rotas /api/*: inventário, usuários, notas, scanner, painéis…
                           │
                           └─ Ações remotas ─► WMI / ADMIN$ / PAExec  (com a conta de QUEM clicou)
```

**Monitoramento.** Uma thread em segundo plano lê todos os hosts do inventário e dá um ping em cada um (100 ao mesmo tempo). O resultado fica na memória; a tela consulta `/api/check/<host>` e mostra ONLINE/OFFLINE. Só há resposta real quando o ping devolve `TTL=` (no Windows o código de saída 0 não basta). Os nomes de máquinas são resolvidos por DNS (com o sufixo configurado) e guardados em cache por 5 minutos (1 minuto para os que não resolvem), para o ciclo ficar rápido mesmo com centenas de equipamentos offline.

**Histórico de disponibilidade.** A cada ciclo o sistema compara o estado atual com o último gravado e **grava apenas as mudanças**. Assim o banco cresce pouco e dá para calcular quanto tempo cada equipamento ficou no ar. Registros antigos são limpos automaticamente.

**Banco de dados.** SQLite em modo WAL, com uma conexão por chamada. Tabelas: unidades, andares, equipamentos, painéis, usuários, notas, histórico, configurações e auditoria. Segredos guardados (como a senha SSH dos painéis) são criptografados com Fernet; a chave fica em `data/chave.key`.

**Login e sessões.** Senhas com hash; sessão por cookie assinado (8 horas); limite de tentativas de login erradas; páginas e APIs protegidas por papel.

**Ações remotas.** Nunca usam uma conta fixa. Cada pessoa informa a própria conta de rede, o sistema valida que ela é administradora e só então executa (seção 7).

---

## 3. Instalação

**Automática (recomendada):** clique com o botão direito em `instalar.ps1` → *Executar com o PowerShell* (como administrador) → escolha **[1] Automático**. Ele instala o Python (conferindo a assinatura digital), as bibliotecas e oferece iniciar com o Windows.

**Manual:** instale o Python (marque *Add python.exe to PATH*), execute `instalar_dependencias.bat` e depois `iniciar.bat`.

Para iniciar depois: `iniciar.bat` (janela visível) ou `bandeja.bat` (ícone na bandeja do Windows).

---

## 4. Primeiro acesso (assistente)

Abra `http://127.0.0.1:5050/setup` **no próprio computador do servidor** (por segurança, enquanto não existe administrador a configuração só é aceita a partir dele). O assistente guia cada etapa e **testa antes de seguir** (✅ funcionou / ❌ falhou); as etapas opcionais podem ser puladas e refeitas depois em *Administração*:

1. **Administrador:** usuário e senha (qualquer senha não vazia) e escolha se o painel exige login para ser visto.
2. **Requisitos:** Python, bibliotecas, PAExec (instalação automática com verificação de assinatura, ou manual) e DelProf2 (manual).
3. **Rede:** sufixo DNS e busca de equipamentos na rede, importando direto para o inventário.
4. **Acesso remoto:** teste de que a **sua** conta de rede é administradora (nada é salvo).
5. **Painéis de TV:** teste do SSH e cadastro do painel.
6. **Concluir.**

---

## 5. Usando o sistema

- **Tela inicial:** botão *Entrar no sistema*. Para administradores, a janela **Conta de rede** abre logo em seguida (seção 7).
- **Início / Visão geral:** totais de PCs e impressoras online/offline e situação dos painéis de TV.
- **Unidades:** cada unidade tem seus andares e equipamentos, com status, IP, disco e detalhes; busca por texto e botão de notas/histórico.
- **Ferramentas de Gestão:** escolha uma máquina e rode uma ação. Cada execução ganha o próprio bloco de resultado (com a máquina no título), então várias ações podem rodar ao mesmo tempo sem se misturar.
- **Scanner de Rede:** informa uma faixa privada (ex.: `10.0.1.0/24`, no máximo /23), lista o que respondeu e importa para o inventário.
- **VNC Monitor:** verifica se a porta de acesso remoto responde em uma lista de IPs da rede interna.
- **Administração** (`/admin`, só administrador): usuários, **edição completa do inventário** (unidades, andares e equipamentos: adicionar, editar, mover, excluir, CSV), configurações, registro de ações (auditoria) e backup.
- **Menu lateral:** usuário e papel, botões de Administração e Sobre, estado da conta de rede e créditos.
- **Sobre:** versão, o que o sistema faz, **Criadores** (fixos) e **Equipe**. O administrador clica em *Escolher* e marca, entre as contas existentes, quem deve aparecer; contas excluídas ou desativadas somem da lista sozinhas.
- **Atalhos:** `Esc` fecha a janela aberta.

### Trocar a imagem de fundo da tela inicial

A tela com o botão *Entrar no sistema* pode ter uma foto de fundo (escurecida automaticamente para o texto continuar legível). A imagem **não vem no repositório**: ela fica na pasta `imagens/`, ao lado do `app.py`, que é de uso local e não é enviada ao git.

1. Crie a pasta `imagens` ao lado do `app.py` (se ainda não existir).
2. Coloque lá **uma** imagem `.jpg`, `.jpeg` ou `.png`. Se houver mais de uma, o sistema usa a primeira que encontrar, então deixe só a que você quer.
3. Atualize a página com **Ctrl + F5** (para o navegador não usar a imagem antiga guardada). Não precisa reiniciar o servidor.

Para **trocar**, apague a imagem antiga e coloque a nova. Para **remover**, esvazie a pasta: a tela volta a usar só o fundo escuro. Dica: use uma imagem horizontal, de preferência 1920×1080 ou maior, e de tom suave, porque ela é esticada para preencher a tela.

---

## 6. Usuários e permissões

| Papel | Pode |
|---|---|
| **Administrador** | Tudo: inventário, ações remotas, usuários, configurações, auditoria e backup. |
| **Técnico** | Ver o painel, criar e excluir anotações e ler os IDs de acesso remoto (este com a própria conta de rede). |
| **Leitor** | Somente ver. |

O sistema impede excluir ou rebaixar o **último administrador**. Todas as ações importantes ficam no *Registro*, com usuário, data e detalhe.

---

## 7. Gestão remota e a conta de rede de cada pessoa

O sistema **não guarda nenhuma conta de administrador de rede**. Quem vai usar a gestão remota entra com a **própria** conta, e as ações são feitas e registradas em nome dela.

### Como funciona

1. Ao entrar no sistema (ou ao tentar uma ação remota sem conta válida), abre a janela **Conta de rede** pedindo só **usuário e senha** (`DOMINIO\usuario`).
2. O sistema valida **sozinho e de forma obrigatória**, em duas etapas:
   - **Credenciais:** confere usuário e senha no Windows. Se a senha estiver errada, **para aí** e não toca em nenhuma máquina (evita bloquear a conta de domínio).
   - **Prova de administrador:** tenta abrir, com essa conta, o compartilhamento `ADMIN$` de alguns PCs **ligados do inventário** (escolhidos automaticamente, nunca o próprio servidor). Só administradores conseguem abrir `ADMIN$`; é uma leitura, nada é alterado.
3. Se passar, aparece o ✅ e a conta fica ativa para a sessão. Se não, a gestão remota continua bloqueada.
4. Fechar a janela sem validar **não libera** nada: as ações remotas respondem “conta de rede necessária”.

> Sem PCs ligados no inventário não há onde provar a permissão, então a validação falha. Nunca é aprovada “por padrão”.

### Garantias

- A senha fica **só na memória do servidor**, ligada à sua sessão (o cookie guarda apenas um identificador aleatório). Nunca vai para o banco, log, backup ou tela.
- Vale enquanto a sessão de login durar (8 h sem uso), até você sair, ou até o servidor reiniciar. Sair do sistema também apaga a conta.
- Duas pessoas ao mesmo tempo **não compartilham** credenciais. Arquivos e registro remotos usam uma sessão de logon separada por pessoa; WMI e PAExec recebem a credencial explicitamente.
- O Windows das máquinas registra **quem** fez cada ação, e o *Registro* do sistema anota usuário, máquina e conta usada (nunca a senha).

### Ações disponíveis

Reiniciar e desligar; mensagem na tela do usuário; listar programas, impressoras e disco; instalar/remover programas e impressoras; instalar AnyDesk e RustDesk; atualizar Chrome e corrigir o idioma; limpar perfis de usuário (DelProf2); ler IDs de acesso remoto. O PAExec recebe `-u/-p` por linha de comando: rode o monitor em um servidor restrito.

---

## 8. Acesso pela rede, HTTPS e celular

Por padrão o servidor escuta só em `127.0.0.1`. Para outras pessoas usarem pelo navegador defina no `.env`:

```ini
MONITOR_HOST=0.0.0.0
MONITOR_PORT=5000
MONITOR_HTTPS=1
```

- **Sem HTTPS**, a senha de login e a conta de rede passam sem criptografia pela rede. Use só em rede confiável ou VPN.
- **Com `MONITOR_HTTPS=1`** o sistema cria sozinho um certificado em `data/certificado.pem` (renovado quando vence ou quando o nome/IP do servidor muda) e o cookie de sessão passa a ser seguro. Quem abrir `http://` na mesma porta é redirecionado para `https://`.
- O certificado é **autoassinado**, então o navegador avisa na primeira vez (“Avançado → continuar”). Para o aviso sumir, instale `data/certificado.pem` nos PCs como autoridade raiz confiável, ou troque `certificado.pem` e `certificado-chave.pem` em `data/` por um certificado da sua empresa.
- Se o navegador guardou um redirecionamento antigo para `https://` e você voltou para HTTP, limpe o cache do navegador.
- O **Firewall do Windows** do servidor precisa liberar a porta. O `autostart.ps1` cria a regra para a sub-rede local.
- No **celular** basta abrir o mesmo endereço no navegador, na mesma rede.

---

## 9. Início automático com o Windows

```powershell
.\autostart.ps1 -Acao Instalar -Tipo Tarefa    # tarefa agendada: inicia ao ligar o PC e reinicia se cair
.\autostart.ps1 -Acao Instalar -Tipo Bandeja   # ícone na bandeja ao entrar no Windows
.\autostart.ps1 -Acao Remover
```

`bandeja.bat` abre o programa de bandeja (abrir painel, reiniciar, ver log, sair). O log fica em `data\monitor.log`. O `iniciar.bat` encerra apenas o processo que está usando a porta configurada.

---

## 10. Backup e senha perdida

- **Backup:** *Administração → Backup* baixa uma cópia consistente do banco. **Guarde também `data/chave.key`**: sem ela as senhas salvas (como a do SSH dos painéis) não podem ser lidas.
- **Esqueci a senha:** no computador do servidor, dê dois cliques em `redefinir_senha.bat`, escolha o usuário (ou crie um novo administrador) e digite a nova senha. Funciona com o sistema ligado e exige acesso à pasta do sistema, então só quem administra o servidor consegue. Fica registrado na auditoria.
- **Levar os dados para outro computador:** copie a pasta `data/` inteira (banco e `chave.key`) para o novo servidor, ou use a importação por CSV em *Administração → Inventário*.

---

## 11. Segurança

- **Autenticação:** senhas com hash; limite de tentativas; sessão de 8 h; ações perigosas só para administrador.
- **CSRF:** pedidos que alteram dados são recusados se o navegador indicar uma origem diferente do sistema; o cookie usa `SameSite=Lax` e `HttpOnly`.
- **Cabeçalhos:** `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`; respostas da API sem cache.
- **Banco:** consultas sempre parametrizadas; nenhum comando é montado como texto de shell (só listas de argumentos).
- **Segredos:** senhas guardadas ficam criptografadas e nunca são exibidas; a senha de rede nunca é gravada.
- **Rede:** o scanner só aceita faixas privadas IPv4; o SSH só conecta em painéis cadastrados e valida a chave do host (`data/known_hosts`, primeira conexão confiada e fixada).
- **Primeiro acesso:** a configuração inicial só é aceita pelo próprio servidor.
- **Download de ferramentas:** o PAExec é baixado com verificação da assinatura digital do fabricante.
- **Recomendação:** rode em servidor restrito, com HTTPS ou VPN. O servidor embutido do Flask é o de desenvolvimento; para uso contínuo considere o `waitress`.

---

## 12. Configurações (.env)

Quase tudo é configurado pelo próprio sistema. O arquivo `.env` (opcional, veja `.env.example`) só ajusta o servidor:

| Variável | Padrão | Função |
|---|---|---|
| `MONITOR_HOST` | `127.0.0.1` | Endereço de escuta (`0.0.0.0` libera a rede) |
| `MONITOR_PORT` | `5050` | Porta |
| `MONITOR_HTTPS` | `0` | `1` liga HTTPS com certificado autoassinado |
| `OPEN_BROWSER` | `1` | Abrir o navegador ao iniciar |
| `MONITOR_DATA_DIR` | `./data` | Pasta do banco, chave e certificado |
| `SESSION_COOKIE_SECURE` | segue o HTTPS | Forçar cookie só por HTTPS |

---

## 13. Painéis de TV (Raspberry Pi)

Cadastre cada painel (IP) em *Painéis TV*. Os comandos são enviados por SSH, e o sistema só conecta em IPs cadastrados. Eles esperam um `~/painel.sh` no Pi (com uma linha `URL=...`) que abre o navegador em modo quiosque, além de `wpctl`/`pw-play` para áudio. Disponível: reiniciar navegador, ativar quiosque, limpar cache, trocar URL, controlar áudio e executar comando. A senha SSH fica criptografada.

---

## 14. Ferramentas de terceiros

Não fazem parte do repositório (licenças próprias):

| Arquivo | Para quê | Instalação |
|---|---|---|
| `tools\paexec.exe` | Execução remota | Automática pelo assistente (download de https://www.poweradmin.com/paexec/ com assinatura verificada) ou manual |
| `tools\DelProf2.exe` | Limpeza de perfis | Manual: <https://helgeklein.com/free-tools/delprof2-user-profile-deletion-tool/> |

---

## 15. Problemas comuns

| Sintoma | O que fazer |
|---|---|
| Atualização do status lenta | Verifique o DNS/sufixo em *Configurações*; nomes que não resolvem demoram até o cache (1 min). |
| “Conta de rede necessária” | Entre com a sua conta na janela *Conta de rede*; precisa de ao menos um PC ligado no inventário. |
| Validação falha com a conta certa | A conta precisa ser administradora local das máquinas; confira firewall (SMB 445, RPC/WMI 135) e se o PC de teste está ligado. |
| “Sua conexão não é particular” | Esperado com certificado autoassinado; avance ou instale o certificado (seção 8). |
| Outros PCs não acessam | `MONITOR_HOST=0.0.0.0` e porta liberada no firewall. |
| Perdi a senha do administrador | `redefinir_senha.bat` no servidor. |

---

## 16. Estrutura dos arquivos

| Arquivo | Função |
|---|---|
| `app.py` | Aplicação: rotas, login, assistente, monitoramento, scanner, painéis |
| `db.py` | Banco SQLite: inventário, usuários, notas, histórico, configurações criptografadas, backup |
| `credenciais.py` | Conta de rede por sessão (memória), impersonação, WMI e PAExec com a credencial da pessoa |
| `ferramentas_sistema.py` | Gestão remota (`/api/sistema/*`, só administrador) |
| `requisitos.py` | Verificação de requisitos e download verificado de ferramentas |
| `certificado.py` | Certificado HTTPS autoassinado automático |
| `bandeja.py` | Programa de bandeja |
| `redefinir_senha.py` / `.bat` | Recuperação de senha no servidor |
| `config.py` | Caminhos e leitura do `.env` |
| `templates/` | Telas: principal, login, administração e assistente |
| `instalar.ps1`, `autostart.ps1`, `*.bat` | Instalação e inicialização |
| `wts_send.ps1` | Janela de mensagem exibida na sessão do usuário |

**Dados locais (não versionados):** `data/` (banco, chave, certificado, log), `tools/`, `imagens/` (imagem de fundo opcional da tela inicial: coloque um `.jpg`/`.png` na pasta) e `.env`.

---

## 17. Licença e créditos

Criado por **Ismael Silva, Fabio Lira e Matthews Henrique**.

Distribuído sob a **licença MIT** (veja o arquivo [`LICENSE`](LICENSE)): você pode usar, copiar, modificar e redistribuir, inclusive em projetos comerciais, desde que mantenha o aviso de copyright e a licença. O software é fornecido “como está”, sem garantias.

As ferramentas de terceiros (PAExec e DelProf2) **não** fazem parte do repositório nem desta licença: cada uma tem a licença do próprio fabricante (seção 14). Este projeto também usa bibliotecas de código aberto listadas em `requirements.txt`, cada uma com a sua licença.
