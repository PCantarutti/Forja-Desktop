"""Segredo em repouso (chave de API, token do Hugging Face, token do celular) cifrado com o DPAPI do
Windows, preso à conta do usuário — o mesmo cofre que o `safeStorage` do Electron usa no Windows. Fora
do Windows (Docker, testes em Linux) não há cofre do sistema: o valor fica como está.

O valor cifrado leva o prefixo `dpapi:`; o que chega sem ele é texto puro de uma versão anterior, e é
lido normalmente (a próxima gravação já cifra).
"""
from __future__ import annotations

import base64
import ctypes
import sys

PREFIXO = "dpapi:"


if sys.platform == "win32":
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.windll.crypt32
    _kernel32 = ctypes.windll.kernel32

    def _dpapi(dados: bytes, cifrar: bool) -> bytes:
        entrada = _Blob(len(dados), ctypes.cast(ctypes.create_string_buffer(dados, len(dados)),
                                                ctypes.POINTER(ctypes.c_char)))
        saida = _Blob()
        fn = _crypt32.CryptProtectData if cifrar else _crypt32.CryptUnprotectData
        # 0x1 = CRYPTPROTECT_UI_FORBIDDEN: nunca abrir diálogo (o backend não tem janela)
        if not fn(ctypes.byref(entrada), None, None, None, None, 0x1, ctypes.byref(saida)):
            raise OSError("DPAPI recusou o segredo")
        try:
            return ctypes.string_at(saida.pbData, saida.cbData)
        finally:
            _kernel32.LocalFree(saida.pbData)
else:
    _dpapi = None


def cifrar(texto: str) -> str:
    if not texto or texto.startswith(PREFIXO) or _dpapi is None:
        return texto
    return PREFIXO + base64.b64encode(_dpapi(texto.encode("utf-8"), True)).decode()


def decifrar(texto: str) -> str:
    if not texto or not texto.startswith(PREFIXO):
        return texto or ""
    if _dpapi is None:
        return ""  # cifrado no Windows e lido em outro sistema: não há como abrir
    try:
        return _dpapi(base64.b64decode(texto[len(PREFIXO):]), False).decode("utf-8")
    except (OSError, ValueError):
        return ""  # outra conta do Windows (ou perfil restaurado em outra máquina): a chave precisa ser digitada de novo
