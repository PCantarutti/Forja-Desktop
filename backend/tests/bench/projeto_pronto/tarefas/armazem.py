from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from typing import Any

from .modelo import Tarefa, PRIORIDADES


class Armazem:
    """Persistência de Tarefas em JSON com operações CRUD."""

    PRIORIDADE_ORD = {"alta": 0, "media": 1, "baixa": 2}

    def __init__(self, caminho: str) -> None:
        self.caminho = caminho
        if os.path.exists(caminho):
            with open(caminho, "r", encoding="utf-8") as f:
                data: list[dict[str, Any]] = json.load(f)
            self._tarefas: list[Tarefa] = [Tarefa.de_dict(d) for d in data]
        else:
            self._tarefas = []

    def _salvar(self) -> None:
        """Grava as tarefas no arquivo de forma atômica (tempfile + replace)."""
        dirn = os.path.dirname(self.caminho) or "."
        fd, tmp_path = tempfile.mkstemp(dir=dirn, suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump([t.para_dict() for t in self._tarefas], f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self.caminho)
        except BaseException:
            os.unlink(tmp_path)
            raise

    # --- CRUD ---

    def adicionar(
        self,
        titulo: str,
        prioridade: str = "media",
        prazo: date | None = None,
        tags: list[str] | None = None,
    ) -> Tarefa:
        max_id = (max(t.id for t in self._tarefas) if self._tarefas else 0)
        tarefa = Tarefa(
            id=max_id + 1,
            titulo=titulo,
            prioridade=prioridade,
            prazo=prazo,
            tags=list(tags) if tags else [],
        )
        self._tarefas.append(tarefa)
        self._salvar()
        return tarefa

    def obter(self, id: int) -> Tarefa:
        for t in self._tarefas:
            if t.id == id:
                return t
        raise KeyError(id)

    def concluir(self, id: int) -> Tarefa:
        for t in self._tarefas:
            if t.id == id:
                t.feita = True
                self._salvar()
                return t
        raise KeyError(id)

    def remover(self, id: int) -> None:
        for i, t in enumerate(self._tarefas):
            if t.id == id:
                self._tarefas.pop(i)
                self._salvar()
                return
        raise KeyError(id)

    # --- Consultas ---

    def listar(
        self,
        feita: bool | None = None,
        prioridade: str | None = None,
        tag: str | None = None,
    ) -> list[Tarefa]:
        resultado = self._tarefas

        if feita is not None:
            resultado = [t for t in resultado if t.feita == feita]
        if prioridade is not None:
            resultado = [t for t in resultado if t.prioridade == prioridade]
        if tag is not None:
            resultado = [t for t in resultado if tag in t.tags]

        # Ordena: prioridade (alta→media→baixa), prazo (sem prazo por último), id
        def _chave(t: Tarefa):
            prio_ord = self.PRIORIDADE_ORD.get(t.prioridade, 99)
            prazo_ord = (0, t.prazo.isoformat()) if t.prazo else (1, "")
            return (prio_ord, prazo_ord, t.id)

        return sorted(resultado, key=_chave)

    def atrasadas(self, hoje: date) -> list[Tarefa]:
        """Retorna tarefas pendentes com prazo < hoje."""
        return [t for t in self._tarefas if not t.feita and t.prazo is not None and t.prazo < hoje]
