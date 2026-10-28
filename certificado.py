"""Certificado HTTPS autoassinado, gerado automaticamente na primeira vez (MONITOR_HTTPS=1).

Fica em data/certificado.pem + data/certificado-chave.pem. É renovado sozinho quando
vence ou quando o nome/IP do servidor muda. Para o navegador deixar de avisar, instale
data/certificado.pem como "Autoridade de certificação raiz confiável" nos PCs
(ou use um certificado da empresa: coloque os dois arquivos com esses nomes na pasta data).
"""
import datetime
import ipaddress
import os
import socket

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import config

CERT = os.path.join(config.DATA_DIR, 'certificado.pem')
CHAVE = os.path.join(config.DATA_DIR, 'certificado-chave.pem')


def _nomes_e_ips():
    nomes = {'localhost', socket.gethostname()}
    ips = {'127.0.0.1'}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:
        nomes.add(socket.getfqdn())
    except OSError:
        pass
    return {n for n in nomes if n}, ips


def _valido(nomes, ips):
    try:
        cert = x509.load_pem_x509_certificate(open(CERT, 'rb').read())
        if cert.not_valid_after_utc - datetime.datetime.now(datetime.timezone.utc) < datetime.timedelta(days=30):
            return False
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        tem_nomes = set(san.get_values_for_type(x509.DNSName))
        tem_ips = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
        return nomes <= tem_nomes and ips <= tem_ips
    except Exception:
        return False


def garantir():
    """Retorna (arquivo_cert, arquivo_chave), criando/renovando se preciso."""
    nomes, ips = _nomes_e_ips()
    if os.path.exists(CERT) and os.path.exists(CHAVE) and _valido(nomes, ips):
        return CERT, CHAVE
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sujeito = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Monitor TI')])
    agora = datetime.datetime.now(datetime.timezone.utc)
    san = [x509.DNSName(n) for n in sorted(nomes)] + [x509.IPAddress(ipaddress.ip_address(i)) for i in sorted(ips)]
    cert = (x509.CertificateBuilder().subject_name(sujeito).issuer_name(sujeito).public_key(chave.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(agora - datetime.timedelta(days=1))
            .not_valid_after(agora + datetime.timedelta(days=825))
            .add_extension(x509.SubjectAlternativeName(san), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(chave, hashes.SHA256()))
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(CHAVE, 'wb') as f:
        f.write(chave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                    serialization.NoEncryption()))
    with open(CERT, 'wb') as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    return CERT, CHAVE
