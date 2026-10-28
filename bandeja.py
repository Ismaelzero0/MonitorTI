# -*- coding: utf-8 -*-
"""Monitor TI na bandeja do Windows: inicia o servidor em segundo plano e oferece um menu
(Abrir painel / Reiniciar / Ver log / Sair). Os dados e o log ficam na pasta data/."""

import os
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

import pystray
from PIL import Image, ImageDraw

from config import BASE_DIR, DATA_DIR, env_bool, env_int

PORTA = env_int('MONITOR_PORT', 5050)
ESQUEMA = 'https' if env_bool('MONITOR_HTTPS', False) else 'http'
LOG = os.path.join(DATA_DIR, 'monitor.log')
_proc = None
_lock = threading.Lock()


def _icone(cor):
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=cor)
    d.text((19, 22), 'TI', fill='white')
    return img


def _servidor_no_ar():
    try:
        ctx = ssl._create_unverified_context() if ESQUEMA == 'https' else None   # só consulta o próprio servidor
        urllib.request.urlopen(f'{ESQUEMA}://127.0.0.1:{PORTA}/login', timeout=2, context=ctx).close()
        return True
    except Exception:
        return False


def iniciar_servidor():
    global _proc
    with _lock:
        if _proc and _proc.poll() is None:
            return
        try:
            if os.path.exists(LOG) and os.path.getsize(LOG) > 5 * 1024 * 1024:   # rotação simples do log
                os.replace(LOG, LOG + '.old')
        except OSError:
            pass
        saida = open(LOG, 'ab')
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        env = dict(os.environ, OPEN_BROWSER='0')
        _proc = subprocess.Popen([sys.executable, os.path.join(BASE_DIR, 'app.py')], cwd=BASE_DIR,
                                 stdout=saida, stderr=saida, creationflags=flags, env=env)


def parar_servidor():
    global _proc
    with _lock:
        if _proc and _proc.poll() is None:
            _proc.terminate()
            try:
                _proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                _proc.kill()
        _proc = None


def abrir(icon=None, item=None):
    webbrowser.open(f'{ESQUEMA}://127.0.0.1:{PORTA}')


def reiniciar(icon, item):
    parar_servidor()
    iniciar_servidor()


def ver_log(icon, item):
    if os.path.exists(LOG):
        os.startfile(LOG)


def sair(icon, item):
    parar_servidor()
    icon.stop()


def vigiar(icon):
    """Reinicia o servidor se cair e atualiza a cor do ícone (verde = no ar)."""
    while True:
        with _lock:
            morreu = _proc is not None and _proc.poll() is not None
        if morreu:
            iniciar_servidor()
        no_ar = _servidor_no_ar()
        icon.icon = _icone('#16a34a' if no_ar else '#d97706')
        icon.title = f'Monitor TI — {"no ar" if no_ar else "iniciando..."} (porta {PORTA})'
        time.sleep(5)


def main():
    iniciar_servidor()
    menu = pystray.Menu(
        pystray.MenuItem('Abrir painel', abrir, default=True),
        pystray.MenuItem('Reiniciar servidor', reiniciar),
        pystray.MenuItem('Ver log', ver_log),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Sair', sair),
    )
    icon = pystray.Icon('MonitorTI', _icone('#d97706'), 'Monitor TI — iniciando...', menu)
    threading.Thread(target=vigiar, args=(icon,), daemon=True).start()
    if env_bool('OPEN_BROWSER', True):
        threading.Timer(3, abrir).start()   # abre o painel ao iniciar
    icon.run()


if __name__ == '__main__':
    main()
