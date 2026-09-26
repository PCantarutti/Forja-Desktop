"""Trabalho autônomo (E16-C): a noite toda sem parar à toa, e nunca em silêncio.

Global nas configurações (`autonomo`) e ligado por conversa. Com ele ligado:
- o limite de passos vira checkpoint: com progresso recente segue sozinho até o orçamento;
- `ask_user` sem ninguém olhando anota a pergunta (ou escolhe a recomendada) e segue;
- no fim, ou ao estacionar, um relatório na conversa e no celular.
A escada de recuperação (níveis 3–5) vale com `recuperacao` ligada, autônomo ou não.
"""
from __future__ import annotations

import threading
import time
from collections import Counter
from dataclasses import dataclass, field

from . import config, db

PADRAO = {"recuperacao": True, "horas": 8.0, "passos": 2000, "ask_user": "anota", "notificar_nivel": 4}
ASK = ("anota", "recomendada")
CHAVE_CONVERSAS = "autonomo_conversas"


def opcoes() -> dict:
    return {**PADRAO, **(getattr(config, "AUTONOMO", None) or {})}


def valida(raw) -> dict:
    """Configuração vinda da tela: tipos e limites."""
    if not isinstance(raw, dict):
        raise ValueError("autonomo deve ser um objeto")
    v = {**PADRAO, **{k: raw[k] for k in PADRAO if k in raw}}
    v["recuperacao"] = bool(v["recuperacao"])
    v["horas"] = max(0.1, min(72.0, float(v["horas"])))
    v["passos"] = max(10, min(100_000, int(v["passos"])))
    if v["ask_user"] not in ASK:
        raise ValueError(f"autonomo.ask_user deve ser um de: {', '.join(ASK)}")
    v["notificar_nivel"] = max(2, min(5, int(v["notificar_nivel"])))
    return v


def _conversas() -> dict:
    with db.session() as s:
        linha = s.get(db.AppSetting, CHAVE_CONVERSAS)
        return dict(linha.value) if linha and isinstance(linha.value, dict) else {}


def ligado(conv_id: int) -> bool:
    return bool(_conversas().get(str(conv_id)))


def ligadas() -> list[int]:
    return sorted(int(k) for k, v in _conversas().items() if v)


def define(conv_id: int, valor: bool) -> None:
    atual = _conversas()
    if valor:
        atual[str(conv_id)] = True
    else:
        atual.pop(str(conv_id), None)
    with db.session() as s:
        if linha := s.get(db.AppSetting, CHAVE_CONVERSAS):
            linha.value = atual
        else:
            s.add(db.AppSetting(key=CHAVE_CONVERSAS, value=atual))
        s.commit()


@dataclass
class Sessao:
    """O que o relatório da manhã conta. Vive no Run."""
    conv_id: int
    ligado: bool = False
    t0: float = field(default_factory=time.monotonic)
    passos: int = 0
    niveis: Counter = field(default_factory=Counter)
    perguntas: list[str] = field(default_factory=list)
    estacionou: str = ""

    def estourou(self) -> str:
        """Motivo, se o orçamento acabou ('' se não)."""
        o = opcoes()
        if (time.monotonic() - self.t0) / 3600 >= o["horas"]:
            return f"orçamento de {o['horas']:g} h esgotado"
        if self.passos >= o["passos"]:
            return f"orçamento de {o['passos']} passos esgotado"
        return ""

    def relatorio(self, feitas: list[str], pendentes: list[str]) -> str:
        minutos = round((time.monotonic() - self.t0) / 60)
        n = self.niveis
        linhas = [f"Relatório do trabalho autônomo — {minutos} min, {self.passos} passos."]
        linhas.append(f"Feito: {', '.join(feitas) if feitas else 'nenhuma tarefa concluída'}.")
        linhas.append(f"Recuperação: {n[2]} intervenção(ões), {n[3]} contexto(s) limpo(s), {n[4]} recuo(s), "
                      f"{n[5]} estacionamento(s).")
        if self.estacionou:
            linhas.append(f"Travou: {self.estacionou}.")
        if pendentes:
            linhas.append(f"Precisa de você: {', '.join(pendentes)}.")
        if self.perguntas:
            linhas.append("Perguntas anotadas:\n" + "\n".join(f"- {p}" for p in self.perguntas))
        return "\n".join(linhas)


def avisa_celular(titulo: str, texto: str, conv_id: int) -> None:
    """Push sem prender o loop (o httpx do mobile.avisa é síncrono)."""
    from . import mobile
    threading.Thread(target=mobile.avisa, args=(titulo, texto, conv_id), daemon=True).start()
